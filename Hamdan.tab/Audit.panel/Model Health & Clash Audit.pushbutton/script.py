# -*- coding: utf-8 -*-
"""
Hamdan — Model Health & Clash Audit (v3)
Target: pyRevit IronPython 2.7-compatible syntax, Revit API (verify against installed Revit version)
Purpose: audit the active Revit model and export JSON history, CSV clashes,
BCF 2.1 viewpoints, standards checks, health score, and prioritized fixes.

IMPORTANT:
- Audit/report command: geometry reads only; sheet creation is a separate command.
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
from xml.sax.saxutils import escape as html_escape

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


from pyrevit import revit, forms, script, DB
from Autodesk.Revit.DB import (
    BuiltInCategory, BuiltInParameter, ElementId, FilteredElementCollector,
    FamilyInstance, IndependentTag, Level, RevitLinkInstance, View3D,
    ViewType, XYZ, BoundingBoxXYZ, WorksetKind, WorksetId,
    WorksetTable, WorksetVisibility, UnitUtils, BoundingBoxIntersectsFilter, Outline, Element
)
from System.Diagnostics import Process
from System import Int64
from hamdan_issues import attach_references, build_issues, model_identity, identity_matches
from hamdan_elements import describe_element, text_type, collect_rooms
from hamdan_audit import warning_occurrence_key, stable_key, identity_key, penalty_rows, clash_history
from hamdan_exports import write_bcf21, write_clash_csv
from hamdan_clashes import ClashEngine
from hamdan_profiles import effective_config
from hamdan_tracking import overlay_issues
from hamdan_session import begin_scan, publish_scan
from hamdan_scan import (PRESETS, ScanCancelled, ScanProgress, pair_rules, preset_config,
                         scope_record, in_level_band)

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()
SCAN_PROGRESS = None
CHECK_RESULTS = {}
SCAN_DIAGNOSTICS = []
CLASH_COVERAGE = {}


def scan_tick(label=None, current=None, total=None):
    if SCAN_PROGRESS is not None:
        SCAN_PROGRESS.pulse(label, current, total)

SCRIPT_DIR = os.path.dirname(__file__)
REPORT_DIR = os.path.join(os.path.expanduser("~"), "Documents", "RevitScanner", "reports")
CONFIG_PATH = os.path.join(REPORT_DIR, "audit_config.json")
makedirs_compat(REPORT_DIR)

from hamdan_config import DEFAULT_CONFIG


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
    try:
        p = get_param(element, name)
        if p is None or not p.HasValue:
            if name == 'Level':
                level = doc.GetElement(element.LevelId)
                return Element.Name.GetValue(level) if level is not None else ''
            return ''
        storage = p.StorageType.ToString()
        if storage == 'String':
            return p.AsString() or ''
        if storage == 'ElementId':
            eid = p.AsElementId()
            if element_id_value(eid) == -1:
                # A wall's unconnected top is a valid negative built-in constraint.
                return p.AsValueString() or ('Unconnected' if name == 'Top Constraint' else '')
            if element_id_value(eid) < 0:
                return p.AsValueString() or ''
            referenced = doc.GetElement(eid)
            return Element.Name.GetValue(referenced) if referenced is not None else ''
        return p.AsValueString() or text_type(p.AsDouble() if storage == 'Double' else p.AsInteger())
    except Exception as error:
        if len(SCAN_DIAGNOSTICS) < 500:
            SCAN_DIAGNOSTICS.append({'type': 'parameter_read', 'element_id': element_id_value(element.Id), 'message': str(error)})
        return ''

def get_param(element, name):
    builtins = {'Mark': 'ALL_MODEL_MARK', 'Type Mark': 'ALL_MODEL_TYPE_MARK',
        'Type': 'ELEM_TYPE_PARAM', 'Level': 'FAMILY_LEVEL_PARAM', 'Reference Level': 'INSTANCE_REFERENCE_LEVEL_PARAM',
        'Base Constraint': 'WALL_BASE_CONSTRAINT', 'Top Constraint': 'WALL_HEIGHT_TYPE',
        'Base Level': 'FAMILY_BASE_LEVEL_PARAM', 'Top Level': 'FAMILY_TOP_LEVEL_PARAM'}
    try:
        spec = name if isinstance(name, dict) else {'name': name, 'builtin': builtins.get(name)}
        scope = spec.get('scope', 'either')
        if scope not in ('instance', 'type', 'either'):
            raise ValueError('Parameter scope must be instance, type or either.')
        targets = [element] if scope != 'type' else []
        if scope != 'instance':
            type_element = doc.GetElement(element.GetTypeId())
            if type_element is not None:
                targets.append(type_element)
        for target in targets:
            parameter = None
            if spec.get('guid'):
                from System import Guid
                parameter = target.get_Parameter(Guid(spec['guid']))
            elif spec.get('builtin'):
                parameter = target.get_Parameter(getattr(BuiltInParameter, spec['builtin']))
            if parameter is None and spec.get('name') and not spec.get('guid'):
                parameter = target.LookupParameter(spec['name'])
            if parameter is not None:
                return parameter
    except Exception as error:
        if len(SCAN_DIAGNOSTICS) < 500:
            SCAN_DIAGNOSTICS.append({'type': 'parameter_read', 'element_id': element_id_value(element.Id), 'message': str(error)})
    return None


def get_bbox(element):
    try:
        box = element.get_BoundingBox(None)
        if box is None:
            return None
        return (box.Min.X, box.Min.Y, box.Min.Z, box.Max.X, box.Max.Y, box.Max.Z)
    except Exception:
        return None


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
    record = describe_element(doc, element, name_getter=lambda item: Element.Name.GetValue(item),
                              level_getter=get_level_name)
    record.update({'category': category_bic_name(element), 'workset': get_workset_name(element),
                   'bbox_internal': list(bbox) if bbox else None})
    return record


def describe_review_element(value):
    scan_tick()
    element = doc.GetElement(ElementId(Int64(value)))
    return get_element_record(element) if element is not None else None


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
    return isinstance(element, FamilyInstance) and element.SuperComponent is not None

def load_config():
    if not os.path.exists(CONFIG_PATH):
        with open_text_compat(CONFIG_PATH, "w") as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
    if doc is None:
        return dict(DEFAULT_CONFIG)
    return effective_config(REPORT_DIR, model_identity(doc, Process.GetCurrentProcess().Id))


CONFIG = dict(DEFAULT_CONFIG)


def all_model_elements():
    import time
    elements = []
    started = time.time()
    errors = 0
    collector = FilteredElementCollector(doc).WhereElementIsNotElementType()
    total = collector.GetElementCount()
    try:
        for index, element in enumerate(collector):
            if index % 100 == 0:
                scan_tick(current=index, total=total)
            try:
                if element.Category and not element.ViewSpecific:
                    elements.append(element)
            except Exception as error:
                errors += 1
                SCAN_DIAGNOSTICS.append({'type': 'inventory_access_error', 'message': str(error)})
    finally:
        collector.Dispose()
    CHECK_RESULTS['inventory'] = {'status': 'INCOMPLETE' if errors else 'PASS', 'seconds': round(time.time() - started, 3),
                                  'message': '{} categorized, non-view-specific host records; {} inaccessible.'.format(len(elements), errors)}
    return elements


def element_is_ignored_pair(a, b, config, same_source=True):
    if config.get('ignore_nested_or_shared_family_components') and (is_nested_family(a) or is_nested_family(b)):
        return True
    if not same_source:
        return False
    # Only suppress the actual host relationship, not every pair involving a hosted fixture.
    if config.get('ignore_hosted_elements'):
        for child, parent in ((a, b), (b, a)):
            if isinstance(child, FamilyInstance) and child.Host is not None and child.Host.Id == parent.Id:
                return True
    if config.get('ignore_same_group') and get_group_id(a) is not None and get_group_id(a) == get_group_id(b):
        return True
    if config.get('ignore_same_assembly') and get_assembly_id(a) is not None and get_assembly_id(a) == get_assembly_id(b):
        return True
    return False

def detect_clashes(config, band=None):
    import time
    started = time.time()
    engine = ClashEngine(DB, doc, config, scan_tick, element_is_ignored_pair, SCAN_DIAGNOSTICS)
    try:
        clashes = engine.run(band)
        CHECK_RESULTS['clashes'] = {'status': 'PASS' if engine.stats['complete'] else 'INCOMPLETE',
                                   'seconds': round(time.time() - started, 3), 'message': 'See geometry coverage and scan notes.'}
    except Exception as error:
        engine.stats['complete'] = False
        CHECK_RESULTS['clashes'] = {'status': 'FAILED', 'seconds': round(time.time() - started, 3), 'message': str(error)}
        SCAN_DIAGNOSTICS.append({'type': 'clash_check_error', 'message': str(error)})
        clashes = []
    CLASH_COVERAGE.update(engine.stats)
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
            scan_tick()
            try:
                if key == "views" and (item.IsTemplate or isinstance(item, DB.ViewSheet)):
                    continue
                name = item.SheetNumber if key == 'sheets' else item.Name
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
            for ws in DB.FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset):
                if ws.Kind == WorksetKind.UserWorkset and not pat.match(ws.Name or ""):
                    issues.append({"type": "naming", "rule": "worksets", "name": ws.Name,
                                   "message": "Workset name does not match configured regex"})
    except Exception as error:
        issues.append({'type': 'workset_naming_error', 'message': str(error)})
    return issues


def required_parameter_check(elements, config):
    results = []
    rules = config.get("required_parameters", {})
    by_cat = {}
    for el in elements:
        scan_tick()
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
            scan_tick()
            for pname in names:
                p = get_param(el, pname)
                value = get_param_text(el, pname)
                has_value = bool(value and value.strip())
                if has_value:
                    filled += 1
                else:
                    missing.append({"element_id": element_id_value(el.Id), "category": cat,
                                    "element_name": str(getattr(el, "Name", "")),
                                    "parameter": pname.get("name", pname.get("builtin", pname.get("guid", ""))) if isinstance(pname, dict) else pname})
        results.append({
            "category": cat, "element_count": len(items), "required_count": len(names),
            "filled_percent": round((filled / float(total)) * 100.0, 2) if total else 100.0,
            "missing": missing
        })
    return results


def workset_health():
    if not doc.IsWorkshared:
        return [{'type': 'workset_info', 'message': 'Model is not workshared; workset checks are not applicable.'}]
    issues, counts, owned_self, owned_other = [], {}, 0, 0
    worksets = list(DB.FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset))
    collector = FilteredElementCollector(doc).WhereElementIsNotElementType()
    try:
        for element in collector:
            scan_tick()
            try:
                key = element_id_value(element.WorksetId)
                counts[key] = counts.get(key, 0) + 1
                status = DB.WorksharingUtils.GetCheckoutStatus(doc, element.Id)
                owned_self += int(status == DB.CheckoutStatus.OwnedByCurrentUser)
                owned_other += int(status == DB.CheckoutStatus.OwnedByOtherUser)
            except Exception as error:
                issues.append({'type': 'workset_check_error', 'element_id': element_id_value(element.Id), 'message': str(error)})
    finally:
        collector.Dispose()
    for ws in worksets:
        count = counts.get(element_id_value(ws.Id), 0)
        if ws.Name.lower() == 'workset1' and count:
            issues.append({'type': 'workset_default', 'workset': ws.Name, 'count': count,
                           'message': '{} elements on default Workset1; review against the BEP.'.format(count)})
        if not ws.IsOpen:
            issues.append({'type': 'workset_closed_info', 'workset': ws.Name,
                           'message': 'Workset is closed; its unloaded contents were not audited.'})
    issues.append({'type': 'ownership_info', 'owned_by_current_user': owned_self, 'owned_by_other_user': owned_other,
                   'message': 'Checkout ownership: {} current user, {} other users (local cached status).'.format(owned_self, owned_other)})
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
                    "type": "link_positioning_info",
                    "element_id": element_id_value(link.Id),
                    "link_name": link.Name,
                    "pinned": bool(link.Pinned),
                    "transform_origin_internal": [transform.Origin.X, transform.Origin.Y, transform.Origin.Z],
                    "message": "Verify link positioning mode and shared-coordinate coordination manually."
                })
            except Exception as link_ex:
                issues.append({"type": "link_check_error", "element_id": element_id_value(link.Id),
                               "message": str(link_ex)})
    except Exception as ex:
        issues.append({"type": "link_check_error", "message": str(ex)})
    return issues


def unused_and_performance_checks(config):
    issues = []
    for view in FilteredElementCollector(doc).OfClass(View3D):
        scan_tick()
        try:
            if not view.IsTemplate and not view.IsSectionBoxActive:
                issues.append({'type': 'performance', 'element_id': element_id_value(view.Id),
                               'message': "3D view '{}' has no section box; review its intended use.".format(view.Name)})
        except Exception as error:
            issues.append({'type': 'performance_error', 'message': str(error)})
    issues.append({'type': 'performance_info', 'message':
        'Family file size, nested-family depth and purgeability were not measured; they require a separate content audit.'})
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
    warnings = doc.GetWarnings()
    for warning in warnings:
        scan_tick()
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
                "count": 0, "element_ids": [], "occurrences": []
            })
            failing = [element_id_value(v) for v in warning.GetFailingElements()]
            additional = [element_id_value(v) for v in warning.GetAdditionalElements()]
            item['occurrences'].append({'key': warning_occurrence_key(failure_id, description, failing, additional),
                                        'element_ids': failing, 'additional_ids': additional})
            item["count"] += 1
            for element_id in warning.GetFailingElements():
                value = element_id_value(element_id)
                if value >= 0 and value not in item["element_ids"]:
                    item["element_ids"].append(value)
        except Exception as error:
            raise RuntimeError('Could not read native warning: {}'.format(error))
    return sorted(grouped.values(), key=lambda item: (-item["count"], item["description"]))


def model_health_inventory(elements):
    """Collect practical project-health metrics without editing the model."""
    result = {"issues": [], "check_errors": [], "links": [], "unplaced_rooms": [], "unenclosed_rooms": [], "unplaced_views": [],
              "imported_cad_elements": []}
    path = getattr(doc, "PathName", "") or ""
    result["model_path"] = path
    result["workshared"] = bool(getattr(doc, "IsWorkshared", False))
    try:
        result["file_size_mb"] = round(os.path.getsize(path) / (1024.0 * 1024.0), 2) if path and os.path.exists(path) else None
    except Exception:
        result["file_size_mb"] = None

    try:
        links = FilteredElementCollector(doc).OfClass(RevitLinkInstance).ToElements()
        for link in links:
            try:
                result["links"].append({"id": element_id_value(link.Id), "name": link.Name,
                                        "loaded": bool(DB.RevitLinkType.IsLoaded(doc, link.GetTypeId())),
                                        "document_available": link.GetLinkDocument() is not None})
            except Exception as error:
                result['check_errors'].append({'type': 'inventory_error', 'message': str(error)})
    except Exception as error:
        result['check_errors'].append({'type': 'inventory_error', 'message': str(error)})

    try:
        result['workset_count'] = len(list(DB.FilteredWorksetCollector(doc).OfKind(WorksetKind.UserWorkset))) if doc.IsWorkshared else 0
        result['central_model_path'] = (DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(doc.GetWorksharingCentralModelPath())
                                        if doc.IsWorkshared else '')
        sheets = list(FilteredElementCollector(doc).OfClass(DB.ViewSheet))
        result['sheet_count'] = len([sheet for sheet in sheets if not sheet.IsPlaceholder])
        result['view_count'] = len([v for v in FilteredElementCollector(doc).OfClass(DB.View) if not v.IsTemplate])
        result['in_place_family_count'] = len([f for f in FilteredElementCollector(doc).OfClass(DB.Family) if f.IsInPlace])
        result['design_option_count'] = FilteredElementCollector(doc).OfClass(DB.DesignOption).GetElementCount()
        result['model_group_count'] = FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_IOSModelGroups).WhereElementIsNotElementType().GetElementCount()
        result['detail_group_count'] = FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_IOSDetailGroups).WhereElementIsNotElementType().GetElementCount()
    except Exception as error:
        result['check_errors'].append({'type': 'inventory_error', 'message': 'Model metadata: {}'.format(error)})

    try:
        rooms = collect_rooms(DB, doc)
        result["room_count"] = len(rooms)
        for room in rooms:
            scan_tick()
            if room.Location is None:
                result["unplaced_rooms"].append(element_id_value(room.Id))
            elif room.Area <= 0:
                result["unenclosed_rooms"].append(element_id_value(room.Id))
        if result["unplaced_rooms"]:
            result["issues"].append({"type": "unplaced_rooms", "severity": "MEDIUM", "count": len(result["unplaced_rooms"]),
                                     "message": "{} room(s) are unplaced.".format(len(result["unplaced_rooms"]))})
        if result['unenclosed_rooms']:
            result['issues'].append({'type': 'unenclosed_rooms', 'severity': 'MEDIUM', 'count': len(result['unenclosed_rooms']),
                                     'message': '{} placed room(s) have zero area; review enclosure/redundancy.'.format(len(result['unenclosed_rooms']))})
    except Exception as error:
        result['room_count'] = None
        result['check_errors'].append({'type': 'inventory_error', 'message': 'Rooms: {}'.format(error)})

    try:
        view_class = __import__("Autodesk.Revit.DB", fromlist=["View"]).View
        sheet_class = __import__("Autodesk.Revit.DB", fromlist=["ViewSheet"]).ViewSheet
        placed = set()
        for sheet in FilteredElementCollector(doc).OfClass(sheet_class).ToElements():
            scan_tick()
            for view_id in sheet.GetAllPlacedViews():
                placed.add(element_id_value(view_id))
        for instance in FilteredElementCollector(doc).OfClass(DB.ScheduleSheetInstance):
            if not instance.IsTitleblockRevisionSchedule:
                placed.add(element_id_value(instance.ScheduleId))
        for view in FilteredElementCollector(doc).OfClass(view_class).ToElements():
            scan_tick()
            try:
                if not view.IsTemplate and not isinstance(view, DB.ViewSheet) and view.CanBePrinted and element_id_value(view.Id) not in placed:
                    result["unplaced_views"].append({"id": element_id_value(view.Id), "name": view.Name})
            except Exception as error:
                result['check_errors'].append({'type': 'inventory_error', 'message': str(error)})
        if result["unplaced_views"]:
            result["issues"].append({"type": "unplaced_views", "severity": "LOW", "count": len(result["unplaced_views"]),
                                     "message": "{} printable view(s) are not placed on sheets.".format(len(result["unplaced_views"]))})
    except Exception as error:
        result['check_errors'].append({'type': 'inventory_error', 'message': str(error)})

    try:
        import_class = __import__("Autodesk.Revit.DB", fromlist=["ImportInstance"]).ImportInstance
        imports = FilteredElementCollector(doc).OfClass(import_class).ToElements()
        result["imported_cad_count"] = len([item for item in imports if not getattr(item, "IsLinked", False)])
        result["linked_cad_count"] = len([item for item in imports if getattr(item, "IsLinked", False)])
        result["imported_cad_elements"] = [{"id": element_id_value(item.Id), "name": item.Name}
                                            for item in imports if not getattr(item, "IsLinked", False)]
        if result["imported_cad_count"]:
            result["issues"].append({"type": "imported_cad", "severity": "MEDIUM", "count": result["imported_cad_count"],
                                     "message": "{} imported CAD instance(s) found; review whether linking is more appropriate.".format(result["imported_cad_count"])})
    except Exception as error:
        result['check_errors'].append({'type': 'inventory_error', 'message': 'CAD: {}'.format(error)})
    return result


def collect_standards(elements, config):
    import time
    result = {}
    checks = [('revit_warnings', 'Revit warnings', document_warnings),
        ('model_health', 'Model health', lambda: model_health_inventory(elements)),
        ('naming_issues', 'Naming rules', lambda: validate_naming(config)),
        ('required_parameters', 'Required parameters', lambda: required_parameter_check(elements, config)),
        ('workset_issues', 'Worksets', workset_health),
        ('link_and_view_issues', 'Links and views', link_coordination_checks),
        ('performance_flags', 'Performance checks', lambda: unused_and_performance_checks(config))]
    for index, (key, label, check) in enumerate(checks):
        scan_tick(label, index, len(checks))
        started = time.time()
        notes_before = len(SCAN_DIAGNOSTICS)
        try:
            result[key] = check()
            entries = result[key] if isinstance(result[key], list) else result[key].get('check_errors', [])
            errors = [i for i in entries if i.get('type', '').endswith('_error')]
            closed = any(i.get('type') == 'workset_closed_info' for i in entries)
            closed = closed or len(SCAN_DIAGNOSTICS) > notes_before
            CHECK_RESULTS[key] = {'status': 'FAILED' if errors else 'INCOMPLETE' if closed else 'PASS',
                                  'seconds': round(time.time() - started, 3),
                                  'message': '; '.join(i.get('message', '') for i in errors[:5])}
        except Exception as error:
            result[key] = {} if key == 'model_health' else []
            CHECK_RESULTS[key] = {'status': 'FAILED', 'seconds': round(time.time() - started, 3), 'message': str(error)}
            SCAN_DIAGNOSTICS.append({'type': 'check_error', 'check': key, 'message': str(error)})
    return result

def flatten_warnings(clashes, standards):
    warnings = []
    for c in clashes:
        warnings.append({"key": "clash:" + c["clash_id"], "kind": "clash", "severity": c["kind"],
                         "status": c.get("status", "Active"), "message": c["message"]})
    for issue in standards.get('revit_warnings', []):
        for occurrence in issue.get('occurrences', []):
            warnings.append({'key': occurrence['key'], 'kind': 'revit_warning', 'severity': issue['severity'],
                             'status': 'Active', 'message': issue['description'], 'element_ids': occurrence['element_ids']})
    for issue in standards.get("naming_issues", []):
        warnings.append({"key": "naming:{}:{}".format(issue.get("rule"), issue.get("element_id", issue.get("name"))),
                         "kind": "warning", "severity": "Low", "status": "Active", "message": issue["message"]})
    for cat in standards.get("required_parameters", []):
        for missing in cat.get("missing", []):
            warnings.append({"key": "param:{}:{}:{}".format(missing["category"], missing["element_id"], missing["parameter"]),
                             "kind": "warning", "severity": "Low", "status": "Active",
                             "message": "Missing {} on element {}".format(missing["parameter"], missing["element_id"])})
    for group in ("workset_issues", "link_and_view_issues", "performance_flags"):
        for issue in standards.get(group, []):
            if issue.get("type", "").endswith("_info"):
                continue
            warnings.append({"key": "{}:{}:{}".format(group, issue.get("type"), issue.get("element_id", stable_key([issue.get("element_a"), issue.get("element_b")]) if "element_a" in issue else issue.get("workset", issue.get("message", "")))),
                             "kind": "warning", "severity": "Medium", "status": "Active",
                             "message": issue.get("message", str(issue))})
    for issue in standards.get("model_health", {}).get("issues", []):
        if issue.get("type") == "duplicate_marks":
            continue
        warnings.append({"key": "health:{}".format(issue.get("type")), "kind": "model_health",
                         "severity": issue.get("severity", "MEDIUM"), "status": "Active",
                         "message": issue.get("message", str(issue))})
    return warnings


def previous_audit_files():
    files = []
    for name in os.listdir(REPORT_DIR):
        if name.startswith("audit_") and name.endswith(".json") and name != os.path.basename(CONFIG_PATH):
            path = os.path.join(REPORT_DIR, name)
            files.append(path)
    return sorted(files, key=lambda p: os.path.getmtime(p), reverse=True)


def load_previous_audit(current_doc_key, scan_scope=None):
    for path in previous_audit_files():
        try:
            with open_text_compat(path, "r") as f:
                data = json.load(f)
            if data.get("document_key") == current_doc_key and data.get("schema_version") in ("3.0", "3.1") and data.get("audit_complete", False) and data.get('scan_scope') == scan_scope:
                return data
        except Exception:
            continue
    return None


def compare_history(current_warnings, previous, complete=True):
    previous_map = {w.get("key"): w for w in (previous or {}).get("warnings", [])}
    current_map = {w.get("key"): w for w in current_warnings}
    changes = []
    for key, warning in current_map.items():
        state = "UNCHANGED" if key in previous_map else "NEW"
        changes.append(dict(warning, trend=state))
    for key, warning in previous_map.items():
        if complete and key not in current_map:
            changes.append(dict(warning, trend="RESOLVED", status="Resolved"))
    return changes


def health_score(clashes, standards, config):
    if any(row.get('status') in ('FAILED', 'INCOMPLETE') for row in CHECK_RESULTS.values()):
        return None
    return max(0, min(100, 100 - sum(row['impact'] for row in penalty_rows(clashes, standards, config))))

def fix_first(clashes, standards, config):
    return penalty_rows(clashes, standards, config)[:10]

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
    per_level, per_discipline, per_category = {}, {}, {}
    for el in elements:
        scan_tick()
        level = get_level_name(el)
        cat = category_bic_name(el)
        per_level.setdefault(level, {"elements": 0, "hard_clashes": 0, "clearance_clashes": 0})
        per_level[level]["elements"] += 1
        disc = discipline_name(cat)
        per_discipline.setdefault(disc, {"elements": 0, "hard_clashes": 0, "clearance_clashes": 0})
        per_discipline[disc]["elements"] += 1
        category = per_category.setdefault(cat or category_key(el), {
            'name': category_key(el), 'group': disc, 'records': 0, 'samples': []})
        category['records'] += 1
        if len(category['samples']) < 5:
            try:
                category['samples'].append(describe_element(doc, el,
                    name_getter=lambda item: Element.Name.GetValue(item)))
            except Exception:
                category['samples'].append({'id': element_id_value(el.Id), 'display_name': 'Name unavailable'})
    for c in clashes:
        for rec in (c["element_a"], c["element_b"]):
            level = rec.get("level", "Unassigned")
            disc = discipline_name(rec.get("category", ""))
            key = "hard_clashes" if c["kind"] == "Hard" else "clearance_clashes"
            per_level.setdefault(level, {"elements": 0, "hard_clashes": 0, "clearance_clashes": 0})[key] += 1
            per_discipline.setdefault(disc, {"elements": 0, "hard_clashes": 0, "clearance_clashes": 0})[key] += 1
    return {"per_level": per_level, "per_discipline": per_discipline, 'per_category': per_category}


def export_clashes_csv(clashes, path):
    write_clash_csv(clashes, path, scan_tick)

def export_bcf21(clashes, path, project_name):
    write_bcf21(clashes, path, project_name, scan_tick, os.environ.get('USERNAME') or 'Hamdan')

def report_file_link(path):
    return "file:///" + path.replace("\\", "/").replace(" ", "%20")


def html_table(headers, rows):
    head = "".join("<th>{}</th>".format(html_escape(text_type(value))) for value in headers)
    body = []
    for row in rows:
        body.append("<tr>{}</tr>".format("".join(
            "<td>{}</td>".format(html_escape(text_type(value))) for value in row
        )))
    return "<table><thead><tr>{}</tr></thead><tbody>{}</tbody></table>".format(head, "".join(body))


def export_html_dashboard(data, path):
    """Write a self-contained local dashboard; no network or API is used."""
    clashes = data.get("clashes", [])
    trends = data.get("warning_trends", [])
    hard = sum(1 for clash in clashes if clash.get("kind") == "Hard")
    clearance = sum(1 for clash in clashes if clash.get("kind") == "Clearance")
    trend_counts = {
        "NEW": sum(1 for item in trends if item.get("trend") == "NEW"),
        "RESOLVED": sum(1 for item in trends if item.get("trend") == "RESOLVED"),
        "UNCHANGED": sum(1 for item in trends if item.get("trend") == "UNCHANGED")
    }
    discipline_rows = []
    for name, row in sorted(data.get("breakdowns", {}).get("per_discipline", {}).items()):
        discipline_rows.append((name, row.get("elements", 0), row.get("hard_clashes", 0), row.get("clearance_clashes", 0)))
    level_rows = []
    for name, row in sorted(data.get("breakdowns", {}).get("per_level", {}).items()):
        level_rows.append((name, row.get("elements", 0), row.get("hard_clashes", 0), row.get("clearance_clashes", 0)))
    fix_rows = []
    for index, item in enumerate(data.get("fix_first", []), 1):
        fix_rows.append((index, item.get("title", ""), item.get("count", 0), item.get("impact", 0), item.get("details", "")))
    export_links = []
    for label, export_path in sorted(data.get("exports", {}).items()):
        export_links.append('<a class="export" href="{}">{}</a>'.format(report_file_link(export_path), html_escape(label)))
    score = data.get('health_score')
    thresholds = data.get('score_thresholds', {'pass': 80, 'warning': 60})
    score_class = 'warn' if score is None else ('good' if score >= thresholds['pass'] else 'warn' if score >= thresholds['warning'] else 'bad')
    score_label = 'Incomplete' if score is None else '{:g}/100'.format(score)
    score_status = 'INCOMPLETE' if score is None else ('PASS' if score >= thresholds['pass'] else 'WARNING' if score >= thresholds['warning'] else 'FAIL')
    health = data.get("standards", {}).get("model_health", {})
    loaded_links = len([item for item in health.get("links", []) if item.get("loaded")])
    health_rows = [
        ("Workshared", "Yes" if health.get("workshared") else "No"),
        ('Worksets / central path', '{} | {}'.format(health.get('workset_count', 'Unavailable'), health.get('central_model_path', ''))),
        ('Sheets / views', '{} / {}'.format(health.get('sheet_count', 'Unavailable'), health.get('view_count', 'Unavailable'))),
        ('In-place families / design options', '{} / {}'.format(health.get('in_place_family_count', 'Unavailable'), health.get('design_option_count', 'Unavailable'))),
        ('Model groups / detail groups', '{} / {}'.format(health.get('model_group_count', 'Unavailable'), health.get('detail_group_count', 'Unavailable'))),
        ('Placed rooms with zero area', len(health.get('unenclosed_rooms', []))),
        ("File size", "{} MB".format(health.get("file_size_mb")) if health.get("file_size_mb") is not None else "Unsaved / unavailable"),
        ("Revit links loaded", "{} / {}".format(loaded_links, len(health.get("links", [])))),
        ("Rooms / unplaced", "{} / {}".format(health.get("room_count", 0), len(health.get("unplaced_rooms", [])))),
        ("Unplaced printable views", health.get("unplaced_views", []) and len(health.get("unplaced_views", [])) or 0),
        ("Imported CAD / linked CAD", "{} / {}".format(health.get("imported_cad_count", 0), health.get("linked_cad_count", 0)))
    ]
    scope = data.get('scan_scope', {})
    mode = scope.get('mode', 'Full Audit')
    scope_label = '{} | {} | {}'.format(mode, scope.get('preset', 'Configured pairs'), scope.get('level_name', 'All levels'))
    if mode == 'Quick Scan':
        hard, clearance = 'Not scanned', 'Not scanned'
    warning_count = 'Not scanned' if mode == 'Clash Scan' else len(data.get('standards', {}).get('revit_warnings', []))
    elapsed = int(data.get('elapsed_seconds', 0))
    delta_label = ('{:+g}'.format(data['health_score_delta']) if data.get('health_score_delta') is not None
                   else 'No comparable complete score')
    fresh = data.get('history_mode') == 'fresh'
    score_note = 'Fresh scan | No previous comparison' if fresh else 'Delta: ' + delta_label
    scan_summary = (html_table(['Warnings found', 'Clash findings', 'Previous results reused'],
                              [(len(data.get('warnings', [])), len(clashes), 'No')]) if fresh else
                    html_table(['New', 'Resolved', 'Unchanged'], [(trend_counts['NEW'], trend_counts['RESOLVED'], trend_counts['UNCHANGED'])]))
    if mode == 'Quick Scan':
        group_table = html_table(['Category group', 'Active-model records'],
                                [(row[0], row[1]) for row in discipline_rows])
        by_level_table = html_table(['Level', 'Active-model records'], [(row[0], row[1]) for row in level_rows])
    else:
        group_table = html_table(['Category group', 'Records', 'Hard involvements', 'Clearance involvements'], discipline_rows)
        by_level_table = html_table(['Level', 'Records', 'Hard involvements', 'Clearance involvements'], level_rows)
    category_rows = []
    for key, item in sorted(data.get('breakdowns', {}).get('per_category', {}).items()):
        names = u'; '.join(u'{} [ID {}]'.format(ref.get('display_name', ''), ref['id'])
                           for ref in item.get('samples', []))
        category_rows.append((item['name'], key, item['group'], item['records'], names))
    categories_table = (html_table(['Revit category', 'Category key', 'Group', 'Records', 'Sample names / IDs (up to 5)'], category_rows)
                        if category_rows else 'Run a new scan to record the category breakdown.')
    source_note = ('Source: active RVT records only. Revit link instances: {} ({} loaded). '.format(
        len(health.get('links', [])), loaded_links) if mode != 'Clash Scan' else 'Clash sources: host and configured loaded link instances. ')
    source_note += 'Groups such as Electrical are inferred from Revit categories; they do not indicate linked disciplines.'
    html = """<!doctype html>
