# -*- coding: utf-8 -*-
"""
Revit Scanner — Model Health & Clash Audit (v2)
Target: pyRevit IronPython 2.7-compatible syntax, Revit API (verify against installed Revit version)
Purpose: audit the active Revit model and export JSON history, CSV clashes,
BCF 2.1 viewpoints, standards checks, health score, and prioritized fixes.

IMPORTANT:
- This is the audit/report module for the generator, not a geometry generator.
- Test on a COPY of your RVT first. It reads the model; it does not modify it.
- Configure rules in audit_config.json in the generated report folder after the first run.
"""


import csv
import json
import math
import os
import re
import sys
import traceback
import uuid
import zipfile
from datetime import datetime
import io

def makedirs_compat(path):
    if not os.path.isdir(path):
        try:
            os.makedirs(path)
        except OSError:
            if not os.path.isdir(path):
                raise

def open_text_compat(path, mode):
    # IronPython 2.7-compatible UTF-8 text file helper.
    return io.open(path, mode, encoding="utf-8")


from pyrevit import revit, forms, script
from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, ElementId, FilteredElementCollector,
    FamilyInstance, IndependentTag, Level, RevitLinkInstance, View3D,
    ViewType, XYZ, BoundingBoxXYZ, WorksetKind, WorksetId,
    WorksetTable, WorksetVisibility, UnitUtils
)

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

SCRIPT_DIR = os.path.dirname(__file__)
REPORT_DIR = os.path.join(os.path.expanduser("~"), "Documents", "RevitScanner", "reports")
CONFIG_PATH = os.path.join(REPORT_DIR, "audit_config.json")
makedirs_compat(REPORT_DIR)

DEFAULT_CONFIG = {
    "hard_clash_categories": [
        ["OST_Walls", "OST_Walls"],
        ["OST_Walls", "OST_Floors"],
        ["OST_Walls", "OST_StructuralFraming"],
        ["OST_Walls", "OST_StructuralColumns"],
        ["OST_Floors", "OST_StructuralFraming"],
        ["OST_Floors", "OST_StructuralColumns"],
        ["OST_Doors", "OST_Walls"],
        ["OST_Windows", "OST_Walls"],
        ["OST_DuctCurves", "OST_PipeCurves"],
        ["OST_DuctCurves", "OST_CableTray"],
        ["OST_PipeCurves", "OST_CableTray"]
    ],
    "clearance_tolerances_mm": {
        "OST_DuctCurves|OST_Walls": 25,
        "OST_PipeCurves|OST_Walls": 25,
        "OST_DuctCurves|OST_StructuralFraming": 50,
        "OST_PipeCurves|OST_StructuralFraming": 50,
        "OST_CableTray|OST_Walls": 25
    },
    "ignore_hosted_elements": True,
    "ignore_nested_or_shared_family_components": True,
    "ignore_same_group": True,
    "ignore_same_assembly": True,
    "duplicate_location_tolerance_mm": 10,
    "family_size_threshold_mb": 20,
    "required_parameters": {
        "OST_Doors": ["Mark", "Type Mark", "Level"],
        "OST_Windows": ["Mark", "Type Mark", "Level"],
        "OST_Walls": ["Type", "Base Constraint", "Top Constraint"],
        "OST_Floors": ["Type", "Level"],
        "OST_StructuralColumns": ["Mark", "Base Level", "Top Level"],
        "OST_StructuralFraming": ["Mark", "Reference Level"]
    },
    "naming_regex": {
        "views": r"^(A|S|M|E|P|BIM)[-_ ].+",
        "sheets": r"^[A-Z]{1,3}[-.]?\d{2,4}.*",
        "levels": r"^(Level|L|Roof|Ground).+",
        "grids": r"^[A-Z0-9]+$",
        "worksets": r"^(AR|ST|MEP|EL|PL|Shared|Links)[-_ ].+"
    },
    "health_weights": {
        "hard_clash": 8,
        "clearance_clash": 3,
        "critical_revit_warning": 5,
        "high_revit_warning": 3,
        "medium_revit_warning": 1,
        "missing_parameter": 1,
        "naming_issue": 1,
        "workset_issue": 2,
        "duplicate": 3,
        "link_coordination": 4,
        "performance_flag": 1
    }
}


def now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def mm_to_internal(mm):
    # Revit internal length units are feet.
    return float(mm) / 304.8


def internal_to_mm(value):
    return float(value) * 304.8


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "unnamed"))[:80]


def element_id_value(element_id):
    try:
        return int(element_id.Value)  # Revit 2024+
    except Exception:
        try:
            return int(element_id.IntegerValue)
        except Exception:
            return -1


def category_key(element):
    try:
        return element.Category.Name if element.Category else "No Category"
    except Exception:
        return "No Category"


def category_bic_name(element):
    try:
        bic = element.Category.BuiltInCategory
        return str(bic).replace("BuiltInCategory.", "")
    except Exception:
        return ""


def get_param_text(element, name):
    # First try built-in/visible parameter name.
    try:
        p = element.LookupParameter(name)
        if p is None:
            return ""
        if p.StorageType.ToString() == "String":
            return p.AsString() or ""
        value = p.AsValueString()
        if value:
            return value
        if p.StorageType.ToString() == "ElementId":
            eid = p.AsElementId()
            if eid and element_id_value(eid) >= 0:
                ref = doc.GetElement(eid)
                return ref.Name if ref else str(element_id_value(eid))
        return str(p.AsDouble()) if p.StorageType.ToString() == "Double" else str(p.AsInteger())
    except Exception:
        return ""


def get_param(element, name):
    try:
        return element.LookupParameter(name)
    except Exception:
        return None


def get_bbox(element):
    try:
        box = element.get_BoundingBox(None)
        if box is None:
            return None
        return (box.Min.X, box.Min.Y, box.Min.Z, box.Max.X, box.Max.Y, box.Max.Z)
    except Exception:
        return None


def bbox_intersection(a, b, tolerance=0.0):
    if not a or not b:
        return False
    return not (
        a[3] + tolerance < b[0] or b[3] + tolerance < a[0] or
        a[4] + tolerance < b[1] or b[4] + tolerance < a[1] or
        a[5] + tolerance < b[2] or b[5] + tolerance < a[2]
    )


def bbox_overlap_volume(a, b):
    if not bbox_intersection(a, b):
        return 0.0
    dx = max(0.0, min(a[3], b[3]) - max(a[0], b[0]))
    dy = max(0.0, min(a[4], b[4]) - max(a[1], b[1]))
    dz = max(0.0, min(a[5], b[5]) - max(a[2], b[2]))
    return dx * dy * dz


def get_level_name(element):
    for pname in ("Level", "Reference Level", "Base Level", "Schedule Level"):
        text = get_param_text(element, pname)
        if text:
            return text
    try:
        level_id = element.LevelId
        level = doc.GetElement(level_id) if level_id else None
        return level.Name if level else "Unassigned"
    except Exception:
        return "Unassigned"


def get_type_name(element):
    try:
        type_el = doc.GetElement(element.GetTypeId())
        return type_el.Name if type_el else "Unknown Type"
    except Exception:
        return "Unknown Type"


def get_element_record(element):
    bbox = get_bbox(element)
    return {
        "id": element_id_value(element.Id),
        "unique_id": str(getattr(element, "UniqueId", "")),
        "category": category_bic_name(element),
        "category_name": category_key(element),
        "name": str(getattr(element, "Name", "")),
        "type": get_type_name(element),
        "level": get_level_name(element),
        "workset": get_workset_name(element),
        "bbox_internal": list(bbox) if bbox else None
    }


def get_workset_name(element):
    try:
        wsid = element.WorksetId
        ws = doc.GetWorksetTable().GetWorkset(wsid)
        return ws.Name if ws else "Unknown"
    except Exception:
        return "Not applicable"


def get_group_id(element):
    try:
        gid = element.GroupId
        val = element_id_value(gid)
        return val if val > 0 else None
    except Exception:
        return None


