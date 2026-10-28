# -*- coding: utf-8 -*-
"""Read-only annotation and paper-space layout checks; findings are review candidates."""
from __future__ import unicode_literals
import re
from hamdan_editing import id_value
from hamdan_elements import safe_name, text_type


def rectangle_overlap(a, b, tolerance=0):
    return min(a[2], b[2]) - max(a[0], b[0]) > tolerance and min(a[3], b[3]) - max(a[1], b[1]) > tolerance


def rectangle_outside(box, boundary, tolerance=0):
    return box[0] < boundary[0] - tolerance or box[1] < boundary[1] - tolerance or box[2] > boundary[2] + tolerance or box[3] > boundary[3] + tolerance


def collect(DB, document, cls=None, category=None, view=None):
    collector = DB.FilteredElementCollector(document, view.Id) if view is not None else DB.FilteredElementCollector(document)
    if cls is not None:
        collector = collector.OfClass(cls)
    if category is not None:
        collector = collector.OfCategory(category)
    try:
        return list(collector.WhereElementIsNotElementType().ToElements())
    finally:
        collector.Dispose()


def annotation_checks(DB, document, views, config, tick=lambda: None):
    findings, notes = [], []
    coverage = {'views_requested': len(views), 'views_scanned': 0, 'dimensions_read': 0, 'tag_targets_read': 0, 'errors': 0}
    pattern = re.compile(config.get('annotation_view_name_regex', '.*'))
    for view in views:
        tick()
        if view.IsTemplate or not pattern.search(view.Name):
            notes.append({'type': 'view_skipped', 'element_id': id_value(view.Id), 'message': 'Template or excluded by BEP view-name rule.'})
            continue
        def add(kind, severity, message, ids):
            findings.append({'type': kind, 'severity': severity, 'message': message,
                             'element_ids': ids, 'view_id': id_value(view.Id), 'view_name': view.Name})
        try:
            for dimension in collect(DB, document, cls=DB.Dimension, view=view):
                tick()
                coverage['dimensions_read'] += 1
                parts = list(dimension.Segments) if dimension.NumberOfSegments else [dimension]
                for index, part in enumerate(parts):
                    value = part.ValueOverride
                    if value is not None and text_type(value).strip():
                        add('dimension_override', 'HIGH', 'Dimension value replaced by "{}" (segment {}).'.format(value, index + 1), [id_value(dimension.Id)])
            tagged = set()
            for tag in collect(DB, document, cls=DB.IndependentTag, view=view):
                tick()
                if tag.IsOrphaned:
                    add('orphaned_tag', 'HIGH', 'Tag has lost its element reference.', [id_value(tag.Id)])
                tagged.update(id_value(value) for value in tag.GetTaggedLocalElementIds())
            for category in config.get('tag_required_categories', []):
                bic = getattr(DB.BuiltInCategory, category)
                for element in collect(DB, document, category=bic, view=view):
                    tick()
                    coverage['tag_targets_read'] += 1
                    if id_value(element.Id) not in tagged:
                        add('missing_tag_candidate', 'MEDIUM', '{} has no local independent tag in this view (BEP rule {}).'.format(
                            safe_name(element), category), [id_value(element.Id)])
            coverage['views_scanned'] += 1
        except Exception as error:
            coverage['errors'] += 1
            notes.append({'type': 'annotation_error', 'element_id': id_value(view.Id), 'message': text_type(error)})
    coverage['complete'] = coverage['errors'] == 0 and coverage['views_scanned'] == coverage['views_requested']
    return {'findings': findings, 'notes': notes, 'coverage': coverage,
            'scope_note': 'Selected host views only. Missing tags are BEP candidates from view collectors, not a judgement that every visible element needs a tag. Linked elements, room/space tags and dimensions required by design intent are outside this check. Duplicate Marks are covered by Run Audit.'}