<html><head><meta charset="utf-8"><title>Hamdan Audit</title><style>
body{{font-family:Segoe UI,Arial,sans-serif;margin:0;background:#f3f6fa;color:#1d2939}}.top{{background:#111c2b;color:white;padding:28px 42px}}.top h1{{margin:0;font-size:28px}}.top p{{margin:7px 0 0;color:#b9c6d8}}.wrap{{max-width:1420px;margin:24px auto;padding:0 24px}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(180px,1fr));gap:16px}}.card,section{{background:white;border:1px solid #e4eaf2;border-radius:12px;padding:20px;box-shadow:0 3px 12px #172b4d0d}}.metric{{font-size:32px;font-weight:700;margin-top:9px}}.label{{color:#667085;font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:.04em}}.good{{color:#039855}}.warn{{color:#dc6803}}.bad{{color:#d92d20}}.two{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}}h2{{font-size:18px;margin:0 0 14px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th{{text-align:left;background:#f8fafc;color:#475467}}td,th{{padding:10px;border-bottom:1px solid #eaecf0;vertical-align:top}}.export{{display:inline-block;margin:4px 8px 4px 0;padding:9px 12px;border-radius:7px;background:#e8f1ff;color:#175cd3;text-decoration:none;font-weight:600}}.note{{font-size:12px;color:#667085;margin-top:14px}}.footer{{color:#667085;padding:25px 0;font-size:12px}}@media(max-width:850px){{.grid,.two{{grid-template-columns:1fr}}}}</style></head>
<body><header class="top"><h1>Hamdan</h1><p>Model Health &amp; Clash Audit &middot; {model} &middot; {timestamp}</p></header><main class="wrap">
<section style="margin-bottom:18px"><h2>{scope_label}</h2><p class="note">Elapsed: {elapsed_label}. Results apply to this scan scope only. Level scans include elements crossing the selected level band. Clash-only breakdowns cover the detected candidates.</p></section>
<section style="margin-bottom:18px"><h2>Scan Coverage</h2><p>BEP profile: {bep_profile}</p>{coverage_summary}<p class="note">Counts describe this scan scope. Geometry counters count processing events/pairs, not a percentage of the whole model. Quick Scan does not test clashes. Dimension/tag and sheet-layout checks are separate commands with their own selected scope.</p></section>
<div class="grid"><div class="card"><div class="label">Scan Health Score</div><div class="metric {score_class}">{score_label}</div><div class="note">{score_status} | {score_note}</div></div><div class="card"><div class="label">Hard Clashes</div><div class="metric bad">{hard}</div></div><div class="card"><div class="label">Clearance Candidates</div><div class="metric warn">{clearance}</div></div><div class="card"><div class="label">Revit Warning Groups</div><div class="metric">{warnings}</div></div></div>
<div class="two"><section><h2>{scan_heading}</h2>{trend_table}</section><section><h2>Report Files</h2>{exports}<p class="note">Files contain this scan only. Saved exports are not restored automatically in a new scan or after reopening a model; clash scans also export CSV and BCF.</p></section></div>
<div class="two"><section><h2>Top 10 Fix First</h2>{fix_table}</section><section><h2>Active-model Category Groups</h2><p class="note">{source_note}</p>{discipline_table}</section></div>
<div class="two"><section><h2>Model Health Inventory</h2>{health_table}</section><section><h2>Active-model Level Breakdown</h2>{level_table}</section></div>
<section style="margin-top:18px;overflow:auto"><h2>Category Breakdown</h2><p class="note">These counts show categorized records in the current model, not linked model contents or exclusively physical objects. Samples identify what each group contains.</p>{categories_table}</section>
<section style="margin-top:18px;overflow:auto"><h2>Clash Register</h2>{clash_table}</section>
<section style="margin-top:18px"><h2>Required Parameter Completeness</h2>{parameter_table}</section>
<section id="issues" style="margin-top:18px;overflow:auto"><h2>Review Issues</h2><input id="issue-search" aria-label="Search findings" placeholder="Search severity, element name, level or ID" style="padding:10px;width:90%;margin-bottom:12px"/><p class="note">In Revit, use Hamdan &gt; Audit &gt; Review Issues to select and zoom to an affected element. Search by severity, level, description or Element ID.</p>{issues_table}</section>
<section style="margin-top:18px"><h2>Scan Notes</h2>{checks_table}{coverage_table}{diagnostics_table}<p class="note">Hard clashes require positive solid intersection. Clearance candidates require visual review and do not reduce the score. Custom duplicate-element and repeated-Mark checks are excluded from this scan. Unloaded links, unsupported geometry and failed checks are listed below. Closed worksets and unloaded links cannot be certified as clash-free.</p></section>
<div class="footer">Generated locally by Hamdan. No cloud service or AI API was used.</div></main><script>
document.getElementById('issue-search').oninput=function(){{var q=this.value.toLowerCase();document.querySelectorAll('#issues tbody tr').forEach(function(r){{r.style.display=r.textContent.toLowerCase().includes(q)?'':'none';}});}};
document.querySelectorAll('th').forEach(function(h){{h.style.cursor='pointer';h.onclick=function(){{
var t=h.closest('table'),b=t.tBodies[0],i=Array.prototype.indexOf.call(h.parentNode.children,h),asc=h.dataset.asc!=='true';
h.dataset.asc=asc;Array.from(b.rows).sort(function(a,b){{return a.cells[i].textContent.localeCompare(b.cells[i].textContent,undefined,{{numeric:true}})*(asc?1:-1);}}).forEach(function(r){{b.appendChild(r);}});}};}});
</script></body></html>""".format(
        model=html_escape(text_type(data.get("model_name", "Untitled"))), timestamp=html_escape(text_type(data.get("timestamp", ""))),
        score_label=score_label, score_status=score_status, score_class=score_class, score_note=html_escape(score_note), hard=hard,
        clearance=clearance, warnings=warning_count, scope_label=html_escape(scope_label),
        elapsed_label='{}m {:02d}s'.format(elapsed // 60, elapsed % 60),
        scan_heading='Current scan' if fresh else 'Warning Trends', trend_table=scan_summary,
        exports="".join(export_links),
        fix_table=html_table(["#", "Recommendation", "Items", "Impact", "Details"], fix_rows),
        discipline_table=group_table, level_table=by_level_table,
        source_note=html_escape(source_note), categories_table=categories_table,
        health_table=html_table(["Metric", "Result"], health_rows) if mode != 'Clash Scan' else 'Not included in this scan.',
        parameter_table=html_table(["Category", "Elements", "Filled", "Missing Values"], [
            (item.get("category", ""), item.get("element_count", 0),
             "{}%".format(item.get("filled_percent", 0)) if item.get("element_count", 0) else "N/A - no elements",
             len(item.get("missing", [])))
            for item in data.get("standards", {}).get("required_parameters", [])
        ]) if mode != 'Clash Scan' else 'Not included in this scan.',
        clash_table=html_table(['ID', 'Kind / evidence', 'Status', 'Model A / element', 'Model B / element', 'Approx. XYZ (host metres)', 'Clearance mm'], [
            (c['clash_id'], c['kind'] + ' / ' + c.get('evidence', ''), c.get('status', ''),
             '{} | {} | ID {}'.format(c['element_a'].get('model_name', ''), c['element_a'].get('display_name', ''), c['element_a'].get('linked_element_id', c['element_a'].get('id'))),
             '{} | {} | ID {}'.format(c['element_b'].get('model_name', ''), c['element_b'].get('display_name', ''), c['element_b'].get('linked_element_id', c['element_b'].get('id'))),
             ', '.join('{:.3f}'.format(float(v) * .3048) for v in c.get('point_internal', [])), c.get('tolerance_mm', 0)) for c in clashes
        ]) if mode != 'Quick Scan' else 'Not included in this scan.',
        checks_table=html_table(['Check', 'Status', 'Seconds', 'Notes'],
            [(key, row.get('status'), row.get('seconds'), row.get('message', '')) for key, row in sorted(data.get('check_results', {}).items())]),
        coverage_table=html_table(['Clash coverage', 'Result'], sorted(data.get('clash_coverage', {}).items())),
        bep_profile=html_escape(data.get('bep_profile', 'Project rules')),
        coverage_summary=html_table(['Coverage', 'Result'], [
            ('Scope', scope_label), ('Host records inventoried', data.get('inventory_records', 'Not recorded')),
            ('Checks completed', sum(1 for r in data.get('check_results', {}).values() if r.get('status') == 'PASS')),
            ('Checks failed / incomplete', sum(1 for r in data.get('check_results', {}).values() if r.get('status') in ('FAILED', 'INCOMPLETE'))),
            ('Geometry tested pairs', data.get('clash_coverage', {}).get('solid_pairs_tested', 'Not scanned')),
            ('Geometry skipped events', data.get('clash_coverage', {}).get('skipped_geometry', 'Not scanned')),
            ('Unloaded link instances', data.get('clash_coverage', {}).get('unloaded_links', 'Not scanned')),
            ('Loaded links unavailable to API', data.get('clash_coverage', {}).get('unavailable_link_documents', 'Not scanned'))]),
        diagnostics_table=html_table(['Kind', 'Element', 'Notes'],
            [(r.get('type'), r.get('element_id', ''), r.get('message', '')) for r in data.get('scan_diagnostics', [])]),
        issues_table=html_table(["Issue ID", "Severity", "Type", "Detected", "Workflow", "Assignee", "Due", "Reason", "Level", "Element IDs", "Element Names", "Finding", "Suggested Fix"], [
            (item['issue_id'], item['severity'], item['kind'], item['status'], item.get('workflow_status', item['status']),
             item.get('assignee', ''), item.get('due_date', ''), item.get('reason', ''), item['level'],
             ', '.join(str(value) for value in item['element_ids']),
             ' / '.join(ref.get('display_name') or ref.get('name', '') for ref in item.get('element_refs', [])),
             item['message'], item['suggested_fix'])
            for item in data.get('review_issues', [])
        ])
    )
    with io.open(path, "w", encoding="utf-8") as html_file:
        html_file.write(html)


def choose_scan():
    mode = forms.CommandSwitchWindow.show(
        ['Quick Scan', 'Clash Scan', 'Full Audit'],
        message='Hamdan - Choose what to scan')
    if not mode:
        return None
    config = dict(CONFIG)
    preset = 'Configured pairs'
    if mode == 'Quick Scan':
        preset = 'Health and warnings'
    level_id, level_name, band = '', 'All levels', None
    if mode == 'Clash Scan':
        preset = forms.CommandSwitchWindow.show(
            ['Configured pairs'] + sorted(PRESETS), message='Choose a discipline pair')
        if not preset:
            return None
        if preset != 'Configured pairs':
            tolerance_text = forms.ask_for_string(
                default='25', prompt='Clearance tolerance in millimetres (0 or greater).',
                title='Hamdan - Clash Scan')
            if tolerance_text is None:
                return None
            try:
                config = preset_config(CONFIG, preset, float(tolerance_text))
            except (ValueError, TypeError) as error:
                forms.alert(str(error), title='Hamdan', warn_icon=False)
                return None
        levels = sorted(list(FilteredElementCollector(doc).OfClass(Level).ToElements()),
                        key=lambda level: level.Elevation)
        choices = {'All levels': ('', 'All levels', None)}
        labels = ['All levels']
        for level in levels:
            label = '{} | {:.2f} m | ID {}'.format(
                level.Name, level.Elevation * 0.3048, element_id_value(level.Id))
            higher = [item.Elevation for item in levels if item.Elevation > level.Elevation + 1e-6]
            choices[label] = (level.UniqueId, level.Name,
                              [level.Elevation, min(higher) if higher else None])
            labels.append(label)
        chosen = forms.SelectFromList.show(labels, title='Hamdan - Choose level',
                                           multiselect=False, button_name='Start Scan')
        if not chosen:
            return None
        level_id, level_name, band = choices[chosen]
    scope = scope_record(mode, config, preset, level_id, level_name, band)
    return config, scope


def run_selected_scan(config, scope):
    identity = begin_scan(doc)
    mode = scope['mode']
    CHECK_RESULTS.clear()
    SCAN_DIAGNOSTICS[:] = []
    CLASH_COVERAGE.clear()
    scan_tick('Reading model', 0, 1)
    elements = all_model_elements() if mode != 'Clash Scan' else []
    clashes = detect_clashes(config, scope['level_band_internal']) if mode != 'Quick Scan' else []
    standards = collect_standards(elements, config) if mode != 'Clash Scan' else {}
    if mode == 'Clash Scan':
        # The breakdown covers detected candidates, not a whole-model inventory.
        ids = set(record['id'] for clash in clashes for record in (clash['element_a'], clash['element_b']))
        elements = [doc.GetElement(ElementId(Int64(value))) for value in ids]
        elements = [element for element in elements if element is not None]
    scan_tick('Preparing findings', 0, 1)
    timestamp = now_iso()
    model_name = doc.Title or 'Untitled'
    doc_key = identity_key(identity)
    run_id = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    base = 'audit_{}_{}'.format(safe_name(model_name), run_id)
    json_path = os.path.join(REPORT_DIR, base + '.json')
    csv_path = os.path.join(REPORT_DIR, base + '_clashes.csv')
    bcf_path = os.path.join(REPORT_DIR, base + '_clashes.bcf')
    html_path = os.path.join(REPORT_DIR, base + '.html')
    resolved = clash_history(clashes, None, CLASH_COVERAGE.get('complete', True))
    register = {'issues': {}}
    complete = not any(r.get('status') in ('FAILED', 'INCOMPLETE') for r in CHECK_RESULTS.values())
    warnings = flatten_warnings(clashes, standards)
    score = health_score(clashes, standards, config)
    old_score = None
    data = {
        'schema_version': '3.3', 'history_mode': 'fresh', 'baseline_applied': False,
        'timestamp': timestamp, 'bep_profile': config.get('bep_profile', 'Project rules'),
        'inventory_records': len(elements) if mode != 'Clash Scan' else 'Not inventoried (clash-only scan)',
        'user': os.environ.get('USERNAME') or 'unknown', 'model_name': model_name,
        'document_key': doc_key, 'scan_scope': scope,
        'check_results': dict(CHECK_RESULTS), 'clash_coverage': dict(CLASH_COVERAGE),
        'scan_diagnostics': list(SCAN_DIAGNOSTICS),
        'score_thresholds': config.get('score_thresholds', {'pass': 80, 'warning': 60}),
        'audit_complete': complete,
        'health_score': score, 'previous_health_score': old_score,
        'health_score_delta': score - old_score if old_score is not None and score is not None else None,
        'clashes': clashes, 'resolved_clashes': resolved, 'standards': standards,
        'warnings': warnings, 'warning_trends': [],
        'fix_first': fix_first(clashes, standards, config),
        'breakdowns': breakdown_tables(elements, clashes), 'config_path': CONFIG_PATH,
        'exports': {'HTML dashboard': html_path, 'JSON results': json_path}
    }
    scan_tick('Preparing issue register', 0, 1)
    data['review_model_identity'] = identity
    data['review_issues'] = attach_references(build_issues(data), describe_review_element)
    overlay_issues(data['review_issues'], register)
    if mode != 'Quick Scan':
        scan_tick('Exporting clash CSV', 0, 1)
        export_clashes_csv(clashes, csv_path)
        scan_tick('Exporting BCF viewpoints', 0, 1)
        export_bcf21(clashes, bcf_path, model_name)
        data['exports'].update({'CSV clashes': csv_path, 'BCF 2.1': bcf_path})
    scan_tick('Saving dashboard', 0, 1)
    data['elapsed_seconds'] = round(SCAN_PROGRESS.clock() - SCAN_PROGRESS.started, 2) if SCAN_PROGRESS else 0
    export_html_dashboard(data, html_path)
    # Publish only after every report file succeeds; failed/cancelled scans stay fresh.
    with open_text_compat(json_path, 'w') as handle:
        json.dump(data, handle, indent=2, ensure_ascii=True, default=str)
    publish_scan(doc, identity, json_path)
    return html_path


def main():
    global SCAN_PROGRESS, CONFIG
    if doc is None or doc.IsFamilyDocument:
        forms.alert('Open a Revit project before scanning.', title='Hamdan', warn_icon=False)
        return
    try:
        CONFIG = load_config()
    except Exception as error:
        forms.alert('BEP configuration could not be loaded. Correct the profile before scanning.\n\n{}'.format(error), title='Hamdan', warn_icon=False)
        return
    selection = choose_scan()
    if selection is None:
        return
    config, scope = selection
    try:
        with forms.ProgressBar(title='Hamdan - Preparing scan', cancellable=True, step=1) as bar:
            SCAN_PROGRESS = ScanProgress(bar)
            html_path = run_selected_scan(config, scope)
        try:
            Process.Start('explorer.exe', html_path)
        except Exception:
            pass
        forms.alert('Complete', title='Run Audit', warn_icon=False)
    except ScanCancelled:
        forms.alert('Scan cancelled.', title='Hamdan', warn_icon=False)
    except Exception as error:
        error_path = os.path.join(REPORT_DIR, 'audit_error_{}.txt'.format(datetime.now().strftime('%Y%m%d_%H%M%S_%f')))
        with open_text_compat(error_path, 'w') as handle:
            handle.write(str(error) + '\n\n' + traceback.format_exc())
        forms.alert('Audit failed:\n{}\n\nError log:\n{}'.format(error, error_path), title='Hamdan')
    finally:
        SCAN_PROGRESS = None


if __name__ == '__main__':
    main()