def get_assembly_id(element):
    try:
        aid = element.AssemblyInstanceId
        val = element_id_value(aid)
        return val if val > 0 else None
    except Exception:
        return None


def is_nested_family(element):
    try:
        if not isinstance(element, FamilyInstance):
            return False
        symbol = element.Symbol
        family = symbol.Family if symbol else None
        return bool(family and family.IsInPlace)  # best-effort; nested/shared checks vary by family
    except Exception:
        return False


def is_hosted(element):
    try:
        return isinstance(element, FamilyInstance) and element.Host is not None
    except Exception:
        return False


def load_config():
    if not os.path.exists(CONFIG_PATH):
        with open_text_compat(CONFIG_PATH, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        return DEFAULT_CONFIG
    try:
        with open_text_compat(CONFIG_PATH, "r") as f:
            user_config = json.load(f)
        merged = dict(DEFAULT_CONFIG)
        merged.update(user_config)
        for key in ("required_parameters", "naming_regex", "clearance_tolerances_mm", "health_weights"):
            merged[key] = dict(DEFAULT_CONFIG.get(key, {}), **user_config.get(key, {}))
        return merged
    except Exception as ex:
        forms.alert("Config file could not be read. Using defaults.\n\n{}".format(ex), title="BIM Audit")
        return DEFAULT_CONFIG


CONFIG = load_config()


def all_model_elements():
    elements = []
    collector = FilteredElementCollector(doc).WhereElementIsNotElementType()
    for element in collector:
        try:
            if element.Category and element.get_BoundingBox(None) is not None:
                elements.append(element)
        except Exception:
            continue
    return elements


def get_bic_map():
    result = {}
    for el in all_model_elements():
        key = category_bic_name(el)
        if key:
            result.setdefault(key, []).append(el)
    return result


def element_is_ignored_pair(a, b, config):
    if config.get("ignore_hosted_elements") and (is_hosted(a) or is_hosted(b)):
        return True
    if config.get("ignore_nested_or_shared_family_components") and (is_nested_family(a) or is_nested_family(b)):
        return True
    if config.get("ignore_same_group"):
        ga, gb = get_group_id(a), get_group_id(b)
        if ga is not None and ga == gb:
            return True
    if config.get("ignore_same_assembly"):
        aa, ab = get_assembly_id(a), get_assembly_id(b)
        if aa is not None and aa == ab:
            return True
    return False


def clash_pair_key(cat_a, cat_b):
    return "|".join(sorted([cat_a, cat_b]))


def detect_clashes(elements, config):
    """Fast AABB candidate detection. This is not a replacement for solid/geometry clash tests."""
    category_map = {}
    for el in elements:
        cat = category_bic_name(el)
        box = get_bbox(el)
        if cat and box:
            category_map.setdefault(cat, []).append((el, box))

    hard_pairs = set()
    for pair in config.get("hard_clash_categories", []):
        if len(pair) == 2:
            hard_pairs.add(clash_pair_key(pair[0], pair[1]))

    tolerance_map = {}
    for key, value in config.get("clearance_tolerances_mm", {}).items():
        bits = key.split("|")
        if len(bits) == 2:
            tolerance_map[clash_pair_key(bits[0], bits[1])] = mm_to_internal(value)

    clashes = []
    seen = set()
    cats = list(category_map.keys())
    for i, cat_a in enumerate(cats):
        for cat_b in cats[i:]:
            pair_key = clash_pair_key(cat_a, cat_b)
            is_hard_pair = pair_key in hard_pairs
            clearance = tolerance_map.get(pair_key)
            if not is_hard_pair and clearance is None:
                continue
            list_a = category_map[cat_a]
            list_b = category_map[cat_b]
            for idx_a, (a, box_a) in enumerate(list_a):
                start = idx_a + 1 if cat_a == cat_b else 0
                for b, box_b in list_b[start:]:
                    aid, bid = element_id_value(a.Id), element_id_value(b.Id)
                    if aid == bid:
                        continue
                    pair_ids = tuple(sorted((aid, bid)))
                    if pair_ids in seen:
                        continue
                    if element_is_ignored_pair(a, b, config):
                        continue
                    if is_hard_pair:
                        if bbox_overlap_volume(box_a, box_b) <= 1e-8:
                            continue
                        kind = "Hard"
                        overlap = bbox_overlap_volume(box_a, box_b)
                    else:
                        if bbox_intersection(box_a, box_b, clearance):
                            # Only classify as clearance when boxes do not already overlap.
                            if bbox_overlap_volume(box_a, box_b) > 1e-8:
                                continue
                            kind = "Clearance"
                            overlap = 0.0
                        else:
                            continue
                    seen.add(pair_ids)
                    clashes.append({
                        "clash_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "{}:{}:{}".format(doc.Title, pair_ids[0], pair_ids[1]))),
                        "kind": kind,
                        "status": "Active",
                        "element_a": get_element_record(a),
                        "element_b": get_element_record(b),
                        "overlap_volume_internal": overlap,
                        "tolerance_mm": internal_to_mm(clearance) if clearance is not None and not is_hard_pair else 0,
                        "message": "{} clash between {} ({}) and {} ({})".format(
                            kind, category_key(a), aid, category_key(b), bid)
                    })
    return clashes


def validate_naming(config):
    issues = []
    regexes = config.get("naming_regex", {})
    targets = [
        ("views", FilteredElementCollector(doc).OfClass(__import__("Autodesk.Revit.DB", fromlist=["View"]).View).ToElements()),
        ("sheets", FilteredElementCollector(doc).OfClass(__import__("Autodesk.Revit.DB", fromlist=["ViewSheet"]).ViewSheet).ToElements()),
        ("levels", FilteredElementCollector(doc).OfClass(Level).ToElements()),
        ("grids", FilteredElementCollector(doc).OfClass(__import__("Autodesk.Revit.DB", fromlist=["Grid"]).Grid).ToElements())
    ]
    for key, items in targets:
        pattern = regexes.get(key)
        if not pattern:
            continue
        try:
            compiled = re.compile(pattern)
        except Exception:
            issues.append({"type": "config_error", "rule": key, "message": "Invalid regex: {}".format(pattern)})
            continue
        for item in items:
            try:
                if key == "views" and item.IsTemplate:
                    continue
                name = item.Name
                if not compiled.match(name or ""):
                    issues.append({
                        "type": "naming",
                        "rule": key,
                        "element_id": element_id_value(item.Id),
                        "name": name,
                        "message": "{} name does not match configured regex".format(key.title())
                    })
            except Exception:
                pass
    # Workset naming is checked only in workshared documents.
    try:
        if doc.IsWorkshared and regexes.get("worksets"):
            pat = re.compile(regexes["worksets"])
            for ws in doc.GetWorksetTable().GetWorksets():
                if ws.Kind == WorksetKind.UserWorkset and not pat.match(ws.Name or ""):
                    issues.append({"type": "naming", "rule": "worksets", "name": ws.Name,
                                   "message": "Workset name does not match configured regex"})
    except Exception:
        pass
    return issues


def required_parameter_check(elements, config):
    results = []
    rules = config.get("required_parameters", {})
    by_cat = {}
    for el in elements:
        cat = category_bic_name(el)
        if cat in rules:
            by_cat.setdefault(cat, []).append(el)
    for cat, names in rules.items():
        items = by_cat.get(cat, [])
        if not items:
            results.append({"category": cat, "element_count": 0, "required_count": len(names),
                            "filled_percent": 100.0, "missing": []})
            continue
        missing = []
        total = len(items) * max(1, len(names))
        filled = 0
        for el in items:
            for pname in names:
                p = get_param(el, pname)
                value = get_param_text(el, pname)
                has_value = bool(value and value.strip())
                if has_value:
                    filled += 1
                else:
                    missing.append({"element_id": element_id_value(el.Id), "category": cat,
                                    "element_name": str(getattr(el, "Name", "")),
                                    "parameter": pname})
        results.append({
            "category": cat, "element_count": len(items), "required_count": len(names),
            "filled_percent": round((filled / float(total)) * 100.0, 2) if total else 100.0,
            "missing": missing
        })
    return results


def workset_health():
    issues = []
    if not doc.IsWorkshared:
        return [{"type": "workset_info", "message": "Model is not workshared; workset checks skipped."}]
    try:
        worksets = list(doc.GetWorksetTable().GetWorksets())
        user_worksets = [ws for ws in worksets if ws.Kind == WorksetKind.UserWorkset]
        for ws in user_worksets:
            if ws.Name == "Workset1":
                count = 0
                for el in FilteredElementCollector(doc).WhereElementIsNotElementType():
                    try:
                        if element_id_value(el.WorksetId) == element_id_value(ws.Id):
                            count += 1
                    except Exception:
                        pass
                if count:
                    issues.append({"type": "workset_default", "workset": ws.Name, "count": count,
                                   "message": "{} elements remain on default Workset1".format(count)})
            try:
                if not ws.IsOpen:
                    issues.append({"type": "workset_closed", "workset": ws.Name,
                                   "message": "Workset is closed"})
            except Exception:
                pass
        # Borrowed status is API/version/worksharing-state dependent.
        borrowed = 0
        for el in FilteredElementCollector(doc).WhereElementIsNotElementType():
            try:
                if el.IsBorrowed:
                    borrowed += 1
            except Exception:
                pass
        if borrowed:
            issues.append({"type": "borrowed_elements", "count": borrowed,
                           "message": "{} elements are currently borrowed".format(borrowed)})
    except Exception as ex:
        issues.append({"type": "workset_check_error", "message": str(ex)})
    return issues


def link_coordination_checks():
    issues = []
    try:
        links = FilteredElementCollector(doc).OfClass(RevitLinkInstance).ToElements()
        for link in links:
            try:
                transform = link.GetTotalTransform()
                # Report positioning for manual verification; Revit API does not provide one
                # universal reliable "shared coordinates used" flag for every link workflow.
                issues.append({
                    "type": "link_positioning_review",
                    "element_id": element_id_value(link.Id),
                    "link_name": link.Name,
                    "pinned": bool(link.Pinned),
                    "transform_origin_internal": [transform.Origin.X, transform.Origin.Y, transform.Origin.Z],
                    "message": "Verify link positioning mode and shared-coordinate coordination manually."
                })
            except Exception as link_ex:
                issues.append({"type": "link_check_error", "element_id": element_id_value(link.Id),
                               "message": str(link_ex)})
        # True North / Project North is a view-orientation setting, not one global model flag.
        for view in FilteredElementCollector(doc).OfClass(View3D).ToElements():
            if view.IsTemplate:
                continue
            try:
                if view.ViewType == ViewType.ThreeD and not view.IsSectionBoxActive:
                    issues.append({"type": "3d_view_no_section_box", "element_id": element_id_value(view.Id),
                                   "view": view.Name, "message": "3D view has no active section box"})
            except Exception:
                pass
    except Exception as ex:
        issues.append({"type": "link_check_error", "message": str(ex)})
    return issues


def duplicate_detection(elements, config):
    tol = mm_to_internal(config.get("duplicate_location_tolerance_mm", 10))
    buckets = {}
    duplicates = []
    for el in elements:
        try:
            loc = el.Location
            point = getattr(loc, "Point", None)
            if point is None:
                curve = getattr(loc, "Curve", None)
                if curve:
                    point = curve.Evaluate(0.5, True)
            if point is None:
                continue
            cat = category_bic_name(el)
            typ = get_type_name(el)
            key = (cat, typ, round(point.X / max(tol, 1e-9)), round(point.Y / max(tol, 1e-9)),
                   round(point.Z / max(tol, 1e-9)))
            if key in buckets:
                other = buckets[key]
                duplicates.append({"type": "duplicate", "element_a": element_id_value(other.Id),
                                   "element_b": element_id_value(el.Id), "category": cat, "type_name": typ,
                                   "message": "Possible duplicate: same category/type/location within tolerance"})
            else:
                buckets[key] = el
        except Exception:
            continue
    return duplicates


def unused_and_performance_checks(config):
    issues = []
    # Unused checks are best-effort: Revit exposes usage differently by object type.
    try:
        for template in FilteredElementCollector(doc).OfClass(View3D).ToElements():
            if not template.IsTemplate:
                try:
                    if not template.IsSectionBoxActive:
                        issues.append({"type": "performance", "element_id": element_id_value(template.Id),
                                       "message": "3D view '{}' has no section box".format(template.Name)})
                except Exception:
                    pass
    except Exception:
        pass
    try:
        symbols = FilteredElementCollector(doc).OfClass(FamilyInstance).WhereElementIsNotElementType().ToElements()
        families = {}
        for inst in symbols:
            try:
                fam = inst.Symbol.Family
                if fam:
                    families[element_id_value(fam.Id)] = fam
            except Exception:
                pass
        threshold = float(config.get("family_size_threshold_mb", 20))
        for fam in families.values():
            try:
                # Family file size cannot generally be read from a loaded family in the project.
                # Mark as not measurable rather than inventing a size.
                pass
            except Exception:
                pass
        issues.append({"type": "performance_info", "message":
                       "Family file-size and nested-family-depth checks need family-document inspection; not inferred from loaded model."})
    except Exception:
        pass
    return issues


def warning_severity(description):
    text = (description or "").lower()
    if any(token in text for token in ("overlap", "duplicate", "room separation", "identical mark")):
        return "CRITICAL"
    if any(token in text for token in ("off axis", "highlight", "joined geometry", "cannot keep joined")):
        return "HIGH"
    if any(token in text for token in ("slightly", "constraint", "room", "wall")):
        return "MEDIUM"
    return "LOW"


def document_warnings():
    """Group native Revit warnings without changing the model."""
    grouped = {}
    try:
        warnings = doc.GetWarnings()
    except Exception:
        return []
    for warning in warnings:
        try:
            description = warning.GetDescriptionText() or "Revit warning"
            try:
                failure_id = str(warning.GetFailureDefinitionId().Guid)
            except Exception:
                failure_id = description
            key = failure_id + "|" + description
            item = grouped.setdefault(key, {
                "type": "revit_warning", "failure_id": failure_id,
                "description": description, "severity": warning_severity(description),
                "count": 0, "element_ids": []
            })
            item["count"] += 1
            for element_id in warning.GetFailingElements():
                value = element_id_value(element_id)
                if value >= 0 and value not in item["element_ids"]:
                    item["element_ids"].append(value)
        except Exception:
            continue
    return sorted(grouped.values(), key=lambda item: (-item["count"], item["description"]))


def collect_standards(elements, config):
    return {
        "revit_warnings": document_warnings(),
        "naming_issues": validate_naming(config),
        "required_parameters": required_parameter_check(elements, config),
        "workset_issues": workset_health(),
        "link_and_view_issues": link_coordination_checks(),
        "duplicates": duplicate_detection(elements, config),
        "performance_flags": unused_and_performance_checks(config)
    }


def flatten_warnings(clashes, standards):
    warnings = []
    for c in clashes:
        warnings.append({"key": "clash:" + c["clash_id"], "kind": "clash", "severity": c["kind"],
                         "status": c.get("status", "Active"), "message": c["message"]})
    for issue in standards.get("revit_warnings", []):
        warnings.append({
            "key": "revitwarning:" + issue["failure_id"], "kind": "revit_warning",
            "severity": issue["severity"], "status": "Active",
            "message": "{} occurrence(s): {}".format(issue["count"], issue["description"])
        })
    for issue in standards.get("naming_issues", []):
        warnings.append({"key": "naming:{}:{}".format(issue.get("rule"), issue.get("element_id", issue.get("name"))),
                         "kind": "warning", "severity": "Low", "status": "Active", "message": issue["message"]})
    for cat in standards.get("required_parameters", []):
        for missing in cat.get("missing", []):
            warnings.append({"key": "param:{}:{}:{}".format(missing["category"], missing["element_id"], missing["parameter"]),
                             "kind": "warning", "severity": "Low", "status": "Active",
                             "message": "Missing {} on element {}".format(missing["parameter"], missing["element_id"])})
    for group in ("workset_issues", "link_and_view_issues", "duplicates", "performance_flags"):
        for issue in standards.get(group, []):
            if issue.get("type", "").endswith("_info"):
                continue
            warnings.append({"key": "{}:{}:{}".format(group, issue.get("type"), issue.get("element_id", issue.get("element_a", issue.get("workset", issue.get("message", ""))))),
                             "kind": "warning", "severity": "Medium", "status": "Active",
                             "message": issue.get("message", str(issue))})
    return warnings


def previous_audit_files():
    files = []
    for name in os.listdir(REPORT_DIR):
        if name.startswith("audit_") and name.endswith(".json") and name != os.path.basename(CONFIG_PATH):
            path = os.path.join(REPORT_DIR, name)
            files.append(path)
    return sorted(files, key=lambda p: os.path.getmtime(p), reverse=True)


def load_previous_audit(current_doc_key):
    for path in previous_audit_files():
        try:
            with open_text_compat(path, "r") as f:
                data = json.load(f)
            if data.get("document_key") == current_doc_key:
                return data
        except Exception:
            continue
    return None


def compare_history(current_warnings, previous):
    previous_map = {w.get("key"): w for w in (previous or {}).get("warnings", [])}
    current_map = {w.get("key"): w for w in current_warnings}
    changes = []
    for key, warning in current_map.items():
        state = "UNCHANGED" if key in previous_map else "NEW"
        changes.append(dict(warning, trend=state))
    for key, warning in previous_map.items():
        if key not in current_map:
            changes.append(dict(warning, trend="RESOLVED", status="Resolved"))
    return changes


def health_score(clashes, standards, config):
    weights = config.get("health_weights", {})
    penalty = len([c for c in clashes if c.get("kind") == "Hard"]) * weights.get("hard_clash", 8)
    penalty += len([c for c in clashes if c.get("kind") == "Clearance"]) * weights.get("clearance_clash", 3)
    for warning in standards.get("revit_warnings", []):
        severity = warning.get("severity", "LOW").lower()
        penalty += warning.get("count", 1) * weights.get(severity + "_revit_warning", 0)
    penalty += sum(len(c.get("missing", [])) for c in standards.get("required_parameters", [])) * weights.get("missing_parameter", 1)
    penalty += len(standards.get("naming_issues", [])) * weights.get("naming_issue", 1)
    penalty += len([i for i in standards.get("workset_issues", []) if i.get("type") not in ("workset_info",)]) * weights.get("workset_issue", 2)
    penalty += len(standards.get("duplicates", [])) * weights.get("duplicate", 3)
    penalty += len([i for i in standards.get("link_and_view_issues", []) if i.get("type") == "link_positioning_review"]) * weights.get("link_coordination", 4)
    penalty += len([i for i in standards.get("performance_flags", []) if i.get("type") == "performance"]) * weights.get("performance_flag", 1)
    return max(0, min(100, 100 - penalty))


def fix_first(clashes, standards, config):
    weights = config.get("health_weights", {})
    actions = []
    hard = [c for c in clashes if c.get("kind") == "Hard"]
    clear = [c for c in clashes if c.get("kind") == "Clearance"]
    if hard:
        actions.append({"impact": len(hard) * weights.get("hard_clash", 8), "title": "Resolve hard clashes",
                        "count": len(hard), "details": "Review intersecting element pairs and adjust geometry/coordination."})
    if clear:
        actions.append({"impact": len(clear) * weights.get("clearance_clash", 3), "title": "Resolve clearance clashes",
                        "count": len(clear), "details": "Check service access and configured category-pair tolerances."})
    for severity in ("CRITICAL", "HIGH", "MEDIUM"):
        findings = [w for w in standards.get("revit_warnings", []) if w.get("severity") == severity]
        count = sum(w.get("count", 1) for w in findings)
        if count:
            weight = weights.get(severity.lower() + "_revit_warning", 0)
            actions.append({"impact": count * weight, "title": "Resolve {} Revit warnings".format(severity.lower()),
                            "count": count, "details": "Review the grouped native Revit warnings and affected element IDs."})
    missing = sum(len(c.get("missing", [])) for c in standards.get("required_parameters", []))
    if missing:
        actions.append({"impact": missing * weights.get("missing_parameter", 1), "title": "Complete required parameters",
                        "count": missing, "details": "Fill required parameters by category; see parameter completeness table."})
    naming = len(standards.get("naming_issues", []))
    if naming:
        actions.append({"impact": naming * weights.get("naming_issue", 1), "title": "Fix naming convention violations",
                        "count": naming, "details": "Rename views, sheets, levels, grids, and worksets to match regex rules."})
    duplicates = len(standards.get("duplicates", []))
    if duplicates:
        actions.append({"impact": duplicates * weights.get("duplicate", 3), "title": "Review possible duplicate elements",
                        "count": duplicates, "details": "Confirm duplicates before deleting anything."})
    worksets = len(standards.get("workset_issues", []))
    if worksets:
        actions.append({"impact": worksets * weights.get("workset_issue", 2), "title": "Review workset health",
                        "count": worksets, "details": "Move elements off Workset1 and review closed/borrowed worksets."})
    links = len([i for i in standards.get("link_and_view_issues", []) if i.get("type") == "link_positioning_review"])
    if links:
        actions.append({"impact": links * weights.get("link_coordination", 4), "title": "Verify linked-model coordination",
                        "count": links, "details": "Confirm link positioning and shared coordinates with each discipline."})
    actions.sort(key=lambda x: x["impact"], reverse=True)
    return actions[:10]


def discipline_name(cat):
    cat = cat or ""
    if any(x in cat for x in ("Structural", "OST_Walls", "OST_Floors", "OST_Columns", "OST_Structural")):
        return "Architecture/Structure"
    if any(x in cat for x in ("Duct", "Pipe", "Mechanical")):
        return "Mechanical/Plumbing"
    if any(x in cat for x in ("Electrical", "CableTray", "Conduit", "Lighting")):
        return "Electrical"
    return "Other"


def breakdown_tables(elements, clashes):
    per_level, per_discipline = {}, {}
    for el in elements:
        level = get_level_name(el)
        cat = category_bic_name(el)
        per_level.setdefault(level, {"elements": 0, "hard_clashes": 0, "clearance_clashes": 0})
        per_level[level]["elements"] += 1
        disc = discipline_name(cat)
        per_discipline.setdefault(disc, {"elements": 0, "hard_clashes": 0, "clearance_clashes": 0})
        per_discipline[disc]["elements"] += 1
    for c in clashes:
        for rec in (c["element_a"], c["element_b"]):
            level = rec.get("level", "Unassigned")
            disc = discipline_name(rec.get("category", ""))
            key = "hard_clashes" if c["kind"] == "Hard" else "clearance_clashes"
            per_level.setdefault(level, {"elements": 0, "hard_clashes": 0, "clearance_clashes": 0})[key] += 1
            per_discipline.setdefault(disc, {"elements": 0, "hard_clashes": 0, "clearance_clashes": 0})[key] += 1
    return {"per_level": per_level, "per_discipline": per_discipline}


def export_clashes_csv(clashes, path):
    fields = ["clash_id", "kind", "status", "element_a_id", "element_a_category", "element_a_name",
              "element_a_level", "element_b_id", "element_b_category", "element_b_name",
              "element_b_level", "tolerance_mm", "message"]
    with open(path, "wb") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for c in clashes:
            a, b = c["element_a"], c["element_b"]
            writer.writerow({
                "clash_id": c["clash_id"], "kind": c["kind"], "status": c.get("status", "Active"),
                "element_a_id": a["id"], "element_a_category": a["category_name"], "element_a_name": a["name"],
                "element_a_level": a["level"], "element_b_id": b["id"], "element_b_category": b["category_name"],
                "element_b_name": b["name"], "element_b_level": b["level"],
                "tolerance_mm": c.get("tolerance_mm", 0), "message": c["message"]
            })


def bcf_guid(value):
    return "{" + str(value).upper() + "}"


def export_bcf21(clashes, path, project_name):
    """Write a minimal BCF 2.1 ZIP with one viewpoint per clash (AABB center camera)."""
    import xml.etree.ElementTree as ET
    ns = "http://www.buildingsmart-tech.org/bcf/2.1"
    ET.register_namespace("", ns)
    root = ET.Element("Markup", {"xmlns": ns})
    header = ET.SubElement(root, "Header")
    ET.SubElement(header, "File", {"IfcProject": "", "IfcSpatialStructureElement": "", "isExternal": "true", "filename": project_name + ".rvt"})
    topic_list = []
    temp_dir = os.path.join(REPORT_DIR, "_bcf_temp_" + uuid.uuid4().hex)
    makedirs_compat(temp_dir)
    try:
        for c in clashes:
            topic_guid = bcf_guid(c["clash_id"])
            topic_dir = os.path.join(temp_dir, topic_guid)
            makedirs_compat(topic_dir)
            topic = ET.SubElement(root, "Topic", {
                "Guid": topic_guid, "TopicType": "Clash", "TopicStatus": c.get("status", "Active"),
                "Title": c["message"][:200], "Priority": "Normal", "Index": str(len(topic_list) + 1),
                "CreationDate": now_iso(), "CreationAuthor": "Revit Scanner"
            })
            ET.SubElement(topic, "Description").text = c["message"]
            viewpoint_guid = bcf_guid(uuid.uuid5(uuid.NAMESPACE_URL, c["clash_id"] + ":viewpoint"))
            viewpoint = ET.SubElement(topic, "Viewpoints")
            ET.SubElement(viewpoint, "ViewPoint", {"Guid": viewpoint_guid, "Viewpoint": "viewpoint.bcfv"})
            # Minimal viewpoint with selected components. BCF viewers may require richer camera/IFC GUID data.
            vroot = ET.Element("VisualizationInfo", {"xmlns": ns})
            components = ET.SubElement(vroot, "Components")
            selection = ET.SubElement(components, "Selection")
            for rec in (c["element_a"], c["element_b"]):
                ET.SubElement(selection, "Component", {"IfcGuid": "", "OriginatingSystem": "Autodesk Revit",
                                                       "AuthoringToolId": str(rec["unique_id"])})
            box_a = c["element_a"].get("bbox_internal")
            box_b = c["element_b"].get("bbox_internal")
            if box_a and box_b:
                center = [(min(box_a[i], box_b[i]) + max(box_a[i + 3], box_b[i + 3])) / 2.0 for i in range(3)]
            else:
                center = [0.0, 0.0, 0.0]
            camera = ET.SubElement(vroot, "PerspectiveCamera")
            ET.SubElement(camera, "CameraViewPoint").attrib.update({"X": str(center[0]), "Y": str(center[1]), "Z": str(center[2] + 10)})
            ET.SubElement(camera, "CameraDirection").attrib.update({"X": "0", "Y": "0", "Z": "-1"})
            ET.SubElement(camera, "CameraUpVector").attrib.update({"X": "0", "Y": "1", "Z": "0"})
            ET.SubElement(camera, "FieldOfView").text = "60"
            ET.SubElement(camera, "AspectRatio").text = "1.7777778"
            with open(os.path.join(topic_dir, "markup.bcf"), "wb") as f:
                f.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))
            with open(os.path.join(topic_dir, "viewpoint.bcfv"), "wb") as f:
                f.write(ET.tostring(vroot, encoding="utf-8", xml_declaration=True))
            topic_list.append(topic_guid)
        # Rebuild a single root markup file for the package root.
        root_path = os.path.join(temp_dir, "markup.bcf")
        with open(root_path, "wb") as f:
            f.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(root_path, "markup.bcf")
            for dirpath, _, filenames in os.walk(temp_dir):
                for filename in filenames:
                    full = os.path.join(dirpath, filename)
                    rel = os.path.relpath(full, temp_dir)
                    if rel != "markup.bcf":
                        zf.write(full, rel)
    finally:
        try:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def print_report(data):
    print("## Model Health & Clash Audit")
    print("**Model:** {}  ".format(data["model_name"]))
    print("**Timestamp:** {}  ".format(data["timestamp"]))
    print("**Health Score:** {}/100 (delta: {:+})".format(data["health_score"], data.get("health_score_delta", 0)))
    print("")
    print("### Clash summary")
    print("- Hard clashes: {}".format(sum(1 for c in data["clashes"] if c["kind"] == "Hard")))
    print("- Clearance clashes: {}".format(sum(1 for c in data["clashes"] if c["kind"] == "Clearance")))
    print("- Warnings trend: NEW {}, RESOLVED {}, UNCHANGED {}".format(
        sum(1 for w in data["warning_trends"] if w["trend"] == "NEW"),
        sum(1 for w in data["warning_trends"] if w["trend"] == "RESOLVED"),
        sum(1 for w in data["warning_trends"] if w["trend"] == "UNCHANGED")))
    print("- Native Revit warning groups: {}".format(len(data["standards"].get("revit_warnings", []))))
    print("")
    print("### Top 10 — Fix first")
    for i, item in enumerate(data["fix_first"], 1):
        print("{}. **{}** — estimated impact {} points; {} item(s). {}".format(
            i, item["title"], item["impact"], item["count"], item["details"]))
    print("")
    print("### Required parameter completeness")
    for item in data["standards"]["required_parameters"]:
        print("- **{}**: {}% filled across {} element(s); {} missing value(s)".format(
            item["category"], item["filled_percent"], item["element_count"], len(item["missing"])))
    print("")
    print("### Per-discipline breakdown")
    for name, row in sorted(data["breakdowns"]["per_discipline"].items()):
        print("- **{}**: {} elements, {} hard clashes, {} clearance clashes".format(
            name, row["elements"], row["hard_clashes"], row["clearance_clashes"]))
    print("")
    print("### Per-level breakdown")
    for name, row in sorted(data["breakdowns"]["per_level"].items()):
        print("- **{}**: {} elements, {} hard clashes, {} clearance clashes".format(
            name, row["elements"], row["hard_clashes"], row["clearance_clashes"]))
    print("")
    print("### Exports")
    for key, value in data["exports"].items():
        print("- **{}:** `{}`".format(key, value))



