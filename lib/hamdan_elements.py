# -*- coding: utf-8 -*-
"""Readable element labels and a duplicate-free selection list (no Revit imports)."""
from __future__ import unicode_literals

try:
    text_type = unicode
except NameError:
    text_type = str


def safe_name(element, name_getter=None):
    if element is None:
        return ''
    try:
        return text_type(element.Name or '')
    except Exception:
        try:
            return text_type(name_getter(element) or '') if name_getter else ''
        except Exception:
            return ''


def describe_element(document, element, name_getter=None, level_getter=None):
    name = safe_name(element, name_getter)
    try:
        type_element = document.GetElement(element.GetTypeId())
    except Exception:
        type_element = None
    type_name = safe_name(type_element, name_getter)
    family = ''
    try:
        family = text_type(element.Symbol.Family.Name or '')
    except Exception:
        try:
            family = text_type(type_element.FamilyName or '')
        except Exception:
            pass
    label = u' — '.join(value for value in (family, type_name) if value)
    if not label:
        label = name or 'Unnamed element'
    try:
        category = text_type(element.Category.Name)
    except Exception:
        category = 'No category'
    try:
        level = safe_name(document.GetElement(element.LevelId), name_getter)
    except Exception:
        level = ''
    if not level and level_getter:
        try:
            level = level_getter(element) or ''
        except Exception:
            pass
    try:
        element_id = int(element.Id.Value)
    except Exception:
        element_id = int(element.Id.IntegerValue)
    return {'id': element_id, 'unique_id': element.UniqueId, 'name': name,
            'family': family, 'type': type_name, 'display_name': label,
            'category_name': category, 'level': level or 'Not assigned'}


class ElementListState(object):
    def __init__(self, limit=2000):
        self.rows = {}
        self.limit = limit

    def upsert(self, records):
        skipped = 0
        for record in records:
            key = record.get('unique_id')
            if not key:
                continue
            if key not in self.rows and len(self.rows) >= self.limit:
                skipped += 1
                continue
            self.rows[key] = dict(record)
        return skipped

    def visible(self, search=''):
        words = text_type(search).lower().split()
        fields = ('display_name', 'name', 'family', 'type', 'category_name', 'level', 'id')
        result = []
        for record in self.rows.values():
            text = ' '.join(text_type(record.get(key, '')) for key in fields).lower()
            if all(word in text for word in words):
                result.append(record)
        return sorted(result, key=lambda row: (row.get('display_name', '').lower(), row['id']))

    def remove(self, unique_ids):
        for key in unique_ids:
            self.rows.pop(key, None)

    def clear(self):
        self.rows.clear()
