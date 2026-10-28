# -*- coding: utf-8 -*-
"""Annotation text and tag appearance edits with stable targets and stale-preview checks."""
from __future__ import unicode_literals
import math
from hamdan_editing import atomic_edit, id_value
from hamdan_elements import text_type

DIMENSION_FIELDS = ('ValueOverride', 'Prefix', 'Suffix', 'Above', 'Below')


def version(element):
    return text_type(element.VersionGuid)


def dimension_snapshot(element):
    parts = list(element.Segments) if element.NumberOfSegments else [element]
    return {'version': version(element), 'type_id': id_value(element.GetTypeId()),
            'view_id': id_value(element.OwnerViewId), 'segment_count': element.NumberOfSegments,
            'parts': [dict([(field, text_type(getattr(p, field) or '')) for field in DIMENSION_FIELDS] +
                           [('measured', None if p.Value is None else float(p.Value))]) for p in parts]}


def dimension_changes(rows):
    """Rows target a zero-based segment; a single-part dimension uses index zero."""
    changes, seen = [], set()
    for row in rows:
        key = (row['unique_id'], row['segment'])
        if key in seen:
            raise ValueError('The same dimension segment appears twice.')
        seen.add(key)
        before = row['snapshot']['parts'][row['segment']]
        values = dict((field, row['values'][field]) for field in DIMENSION_FIELDS if row['values'][field] != before[field])
        if values:
            changes.append(dict(row, values=values))
    return changes


def apply_dimensions(DB, document, changes):
    if len(set((r['unique_id'], r['segment']) for r in changes)) != len(changes):
        raise ValueError('Duplicate dimension segment targets.')
    def prepare():
        ready = []
        for row in changes:
            element = document.GetElement(row['unique_id'])
            if element is None or not isinstance(element, DB.Dimension):
                raise ValueError('A dimension was removed. Reopen the editor.')
            if dimension_snapshot(element) != row['snapshot']:
                raise ValueError('A dimension changed since preview. Reopen the editor.')
            if element.Pinned or element.GroupId != DB.ElementId.InvalidElementId:
                raise ValueError('Unpin or edit the dimension group explicitly before using this editor.')
            parts = list(element.Segments) if element.NumberOfSegments else [element]
            index = row['segment']
            if not 0 <= index < len(parts) or any(f not in DIMENSION_FIELDS for f in row['values']):
                raise ValueError('Invalid dimension segment or text field.')
            ready.append((parts[index], row['values']))
        return ready
    def apply(ready):
        for part, values in ready:
            for field, value in values.items():
                # Empty text restores the real measured dimension when clearing ValueOverride.
                setattr(part, field, value)
        return len(ready)
    return atomic_edit(DB, document, 'Hamdan - Edit dimension text', prepare, apply)


def tag_snapshot(tag):
    point = tag.TagHeadPosition
    refs = sorted((id_value(r.LinkInstanceId), id_value(r.LinkedElementId), id_value(r.HostElementId))
                  for r in tag.GetTaggedElementIds())
    return {'version': version(tag), 'type_id': id_value(tag.GetTypeId()), 'view_id': id_value(tag.OwnerViewId),
            'leader': bool(tag.HasLeader), 'orientation': text_type(tag.TagOrientation),
            'point': [point.X, point.Y, point.Z], 'references': refs}


def leader_value(value):
    value = text_type(value).strip().lower()
    if value in ('yes', 'true', '1'):
        return True
    if value in ('no', 'false', '0'):
        return False
    raise ValueError('Leader must be Yes or No.')


def offset_value(value):
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError('Tag offsets must be numbers in millimetres.')
    if math.isnan(result) or math.isinf(result):
        raise ValueError('Tag offsets must be finite numbers.')
    return result


def tag_changes(rows):
    result, seen = [], set()
    for row in rows:
        if row['unique_id'] in seen:
            raise ValueError('Duplicate tag target.')
        seen.add(row['unique_id'])
        old = row['snapshot']
        leader = leader_value(row['leader'])
        orientation = row['orientation'].strip()
        if orientation not in ('Horizontal', 'Vertical', old['orientation']):
            raise ValueError('Tag orientation must be Horizontal or Vertical, or keep the current value.')
        right, up = offset_value(row['right_mm']), offset_value(row['up_mm'])
        if row['type_id'] != old['type_id'] or leader != old['leader'] or orientation != old['orientation'] or right or up:
            result.append(dict(row, leader=leader, orientation=orientation, right_mm=right, up_mm=up))
    return result


def apply_tags(DB, document, changes):
    if len(set(r['unique_id'] for r in changes)) != len(changes):
        raise ValueError('Duplicate tag targets.')
    def prepare():
        ready = []
        for row in changes:
            tag = document.GetElement(row['unique_id'])
            if tag is None or not isinstance(tag, DB.IndependentTag):
                raise ValueError('A tag was removed. Reopen the editor.')
            if tag_snapshot(tag) != row['snapshot']:
                raise ValueError('A tag changed since preview. Reopen the editor.')
            if tag.Pinned or tag.GroupId != DB.ElementId.InvalidElementId or tag.IsOrphaned:
                raise ValueError('Pinned, grouped or orphaned tags cannot be edited here.')
            valid = dict((id_value(v), v) for v in tag.GetValidTypes())
            if row['type_id'] != row['snapshot']['type_id'] and row['type_id'] not in valid:
                raise ValueError('The chosen tag type is not compatible with every selected tag.')
            view = document.GetElement(tag.OwnerViewId)
            if view is None or view.IsTemplate:
                raise ValueError('The tag owner view is unavailable.')
            ready.append((tag, view, row, valid))
        return ready
    def apply(ready):
        for tag, view, row, valid in ready:
            old = row['snapshot']
            if row['type_id'] != old['type_id']:
                replacement = tag.ChangeTypeId(valid[row['type_id']])
                if replacement is not None and replacement != DB.ElementId.InvalidElementId:
                    tag = document.GetElement(replacement)
            if row['leader'] != old['leader']:
                tag.HasLeader = row['leader']
            if row['orientation'] != old['orientation']:
                tag.TagOrientation = getattr(DB.TagOrientation, row['orientation'])
            if row['right_mm'] or row['up_mm']:
                delta = view.RightDirection.Multiply(row['right_mm'] / 304.8).Add(view.UpDirection.Multiply(row['up_mm'] / 304.8))
                tag.TagHeadPosition = DB.XYZ(*old['point']).Add(delta)
        return len(ready)
    return atomic_edit(DB, document, 'Hamdan - Edit tags', prepare, apply)