# ---------------------------------------------------------------------------
# MEP GENERATION EXTENSION — Existing-model workflow
# ---------------------------------------------------------------------------
# This extension creates preliminary Revit Pipe and Conduit elements where suitable
# Revit types/system types exist. It intentionally does not fabricate fixtures:
# load/position suitable families first, or enable configured fixture placement.
# Pipe/conduit runs are routed as orthogonal segments; fittings and engineered
# calculations must be reviewed by a qualified BIM/MEP professional.

MEP_DEFAULTS = {
    "enable_pipe_generation": True,
    "enable_conduit_generation": True,
    "enable_fixture_placement": False,
    "route_existing_plumbing_fixtures": True,
    "route_existing_electrical_devices": True,
    "supply_system_type_contains": ["Domestic", "Cold Water", "Potable"],
    "drain_system_type_contains": ["Sanitary", "Domestic", "Waste"],
    "electrical_panel_name_contains": ["Panel", "DB", "MDB"],
    "supply_pipe_type_contains": ["Domestic", "Water"],
    "drain_pipe_type_contains": ["Sanitary", "Waste", "Drain"],
    "conduit_type_contains": ["Conduit", "Electrical"],
    "supply_diameter_mm": 20,
    "drain_diameter_mm": 75,
    "conduit_diameter_mm": 20,
    "horizontal_route_offset_mm": 300,
    "minimum_route_length_mm": 150,
    "fixture_categories": [
        "OST_PlumbingFixtures", "OST_Sprinklers", "OST_MechanicalEquipment"
    ],
    "electrical_device_categories": [
        "OST_LightingFixtures", "OST_ElectricalFixtures", "OST_ElectricalEquipment"
    ],
    "notes": "Use actual loaded pipe/conduit/system types; verify all routes and connections."
}