def sheet_layout_checks(DB, document, sheets, config, tick=lambda: None):
    findings, notes = [], []
    coverage = {'sheets_requested': len(sheets), 'sheets_scanned': 0, 'placement_boxes': 0, 'errors': 0, 'unbounded_sheets': 0}
    tolerance = float(config.get('sheet_layout_tolerance_mm', 2)) / 304.8
    for sheet in sheets:
        tick()
        label = '{} - {}'.format(sheet.SheetNumber, sheet.Name)
        def add(kind, severity, message, ids):
            findings.append({'type': kind, 'severity': severity, 'message': message,
                             'element_ids': ids, 'sheet_id': id_value(sheet.Id), 'sheet_name': label})
        try:
            if sheet.IsPlaceholder:
                coverage['unbounded_sheets'] += 1
                notes.append({'type': 'placeholder_skipped', 'element_id': id_value(sheet.Id), 'message': 'Placeholder sheet has no paper-space layout.'})
                continue
            titleblocks = collect(DB, document, category=DB.BuiltInCategory.OST_TitleBlocks, view=sheet)
            boundary = None
            if len(titleblocks) == 1:
                box = titleblocks[0].get_BoundingBox(sheet)
                if box is not None:
                    boundary = [box.Min.X, box.Min.Y, box.Max.X, box.Max.Y]
            if boundary is None:
                coverage['unbounded_sheets'] += 1
                add('sheet_boundary_unavailable', 'MEDIUM', 'No unique title-block bounds; outside-sheet test skipped.', [id_value(sheet.Id)])
            if not titleblocks:
                add('missing_titleblock', 'MEDIUM', 'Sheet has no title-block instance.', [id_value(sheet.Id)])
            for block in titleblocks:
                for rule in config.get('titleblock_required_parameters', []):
                    builtin = getattr(DB.BuiltInParameter, rule, None)
                    parameter = block.get_Parameter(builtin) if builtin is not None else block.LookupParameter(rule)
                    value = (parameter.AsString() if parameter.StorageType.ToString() == 'String' else parameter.AsValueString()) if parameter is not None else None
                    if not value or not text_type(value).strip():
                        add('titleblock_parameter', 'MEDIUM', 'Title-block value is missing: ' + rule, [id_value(block.Id), id_value(sheet.Id)])
            boxes = []
            for viewport_id in sheet.GetAllViewports():
                tick()
                viewport = document.GetElement(viewport_id)
                outline = viewport.GetBoxOutline()
                try:
                    lo, hi = outline.MinimumPoint, outline.MaximumPoint
                    boxes.append((id_value(viewport.Id), [lo.X, lo.Y, hi.X, hi.Y], 'Viewport'))
                finally:
                    outline.Dispose()
            for schedule in collect(DB, document, cls=DB.ScheduleSheetInstance, view=sheet):
                tick()
                if schedule.IsTitleblockRevisionSchedule:
                    continue
                box = schedule.get_BoundingBox(sheet)
                if box is not None:
                    boxes.append((id_value(schedule.Id), [box.Min.X, box.Min.Y, box.Max.X, box.Max.Y], 'Schedule'))
                else:
                    raise ValueError('Schedule bounding box unavailable: ' + text_type(schedule.Id))
            coverage['placement_boxes'] += len(boxes)
            for index, (element_id, box, kind) in enumerate(boxes):
                tick()
                if boundary is not None and rectangle_outside(box, boundary, tolerance):
                    add('outside_titleblock_bounds', 'MEDIUM', kind + ' extends beyond the title-block bounding rectangle.', [element_id, id_value(sheet.Id)])
                for other_id, other, other_kind in boxes[:index]:
                    if rectangle_overlap(box, other, tolerance):
                        add('layout_overlap_candidate', 'MEDIUM', kind + ' / ' + other_kind + ' paper-space boxes overlap; verify layout.', [element_id, other_id, id_value(sheet.Id)])
            coverage['sheets_scanned'] += 1
        except Exception as error:
            coverage['errors'] += 1
            notes.append({'type': 'layout_error', 'element_id': id_value(sheet.Id), 'message': text_type(error)})
    coverage['complete'] = coverage['errors'] == 0 and coverage['unbounded_sheets'] == 0
    return {'findings': findings, 'notes': notes, 'coverage': coverage,
            'scope_note': 'Paper-space bounding-box candidates only, not printing certification. Title-block extents may include family annotation beyond the paper. Viewport labels are excluded by GetBoxOutline; schedules are included. Insets/printable regions and intentional overlay views require visual review.'}
