# -*- coding: utf-8 -*-
"""Read-only Revit clash engine: native broad phase, transformed solids, explicit coverage."""
from __future__ import unicode_literals
from collections import OrderedDict
from hamdan_audit import stable_key
from hamdan_scan import pair_rules, in_level_band
from hamdan_elements import describe_element


def id_value(value):
    try:
        return int(value.Value)
    except Exception:
        return int(value.IntegerValue)


def transformed_box(DB, box, transform):
    points = [transform.OfPoint(DB.XYZ(x, y, z))
              for x in (box[0], box[3]) for y in (box[1], box[4]) for z in (box[2], box[5])]
    return [min(getattr(p, axis) for p in points) for axis in ('X', 'Y', 'Z')] + [
            max(getattr(p, axis) for p in points) for axis in ('X', 'Y', 'Z')]


class ClashEngine(object):
    def __init__(self, DB, document, config, tick, ignore, diagnostics):
        self.DB, self.document, self.config = DB, document, config
        self.tick, self.ignore, self.diagnostics = tick, ignore, diagnostics
        self.solids = OrderedDict()
        self.cache_limit = max(8, int(config.get('geometry_cache_elements', 128)))
        self.stats = {'candidate_pairs': 0, 'solid_pairs_tested': 0, 'skipped_geometry': 0,
                      'unloaded_links': 0, 'unavailable_link_documents': 0, 'errors': 0, 'sources': 0}

    def note(self, kind, message, element=None, source=None):
        self.stats[kind] = self.stats.get(kind, 0) + 1
        if len(self.diagnostics) < 500:
            self.diagnostics.append({'type': kind, 'message': message,
                'element_id': id_value(element.Id) if element is not None else None,
                'model': source['document'].Title if source else self.document.Title})

    def sources(self):
        DB = self.DB
        result = [{'document': self.document, 'transform': DB.Transform.Identity, 'key': 'host', 'root_link': None}]
        if not self.config.get('include_linked_models', True):
            return result

        def visit(parent, depth, ancestors):
            links = DB.FilteredElementCollector(parent['document']).OfClass(DB.RevitLinkInstance)
            try:
                for link in links:
                    self.tick()
                    linked = link.GetLinkDocument()
                    if linked is None:
                        loaded = DB.RevitLinkType.IsLoaded(parent['document'], link.GetTypeId())
                        kind = 'unavailable_link_documents' if loaded else 'unloaded_links'
                        message = ('Link type is loaded, but Revit did not expose its document; contents were not scanned.'
                                   if loaded else 'Linked model is unloaded; contents were not scanned.')
                        self.note(kind, message, link, parent)
                        continue
                    if depth >= 8 or linked.GetHashCode() in ancestors:
                        self.note('errors', 'Nested link cycle/depth limit; source omitted.', link, parent)
                        continue
                    source = {'document': linked,
                        'transform': parent['transform'].Multiply(link.GetTransform()),
                        'key': parent['key'] + '/' + link.UniqueId,
                        'root_link': parent['root_link'] if parent['root_link'] is not None else link}
                    result.append(source)
                    visit(source, depth + 1, ancestors + [linked.GetHashCode()])
            finally:
                links.Dispose()
        visit(result[0], 0, [self.document.GetHashCode()])
        return result

    def bbox(self, element, source):
        box = element.get_BoundingBox(None)
        if box is None:
            return None
        # Respect BoundingBoxXYZ.Transform as well as each link-instance transform.
        transform = source['transform'].Multiply(box.Transform)
        return transformed_box(self.DB, [box.Min.X, box.Min.Y, box.Min.Z, box.Max.X, box.Max.Y, box.Max.Z], transform)

    def geometry(self, element, source):
        key = (source['key'], element.UniqueId)
        if key in self.solids:
            # Promote the first operand so loading the second cannot evict/dispose it.
            cached = self.solids.pop(key)
            self.solids[key] = cached
            return cached
        DB, solids = self.DB, []
        options = DB.Options()
        options.DetailLevel = DB.ViewDetailLevel.Fine
        options.IncludeNonVisibleObjects = False
        options.ComputeReferences = False

        def visit(geometry):
            if geometry is None:
                return
            for item in geometry:
                self.tick()
                if isinstance(item, DB.Solid) and item.Faces.Size and item.Volume > 1e-12:
                    solids.append(DB.SolidUtils.CreateTransformed(item, source['transform']))
                elif isinstance(item, DB.GeometryInstance):
                    visit(item.GetInstanceGeometry())
        try:
            visit(element.get_Geometry(options))
        except Exception as error:
            self.note('errors', 'Geometry read failed: {}'.format(error), element, source)
        finally:
            options.Dispose()
        if not solids:
            self.note('skipped_geometry', 'No accessible positive-volume solids; not classified as clash-free.', element, source)
        self.solids[key] = solids
        if len(self.solids) > self.cache_limit:
            _, expired = self.solids.popitem(last=False)
            for solid in expired:
                solid.Dispose()
        return solids

    def record(self, element, source, box):
        DB, document = self.DB, source['document']
        row = describe_element(document, element, name_getter=lambda item: DB.Element.Name.GetValue(item))
        row.update({'category': str(element.Category.BuiltInCategory), 'bbox_internal': box,
                    'model_name': document.Title, 'source_key': source['key']})
        if source['root_link'] is not None:
            row.update({'linked_element_id': row['id'], 'linked_unique_id': row['unique_id'],
                        'link_instance_unique_id': source['key'],
                        'id': id_value(source['root_link'].Id), 'unique_id': source['root_link'].UniqueId})
        return row

    def classify(self, a, sa, ba, b, sb, bb, rule):
        gap2 = sum(max(0, ba[i] - bb[i + 3], bb[i] - ba[i + 3]) ** 2 for i in range(3))
        overlap = all(min(ba[i + 3], bb[i + 3]) > max(ba[i], bb[i]) for i in range(3))
        if overlap and rule['hard']:
            # This native filter is a useful host-model hint, but does not support every solid category.
            if sa['key'] == sb['key'] == 'host':
                native = None
                try:
                    native = self.DB.ElementIntersectsElementFilter(a)
                    native.PassesFilter(self.document, b.Id)
                except Exception:
                    pass  # Boolean solid testing remains authoritative.
                finally:
                    if native is not None:
                        native.Dispose()
            first = list(self.geometry(a, sa))
            second = list(self.geometry(b, sb))
            if not first or not second:
                return None
            volume = 0.0
            for x in first:
                for y in second:
                    self.tick()
                    intersection = None
                    try:
                        intersection = self.DB.BooleanOperationsUtils.ExecuteBooleanOperation(
                            x, y, self.DB.BooleanOperationsType.Intersect)
                        self.stats['solid_pairs_tested'] += 1
                        volume += max(0.0, intersection.Volume)
                    except Exception as error:
                        self.note('errors', 'Solid intersection failed: {}'.format(error), a, sa)
                    finally:
                        if intersection is not None:
                            intersection.Dispose()
            if volume > max(1e-12, float(self.config.get('minimum_clash_volume_mm3', 1.0)) / (304.8 ** 3)):
                return 'Hard', 'Solid intersection', volume
        tolerance = rule.get('clearance_mm')
        if tolerance is not None and tolerance > 0 and gap2 <= (float(tolerance) / 304.8) ** 2:
            return 'Clearance', 'Bounding-box clearance candidate', 0.0
        return None

    def run(self, band=None):
        DB = self.DB
        sources, findings, seen = self.sources(), [], set()
        self.stats['sources'] = len(sources)
        try:
            for rule in pair_rules(self.config):
                ca, cb = rule['categories']
                self.tick('Clashes: {} / {}'.format(ca.replace('OST_', ''), cb.replace('OST_', '')))
                if not hasattr(DB.BuiltInCategory, ca) or not hasattr(DB.BuiltInCategory, cb):
                    self.note('errors', 'Unknown configured category pair: {} / {}'.format(ca, cb))
                    continue
                for sa in sources:
                    collector = DB.FilteredElementCollector(sa['document']).OfCategory(getattr(DB.BuiltInCategory, ca)).WhereElementIsNotElementType()
                    try:
                        for a in collector:
                            self.tick()
                            ba = self.bbox(a, sa)
                            if not ba:
                                self.note('skipped_geometry', 'No model bounding box; element omitted.', a, sa)
                                continue
                            if not in_level_band(ba, band):
                                continue
                            for sb in sources:
                                tolerance = float(rule.get('clearance_mm') or 0) / 304.8
                                expanded = [ba[i] - tolerance for i in range(3)] + [ba[i + 3] + tolerance for i in range(3)]
                                query = transformed_box(DB, expanded, sb['transform'].Inverse)
                                outline = DB.Outline(DB.XYZ(*query[:3]), DB.XYZ(*query[3:]))
                                prefilter = DB.BoundingBoxIntersectsFilter(outline)
                                nearby = DB.FilteredElementCollector(sb['document']).OfCategory(getattr(DB.BuiltInCategory, cb)).WhereElementIsNotElementType().WherePasses(prefilter)
                                try:
                                    for b in nearby:
                                        self.tick()
                                        ka, kb = (sa['key'], a.UniqueId), (sb['key'], b.UniqueId)
                                        key = tuple(sorted((ka, kb)))
                                        if ka == kb or key in seen:
                                            continue
                                        seen.add(key)
                                        bb = self.bbox(b, sb)
                                        if not bb:
                                            self.note('skipped_geometry', 'No model bounding box; element omitted.', b, sb)
                                            continue
                                        if not in_level_band(bb, band):
                                            continue
                                        # Group/host identifiers only have meaning inside the same document instance.
                                        same_source = sa['key'] == sb['key']
                                        if self.ignore(a, b, self.config, same_source):
                                            continue
                                        self.stats['candidate_pairs'] += 1
                                        hit = self.classify(a, sa, ba, b, sb, bb, rule)
                                        if hit is None:
                                            continue
                                        kind, evidence, volume = hit
                                        findings.append({'clash_id': stable_key(key), 'kind': kind, 'status': 'Active',
                                            'evidence': evidence, 'element_a': self.record(a, sa, ba),
                                            'element_b': self.record(b, sb, bb), 'overlap_volume_internal': volume,
                                            'point_internal': [(max(ba[i], bb[i]) + min(ba[i + 3], bb[i + 3])) / 2 for i in range(3)],
                                            'tolerance_mm': rule.get('clearance_mm') or 0,
                                            'message': '{}: {} [{}] / {} [{}]'.format(kind, sa['document'].Title,
                                                id_value(a.Id), sb['document'].Title, id_value(b.Id))})
                                finally:
                                    nearby.Dispose()
                                    prefilter.Dispose()
                                    outline.Dispose()
                    finally:
                        collector.Dispose()
        finally:
            for solids in self.solids.values():
                for solid in solids:
                    solid.Dispose()
            self.solids.clear()
        self.stats['complete'] = not any(self.stats[k] for k in (
            'errors', 'skipped_geometry', 'unloaded_links', 'unavailable_link_documents'))
        return findings