def merge_mep_config():
    try:
        path = os.path.join(REPORT_DIR, "mep_config.json")
        if not os.path.exists(path):
            with open_text_compat(path, "w") as f:
                json.dump(MEP_DEFAULTS, f, indent=2)
            return dict(MEP_DEFAULTS)
        with open_text_compat(path, "r") as f:
            user = json.load(f)
        merged = dict(MEP_DEFAULTS)
        merged.update(user)
        return merged
    except Exception:
        return dict(MEP_DEFAULTS)


MEP_CONFIG = merge_mep_config()


def _revit_api_class(name):
    # Revit API places MEP classes in nested namespaces in many Revit versions.
    import Autodesk.Revit.DB as DB
    if "." in name:
        current = DB
        for part in name.split("."):
            current = getattr(current, part, None)
            if current is None:
                return None
        return current
    found = getattr(DB, name, None)
    if found is not None:
        return found
    for namespace_name in ("Plumbing", "Electrical", "Structure", "Architecture"):
        namespace = getattr(DB, namespace_name, None)
        if namespace is not None:
            found = getattr(namespace, name, None)
            if found is not None:
                return found
    return None


def _type_name(el):
    try:
        return el.Name or ""
    except Exception:
        return ""


def _contains_any(value, candidates):
    value = (value or "").lower()
    return any((candidate or "").lower() in value for candidate in (candidates or []))


