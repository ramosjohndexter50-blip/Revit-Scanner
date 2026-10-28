# -*- coding: utf-8 -*-
"""Dimensions & Tags command: read-only scans or explicitly reviewed model edits."""
from __future__ import unicode_literals
from pyrevit import DB, forms, revit
from hamdan_annotations import (DIMENSION_FIELDS, dimension_snapshot, dimension_changes, apply_dimensions,
                                tag_snapshot, tag_changes, apply_tags)
from hamdan_document_checks import collect
from hamdan_editing import id_value
from hamdan_elements import safe_name, text_type
from hamdan_ui import Row, TableWindow


class ViewOption(forms.TemplateListItem):
    @property
    def name(self):
        v = self.item
        return '{} | {} | ID {}'.format(v.Name, v.ViewType, v.Id)


def pick_annotations(cls):
    scope = forms.CommandSwitchWindow.show(['Current selection', 'Active view', 'Choose views'],
                                          message='Hamdan - Choose annotation edit scope')
    if not scope:
        return []
    if scope == 'Current selection':
        candidates = [revit.doc.GetElement(v) for v in revit.uidoc.Selection.GetElementIds()]
        return [e for e in candidates if isinstance(e, cls)]
    if scope == 'Active view':
        views = [revit.doc.ActiveView]
    else:
        choices = [ViewOption(v) for v in collect(DB, revit.doc, cls=DB.View)
                   if not v.IsTemplate and v.ViewType in (DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan,
                   DB.ViewType.EngineeringPlan, DB.ViewType.Section, DB.ViewType.Elevation, DB.ViewType.Detail, DB.ViewType.ThreeD)]
        selected = forms.SelectFromList.show(choices, title='Choose annotation views', multiselect=True, button_name='Open editor')
        if not selected:
            return []
        views = [v.item if isinstance(v, ViewOption) else v for v in selected]
    found = {}
    for view in views:
        for element in collect(DB, revit.doc, cls=cls, view=view):
            found[element.UniqueId] = element
    return sorted(found.values(), key=lambda e: id_value(e.Id))


def view_label(element):
    return safe_name(revit.doc.GetElement(element.OwnerViewId)) or 'Owner view unavailable'


def measured_label(element, part):
    if part.Value is None:
        return 'Unavailable'
    angular = element.DimensionType.StyleType == DB.DimensionStyleType.Angular
    spec = DB.SpecTypeId.Angle if angular else DB.SpecTypeId.Length
    return DB.UnitFormatUtils.Format(revit.doc.GetUnits(), spec, float(part.Value), False)


def edit_dimensions():
    dimensions = pick_annotations(DB.Dimension)
    rows, skipped = [], []
    for element in dimensions:
        try:
            if isinstance(element, DB.SpotDimension) or element.Pinned or element.GroupId != DB.ElementId.InvalidElementId:
                raise ValueError('Spot, pinned or grouped dimension')
            snapshot = dimension_snapshot(element)
            parts = list(element.Segments) if element.NumberOfSegments else [element]
            for index, part in enumerate(parts):
                values = dict((f, snapshot['parts'][index][f]) for f in DIMENSION_FIELDS)
                rows.append(Row(Include=False, Uid=element.UniqueId, Id=id_value(element.Id), View=view_label(element),
                                Segment=index + 1, Measured=measured_label(element, part), Snapshot=snapshot, **values))
        except Exception as error:
            skipped.append('{}: {}'.format(element.Id, error))
    if not rows:
        forms.alert('No editable dimensions in this scope.\n' + '\n'.join(skipped[:8]), title='Hamdan', warn_icon=False)
        return
    def prepare(selected):
        plan = dimension_changes([{'unique_id': r.Uid, 'segment': r.Segment - 1, 'snapshot': r.Snapshot,
                                   'values': dict((f, getattr(r, f)) for f in DIMENSION_FIELDS)} for r in selected])
        labels = dict(((r.Uid, r.Segment - 1), '{} | ID {} | segment {}'.format(r.View, r.Id, r.Segment)) for r in selected)
        descriptions = []
        for item in plan:
            old = item['snapshot']['parts'][item['segment']]
            descriptions.extend(dict(Target=labels[(item['unique_id'], item['segment'])], Field=f,
                                     Old=old[f], New=value if value else '(clear; measured value restored)' if f == 'ValueOverride' else '(clear)')
                                for f, value in sorted(item['values'].items()))
        return plan, descriptions
    TableWindow('Edit Dimensions',
        'One row per dimension segment. Measured value is read-only. Blank Value override restores the measured value; it does not resize geometry. '
        'Select Use, edit text, then Review changes. {} dimensions skipped. {}'.format(len(skipped), '; '.join(skipped[:3])), rows,
        [('View', 'View', False), ('ID', 'Id', False), ('Segment', 'Segment', False), ('Measured (project units)', 'Measured', False),
         ('Value override', 'ValueOverride', True), ('Prefix', 'Prefix', True), ('Suffix', 'Suffix', True),
         ('Above', 'Above', True), ('Below', 'Below', True)],
        prepare, lambda plan: apply_dimensions(DB, revit.doc, plan)).ShowDialog()