def _collect_class(class_name):
    cls = _revit_api_class(class_name)
    if cls is None:
        return []
    try:
        return list(FilteredElementCollector(doc).OfClass(cls).ToElements())
    except Exception:
        return []


def _find_named_type(class_name, candidates):
    items = _collect_class(class_name)
    for item in items:
        if _contains_any(_type_name(item), candidates):
            return item
    return items[0] if items else None


def _level_for_element(el):
    try:
        lid = el.LevelId
        level = doc.GetElement(lid)
        if isinstance(level, Level):
            return level
    except Exception:
        pass
    try:
        p = el.LookupParameter("Reference Level") or el.LookupParameter("Level")
        if p and p.StorageType.ToString() == "ElementId":
            level = doc.GetElement(p.AsElementId())
            if isinstance(level, Level):
                return level
    except Exception:
        pass
    levels = sorted(_collect_class("Level"), key=lambda x: x.Elevation)
    return levels[0] if levels else None


def _element_point(el):
    try:
        loc = el.Location
        point = getattr(loc, "Point", None)
        if point:
            return point
        curve = getattr(loc, "Curve", None)
        if curve:
            return curve.Evaluate(0.5, True)
    except Exception:
        pass
    box = get_bbox(el)
    if box:
        return XYZ((box[0]+box[3])/2, (box[1]+box[4])/2, (box[2]+box[5])/2)
    return None


def _get_connectors(el):
    result = []
    try:
        mep = getattr(el, "MEPModel", None)
        manager = mep.ConnectorManager if mep else None
        if manager:
            for connector in manager.Connectors:
                try:
                    if connector.ConnectorType.ToString() in ("End", "Curve", "Physical"):
                        result.append(connector)
                except Exception:
                    result.append(connector)
    except Exception:
        pass
    try:
        manager = getattr(el, "ConnectorManager", None)
        if manager:
            for connector in manager.Connectors:
                result.append(connector)
    except Exception:
        pass
    return result


def _get_builtin_category_ids(names):
    result = set()
    for name in names:
        try:
            bic = getattr(BuiltInCategory, name)
            result.add(int(bic))
        except Exception:
            pass
    return result


def _elements_in_categories(category_names):
    ids = _get_builtin_category_ids(category_names)
    found = []
    for el in FilteredElementCollector(doc).WhereElementIsNotElementType():
        try:
            if el.Category and int(el.Category.Id.Value) in ids:
                found.append(el)
                continue
        except Exception:
            try:
                if el.Category and int(el.Category.Id.IntegerValue) in ids:
                    found.append(el)
            except Exception:
                pass
    return found


def _all_elements_matching_category_tokens(tokens):
    out = []
    seen = set()
    for el in FilteredElementCollector(doc).WhereElementIsNotElementType():
        try:
            cat = category_bic_name(el) or ""
            cat_name = category_key(el) or ""
            combined = (cat + " " + cat_name).lower()
            if any((token or "").lower() in combined for token in tokens):
                eid = element_id_value(el.Id)
                if eid not in seen:
                    out.append(el)
                    seen.add(eid)
        except Exception:
            pass
    return out


def _distance(a, b):
    try:
        return a.DistanceTo(b)
    except Exception:
        return 1e99


def _orthogonal_points(start, end, offset_internal):
    # Stable Manhattan path: vertical to working elevation, XY turn, then endpoint.
    # The middle elevation is chosen above the lower endpoint for service routing.
    z = max(start.Z, end.Z) + offset_internal
    p1 = XYZ(start.X, start.Y, z)
    p2 = XYZ(end.X, start.Y, z)
    p3 = XYZ(end.X, end.Y, z)
    pts = [start, p1, p2, p3, end]
    compact = []
    for p in pts:
        if not compact or _distance(compact[-1], p) > mm_to_internal(1):
            compact.append(p)
    return compact