def editable_tags():
    result, skipped = [], []
    for tag in pick_annotations(DB.IndependentTag):
        try:
            if tag.Pinned or tag.GroupId != DB.ElementId.InvalidElementId or tag.IsOrphaned:
                raise ValueError('Pinned, grouped or orphaned tag')
            if id_value(tag.MultiReferenceAnnotationId) > 0:
                raise ValueError('Tag is managed by a multi-reference annotation')
            result.append((tag, tag_snapshot(tag)))
        except Exception as error:
            skipped.append('{}: {}'.format(tag.Id, error))
    return result, skipped


def tag_type_label(element):
    # FamilySymbol.Name can be write-only through the IronPython property bridge.
    parameter = element.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
    name = parameter.AsString() if parameter else safe_name(element)
    return '{} / {}'.format(element.FamilyName, name or 'Unnamed type')


def edit_tags():
    records, skipped = editable_tags()
    if not records:
        forms.alert('No editable independent tags in this scope.\n' + '\n'.join(skipped[:8]), title='Hamdan', warn_icon=False)
        return
    common = None
    for tag, snapshot in records:
        valid = set(id_value(v) for v in tag.GetValidTypes())
        common = valid if common is None else common.intersection(valid)
    types = {}
    for value in sorted(common or []):
        item = revit.doc.GetElement(DB.ElementId(value))
        label = '{} | ID {}'.format(tag_type_label(item), value)
        types[label] = value
    choice = forms.SelectFromList.show(['Keep current tag types'] + sorted(types), title='Optional replacement type compatible with all tags', button_name='Open tag editor')
    if choice is None:
        return
    replacement = types.get(choice)
    rows = []
    for tag, snapshot in records:
        current = revit.doc.GetElement(tag.GetTypeId())
        label = tag_type_label(current)
        rows.append(Row(Include=False, Uid=tag.UniqueId, Id=id_value(tag.Id), View=view_label(tag), CurrentType=label,
                        ProposedType=choice if replacement is not None else label, TypeId=replacement if replacement is not None else snapshot['type_id'],
                        Leader='Yes' if snapshot['leader'] else 'No', Orientation=snapshot['orientation'],
                        Right='0', Up='0', Snapshot=snapshot))
    def prepare(selected):
        plan = tag_changes([{'unique_id': r.Uid, 'snapshot': r.Snapshot, 'type_id': r.TypeId,
                             'leader': r.Leader, 'orientation': r.Orientation, 'right_mm': r.Right, 'up_mm': r.Up} for r in selected])
        lookup = dict((r.Uid, r) for r in selected)
        descriptions = []
        for change in plan:
            r = lookup[change['unique_id']]
            old = r.Snapshot
            pairs = [('Tag type', r.CurrentType, r.ProposedType)] if change['type_id'] != old['type_id'] else []
            if change['leader'] != old['leader']:
                pairs.append(('Leader', 'Yes' if old['leader'] else 'No', 'Yes' if change['leader'] else 'No'))
            if change['orientation'] != old['orientation']:
                pairs.append(('Orientation', old['orientation'], change['orientation']))
            if change['right_mm'] or change['up_mm']:
                pairs.append(('Move in owner view (mm)', 'No move', '{} right, {} up'.format(change['right_mm'], change['up_mm'])))
            descriptions.extend(dict(Target='{} | ID {}'.format(r.View, r.Id), Field=f, Old=a, New=b) for f, a, b in pairs)
        return plan, descriptions
    TableWindow('Edit Tags', 'Independent tags only. Leader: Yes/No. Orientation: Horizontal/Vertical. Move right/up is relative to the owner view, measured in model millimetres; negative values move left/down. '
        'The replacement type applies only to Use rows. {} tags skipped. {}'.format(len(skipped), '; '.join(skipped[:3])), rows,
        [('View', 'View', False), ('Tag ID', 'Id', False), ('Current type', 'CurrentType', False), ('Proposed type', 'ProposedType', False),
         ('Leader', 'Leader', True), ('Orientation', 'Orientation', True), ('Move right mm', 'Right', True), ('Move up mm', 'Up', True)],
        prepare, lambda plan: apply_tags(DB, revit.doc, plan)).ShowDialog()


def edit_tagged_values(parameters_callback):
    tags = pick_annotations(DB.IndependentTag)
    targets, unavailable = {}, 0
    for tag in tags:
        try:
            for value in tag.GetTaggedLocalElementIds():
                element = revit.doc.GetElement(value)
                if element is not None:
                    targets[element.UniqueId] = element
            if not tag.GetTaggedLocalElementIds().Count:
                unavailable += 1
        except Exception:
            unavailable += 1
    if not targets:
        forms.alert('No local tagged elements in this scope. Linked-model values must be edited in their source document.', title='Hamdan', warn_icon=False)
        return
    parameters_callback(list(targets.values()),
        'Tagged values: choose the exact instance/type parameter used by your tag family label. All tags displaying that parameter will update. '
        '{} linked/unavailable tags excluded.'.format(unavailable))


def annotation_menu(scan_callback, parameters_callback):
    action = forms.CommandSwitchWindow.show(['Scan dimensions & tags', 'Edit Dimensions', 'Edit Tags', 'Edit Tagged Values'], message='Hamdan - Dimensions & Tags')
    if action == 'Scan dimensions & tags':
        scan_callback()
    elif action == 'Edit Dimensions':
        edit_dimensions()
    elif action == 'Edit Tags':
        edit_tags()
    elif action == 'Edit Tagged Values':
        edit_tagged_values(parameters_callback)