def _create_pipe_segment(system_type, pipe_type, level, start, end, diameter_mm, created_ids, log):
    import Autodesk.Revit.DB as DB
    if _distance(start, end) < mm_to_internal(MEP_CONFIG.get("minimum_route_length_mm", 150)):
        return
    try:
        pipe = DB.Plumbing.Pipe.Create(doc, system_type.Id, pipe_type.Id, level.Id, start, end)
        try:
            p = pipe.get_Parameter(BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
            if p and not p.IsReadOnly:
                p.Set(mm_to_internal(diameter_mm))
        except Exception:
            pass
        created_ids.append(element_id_value(pipe.Id))
    except Exception as ex:
        log.append("Pipe segment failed: {}".format(ex))


def _create_conduit_segment(conduit_type, level, start, end, diameter_mm, created_ids, log):
    import Autodesk.Revit.DB as DB
    if _distance(start, end) < mm_to_internal(MEP_CONFIG.get("minimum_route_length_mm", 150)):
        return
    try:
        conduit = DB.Electrical.Conduit.Create(doc, conduit_type.Id, start, end, level.Id)
        try:
            p = conduit.get_Parameter(BuiltInParameter.RBS_CONDUIT_DIAMETER_PARAM)
            if p and not p.IsReadOnly:
                p.Set(mm_to_internal(diameter_mm))
        except Exception:
            pass
        created_ids.append(element_id_value(conduit.Id))
    except Exception as ex:
        log.append("Conduit segment failed: {}".format(ex))


def _route_to_nearest_anchor(device, anchors, system_type, pipe_type, level, diameter_mm,
                             route_offset_mm, created_ids, log, is_conduit=False):
    start = _element_point(device)
    if start is None or not anchors:
        return False
    anchor = min(anchors, key=lambda item: _distance(start, item[0]))
    end, anchor_level = anchor
    use_level = level or anchor_level
    if use_level is None:
        return False
    pts = _orthogonal_points(start, end, mm_to_internal(route_offset_mm))
    for i in range(len(pts)-1):
        if is_conduit:
            _create_conduit_segment(pipe_type, use_level, pts[i], pts[i+1], diameter_mm, created_ids, log)
        else:
            _create_pipe_segment(system_type, pipe_type, use_level, pts[i], pts[i+1], diameter_mm, created_ids, log)
    return True


def _existing_mep_anchor_points(category_tokens):
    anchors = []
    for el in _all_elements_matching_category_tokens(category_tokens):
        point = _element_point(el)
        level = _level_for_element(el)
        if point is not None and level is not None:
            anchors.append((point, level, el))
    return anchors


def _discover_mep_types():
    return {
        "pipe_types": _collect_class("Plumbing.PipeType") or _collect_class("PipeType"),
        "piping_system_types": _collect_class("Plumbing.PipingSystemType") or _collect_class("PipingSystemType"),
        "conduit_types": _collect_class("Electrical.ConduitType") or _collect_class("ConduitType"),
        "electrical_system_types": _collect_class("Electrical.ElectricalSystemType") or _collect_class("ElectricalSystemType"),
        "family_symbols": _collect_class("FamilySymbol")
    }


def _summary_type_names(items):
    return sorted(set(_type_name(x) for x in items if _type_name(x)))


def run_mep_generation():
    """Generate pipe/conduit route segments from existing model devices and log all omissions."""
    report = {"created_pipe_ids": [], "created_conduit_ids": [], "warnings": [], "available_types": {}}
    types = _discover_mep_types()
    report["available_types"] = {
        "pipe_types": _summary_type_names(types["pipe_types"]),
        "piping_system_types": _summary_type_names(types["piping_system_types"]),
        "conduit_types": _summary_type_names(types["conduit_types"]),
        "electrical_system_types": _summary_type_names(types["electrical_system_types"])
    }

    supply_system = next((x for x in types["piping_system_types"]
                          if _contains_any(_type_name(x), MEP_CONFIG.get("supply_system_type_contains", []))), None)
    drain_system = next((x for x in types["piping_system_types"]
                         if _contains_any(_type_name(x), MEP_CONFIG.get("drain_system_type_contains", []))), None)
    supply_pipe = next((x for x in types["pipe_types"]
                        if _contains_any(_type_name(x), MEP_CONFIG.get("supply_pipe_type_contains", []))), None)
    drain_pipe = next((x for x in types["pipe_types"]
                       if _contains_any(_type_name(x), MEP_CONFIG.get("drain_pipe_type_contains", []))), None)
    conduit_type = next((x for x in types["conduit_types"]
                         if _contains_any(_type_name(x), MEP_CONFIG.get("conduit_type_contains", []))), None)
    # Never substitute an arbitrary system type: water and sanitary systems
    # are not interchangeable. Require explicitly matching system names.
    if not supply_system:
        log.append("Domestic-water system type not matched. Load/select a correct Domestic Cold Water system.")
    if not drain_system:
        log.append("Sanitary system type not matched. Load/select a correct Sanitary system.")
    if not supply_pipe:
        log.append("Domestic-water pipe type not matched; check type names in mep_config.json.")
    if not drain_pipe:
        log.append("Sanitary pipe type not matched; check type names in mep_config.json.")
    if not conduit_type:
        log.append("Conduit type not matched; load a conduit type or update mep_config.json.")

    plumbing_devices = _all_elements_matching_category_tokens(
        ("OST_PlumbingFixtures", "plumbing fixture"))
    electrical_devices = _all_elements_matching_category_tokens(
        ("OST_LightingFixtures", "OST_ElectricalFixtures",
         "lighting fixture", "electrical fixture"))
    pipe_anchors = _existing_mep_anchor_points(
        ("OST_PlumbingFixtures", "OST_MechanicalEquipment", "plumbing fixture", "mechanical equipment"))
    panel_anchors = _existing_mep_anchor_points(
        ("OST_ElectricalEquipment", "electrical equipment"))
    pipe_created, conduit_created, log = [], [], []

    if MEP_CONFIG.get("enable_pipe_generation", True):
        if not supply_system or not supply_pipe:
            log.append("Domestic water routing skipped: matching PipingSystemType/PipeType not found. Review mep_config.json.")
        elif not plumbing_devices:
            log.append("No existing plumbing fixtures/equipment found. Load and place plumbing families first.")
        else:
            # Use connectors when available; device location fallback is for route geometry only.
            for device in plumbing_devices:
                level = _level_for_element(device)
                point = _element_point(device)
                if point is None or level is None:
                    log.append("Skipped plumbing element {}: no usable point/level.".format(element_id_value(device.Id)))
                    continue
                # Anchor route geometry to nearest existing fixture/equipment; the generated run
                # is a starter route and does not claim connector/fitting connectivity.
                anchors = [(p, lv) for p, lv, _ in pipe_anchors if element_id_value(_.Id) != element_id_value(device.Id)]
                if not anchors:
                    anchors = [(XYZ(point.X + mm_to_internal(1500), point.Y, point.Z), level)]
                _route_to_nearest_anchor(device, anchors, supply_system, supply_pipe, level,
                    MEP_CONFIG.get("supply_diameter_mm", 20),
                    MEP_CONFIG.get("horizontal_route_offset_mm", 300), pipe_created, log, False)

        if not drain_system or not drain_pipe:
            log.append("Drainage routing skipped: matching sanitary PipingSystemType/PipeType not found. Review mep_config.json.")
        elif not plumbing_devices:
            log.append("No plumbing fixtures found for drainage routing.")
        else:
            for device in plumbing_devices:
                level = _level_for_element(device)
                point = _element_point(device)
                if point is None or level is None:
                    continue
                anchors = [(p, lv) for p, lv, other in pipe_anchors
                           if element_id_value(other.Id) != element_id_value(device.Id)]
                if not anchors:
                    anchors = [(XYZ(point.X + mm_to_internal(1200), point.Y, point.Z - mm_to_internal(300)), level)]
                _route_to_nearest_anchor(device, anchors, drain_system, drain_pipe, level,
                    MEP_CONFIG.get("drain_diameter_mm", 75),
                    -abs(MEP_CONFIG.get("horizontal_route_offset_mm", 300)), pipe_created, log, False)

    if MEP_CONFIG.get("enable_conduit_generation", True):
        if not conduit_type:
            log.append("Conduit routing skipped: no matching ConduitType found. Review mep_config.json.")
        elif not electrical_devices:
            log.append("No existing lights/outlets/electrical equipment found. Load and place families first.")
        else:
            anchors = [(p, lv) for p, lv, _ in panel_anchors]
            if not anchors:
                log.append("No electrical panel/equipment anchor found; electrical device routing skipped.")
            else:
                for device in electrical_devices:
                    level = _level_for_element(device)
                    if level is None:
                        continue
                    _route_to_nearest_anchor(device, anchors, None, conduit_type, level,
                        MEP_CONFIG.get("conduit_diameter_mm", 20),
                        MEP_CONFIG.get("horizontal_route_offset_mm", 300), conduit_created, log, True)

    report["created_pipe_ids"] = pipe_created
    report["created_conduit_ids"] = conduit_created
    report["warnings"] = log
    report["counts"] = {
        "plumbing_devices_found": len(plumbing_devices),
        "electrical_devices_found": len(electrical_devices),
        "pipe_segments_created": len(pipe_created),
        "conduit_segments_created": len(conduit_created),
        "pipe_types_found": len(types["pipe_types"]),
        "piping_system_types_found": len(types["piping_system_types"]),
        "conduit_types_found": len(types["conduit_types"]),
        "electrical_system_types_found": len(types["electrical_system_types"]),
        "family_symbols_loaded": len(types["family_symbols"])
    }
    report["device_category_counts"] = {}
    for el in FilteredElementCollector(doc).WhereElementIsNotElementType():
        try:
            catname = category_bic_name(el) or category_key(el) or "Unknown"
            if any(token in catname.lower() for token in ("plumbing", "fixture", "electrical", "lighting", "mechanical", "sprinkler")):
                report["device_category_counts"][catname] = report["device_category_counts"].get(catname, 0) + 1
        except Exception:
            pass
    return report


def write_mep_generation_report(report):
    path = os.path.join(REPORT_DIR, "mep_generation_{}.json".format(datetime.now().strftime("%Y%m%d_%H%M%S")))
    with open_text_compat(path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=True, default=str)
    return path


def print_mep_generation_report(report, path):
    print("")
    print("### MEP generation")
    print("- Existing plumbing devices found: {}".format(report.get("counts", {}).get("plumbing_devices_found", 0)))
    print("- Existing electrical devices found: {}".format(report.get("counts", {}).get("electrical_devices_found", 0)))
    print("- Pipe segments created: {}".format(report.get("counts", {}).get("pipe_segments_created", 0)))
    print("- Conduit segments created: {}".format(report.get("counts", {}).get("conduit_segments_created", 0)))
    print("- Pipe types found: {}".format(report.get("counts", {}).get("pipe_types_found", 0)))
    print("- Piping system types found: {}".format(report.get("counts", {}).get("piping_system_types_found", 0)))
    print("- Conduit types found: {}".format(report.get("counts", {}).get("conduit_types_found", 0)))
    print("- Loaded family symbols: {}".format(report.get("counts", {}).get("family_symbols_loaded", 0)))
    print("- Device category counts: {}".format(report.get("device_category_counts", {})))
    print("- MEP report: `{}`".format(path))
    for warning in report.get("warnings", []):
        print("- **Review:** {}".format(warning))


def run_mep_generation_with_transaction():
    import Autodesk.Revit.DB as DB
    if not forms.alert(
        "Create starter plumbing pipe and electrical conduit routes from existing model devices?\n\n"
        "This creates model elements in the active RVT. It does NOT automatically create fixtures, "
        "fittings, circuits, engineered sizing, or verified connector connections. Review routes before issue.",
        title="MEP Generation — Confirm",
        yes=True, no=True
    ):
        return None
    report = None
    # Preview source categories before entering a write transaction.
    plumbing_count = len(_all_elements_matching_category_tokens(
        ("OST_PlumbingFixtures", "plumbing fixture")))
    electrical_count = len(_all_elements_matching_category_tokens(
        ("OST_LightingFixtures", "OST_ElectricalFixtures", "lighting fixture", "electrical fixture")))
    if plumbing_count == 0 and electrical_count == 0:
        report = {
            "created_pipe_ids": [], "created_conduit_ids": [],
            "counts": {"plumbing_devices_found": 0, "electrical_devices_found": 0,
                       "pipe_segments_created": 0, "conduit_segments_created": 0},
            "warnings": [
                "No placed plumbing fixtures, lights or electrical fixtures detected.",
                "Load and PLACE suitable connector-enabled families on Ground and Second Floor.",
                "Run again after verifying Pipe Types, Piping System Types and Conduit Types."
            ]
        }
        report_path = write_mep_generation_report(report)
        print_mep_generation_report(report, report_path)
        forms.alert("No placed MEP fixtures detected. No model elements were changed.\\n"
                    "See report: {}".format(report_path), title="MEP Setup Required")
        return report
    tx = DB.Transaction(doc, "Revit Scanner — MEP Routes")
    try:
        tx.Start()
        report = run_mep_generation()
        tx.Commit()
    except Exception:
        try:
            if tx.HasStarted():
                tx.RollBack()
        except Exception:
            pass
        raise
    report_path = write_mep_generation_report(report)
    print_mep_generation_report(report, report_path)
    forms.alert(
        "MEP routing attempt finished.\n\nPipe segments: {}\nConduit segments: {}\n\n"
        "Check warnings in the output and report:\n{}".format(
            len(report.get("created_pipe_ids", [])),
            len(report.get("created_conduit_ids", [])), report_path),
        title="MEP Generation Complete"
    )
    return report


def print_required_mep_setup():
    print("")
    print("### Required Revit setup for MEP generation")
    print("- **Pipe Types:** domestic water and sanitary/waste pipe types.")
    print("- **Piping System Types:** domestic/cold-water supply and sanitary/waste systems.")
    print("- **Conduit Types:** at least one electrical conduit type.")
    print("- **Existing placed families:** Plumbing Fixtures (WC, lavatory, sink, shower as applicable), Lighting Fixtures, Electrical Fixtures (outlets/switches), and Electrical Equipment (panel board).")
    print("- **Levels:** ground-floor and second-floor Levels must exist and be assigned to elements.")
    print("- **Recommended connectors:** MEP connectors configured correctly in each family; hosted families must be placed on valid hosts.")
    print("- **Important:** this version generates preliminary geometric pipe/conduit segments from existing devices. It does not create elbows/tees, auto-connect fittings, electrical circuits, cable/wire, calculations, fixture placements, or certified code-compliant designs.")
    print("- **Configuration:** `{}`".format(os.path.join(REPORT_DIR, "mep_config.json")))


def main():
    if doc is None:
        forms.alert("Open a Revit project before running this script.", title="BIM Audit")
        return
    if not forms.alert(
            "Run a read-only full-model health and clash audit?\n\n"
            "This creates JSON, CSV, and BCF reports but does not change the Revit model.",
            title="Model Health & Clash Audit", yes=True, no=True):
        return

    timestamp = now_iso()
    model_name = doc.Title or "Untitled"
    doc_key = "{}|{}".format(model_name, getattr(doc, "PathName", "") or "unsaved")
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = "audit_{}_{}".format(safe_name(model_name), run_id)
    json_path = os.path.join(REPORT_DIR, base + ".json")
    csv_path = os.path.join(REPORT_DIR, base + "_clashes.csv")
    bcf_path = os.path.join(REPORT_DIR, base + "_clashes.bcf")

    try:
        elements = all_model_elements()
        clashes = detect_clashes(elements, CONFIG)
        standards = collect_standards(elements, CONFIG)
        warnings = flatten_warnings(clashes, standards)
        previous = load_previous_audit(doc_key)
        trends = compare_history(warnings, previous)
        score = health_score(clashes, standards, CONFIG)
        old_score = previous.get("health_score") if previous else None
        delta = score - old_score if old_score is not None else 0
        breakdowns = breakdown_tables(elements, clashes)
        fixes = fix_first(clashes, standards, CONFIG)

        # Persist Approved statuses from the previous run for matching clash IDs.
        resolved_clashes = []
        if previous:
            previous_clashes = {c.get("clash_id"): c for c in previous.get("clashes", [])}
            for clash in clashes:
                old = previous_clashes.get(clash["clash_id"])
                if old and old.get("status") == "Approved":
                    clash["status"] = "Approved"
                elif old:
                    clash["status"] = "Active"
                else:
                    clash["status"] = "New"
            current_ids = set(c.get("clash_id") for c in clashes)
            for old in previous_clashes.values():
                if old.get("clash_id") not in current_ids:
                    resolved = dict(old)
                    resolved["status"] = "Resolved"
                    resolved_clashes.append(resolved)
        else:
            for clash in clashes:
                clash["status"] = "New"

        export_clashes_csv(clashes, csv_path)
        export_bcf21(clashes, bcf_path, model_name)

        data = {
            "schema_version": "2.0",
            "timestamp": timestamp,
            "user": os.environ.get("USERNAME") or os.environ.get("USER") or "unknown",
            "model_name": model_name,
            "document_key": doc_key,
            "health_score": score,
            "previous_health_score": old_score,
            "health_score_delta": delta,
            "clashes": clashes,
            "resolved_clashes": resolved_clashes,
            "standards": standards,
            "warnings": warnings,
            "warning_trends": trends,
            "fix_first": fixes,
            "breakdowns": breakdowns,
            "config_path": CONFIG_PATH,
            "exports": {"JSON": json_path, "CSV": csv_path, "BCF 2.1": bcf_path}
        }
        with open_text_compat(json_path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=True, default=str)
        print_report(data)
        forms.alert(
            "Audit complete.\n\nHealth Score: {}/100 (delta {:+})\nHard clashes: {}\nClearance clashes: {}\n\nReports saved in:\n{}".format(
                score, delta, sum(1 for c in clashes if c["kind"] == "Hard"),
                sum(1 for c in clashes if c["kind"] == "Clearance"), REPORT_DIR),
            title="BIM Audit Complete"
        )
    except Exception as ex:
        error_path = os.path.join(REPORT_DIR, "audit_error_{}.txt".format(run_id))
        with open_text_compat(error_path, "w") as f:
            f.write(str(ex) + "\n\n" + traceback.format_exc())
        forms.alert("Audit failed:\n{}\n\nError log:\n{}".format(ex, error_path), title="BIM Audit Error")


if __name__ == "__main__":
    main()
