# -*- coding: utf-8 -*-
"""Safe, portable sheet/room number formatting and conflict checks."""
from __future__ import unicode_literals
import re
import string

try:
    text_type = unicode
except NameError:
    text_type = str


def validate_template(pattern):
    if not pattern or not pattern.strip():
        raise ValueError('Enter a format, for example {floor}{tower}-{n:02}.')
    for literal, field, spec, conversion in string.Formatter().parse(pattern):
        if field is None:
            continue
        if field not in ('n', 'floor', 'tower', 'old', 'name'):
            raise ValueError('Allowed fields: {n}, {floor}, {tower}, {old}, {name}.')
        if conversion:
            raise ValueError('Field conversions are not supported.')
        if spec:
            if field != 'n' or not re.match(r'^0[1-9][0-9]?d?$', spec):
                raise ValueError('Use a number format such as {n:02} or {n:03}.')
            if int(spec.rstrip('d')) > 12:
                raise ValueError('Number padding must not exceed 12 digits.')


def integer_value(value, label):
    text = text_type(value).strip()
    if not re.match(r'^[+-]?[0-9]+$', text):
        raise ValueError('{} must be a whole number.'.format(label))
    return int(text)


def natural_key(value):
    return tuple((0, int(part)) if part.isdigit() else (1, part.lower())
                 for part in re.split(r'([0-9]+)', text_type(value)))


def plan_numbers(records, pattern, start=1, step=1, floor='', tower='', order='Current number'):
    pattern = text_type(pattern)
    validate_template(pattern)
    start, step = integer_value(start, 'Start'), integer_value(step, 'Increment')
    if start < 0 or step == 0:
        raise ValueError('Start must be zero or greater; increment cannot be zero.')
    field = {'Current number': 'old', 'Name': 'name', 'Element ID': 'id'}[order]
    ordered = sorted(records, key=lambda row: (natural_key(row.get(field, '')), row['id']))
    plans, seen = [], set()
    for index, row in enumerate(ordered):
        number = start + (index * step)
        if number < 0:
            raise ValueError('The sequence would produce a negative number. Adjust Start or Increment.')
        value = pattern.format(n=number, floor=floor, tower=tower, old=row.get('old', ''), name=row.get('name', '')).strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError('Generated numbers must be non-empty and contain no line breaks or control characters.')
        key = (row.get('group', ''), value.lower())
        if key in seen:
            raise ValueError('Duplicate output: {}. Include {n} or another unique field.'.format(value, n='{n}'))
        seen.add(key)
        result = dict(row)
        result.update({'new': value, 'sequence': number, 'position': index + 1})
        plans.append(result)
    return plans


def find_conflicts(plan, existing):
    selected = set(row['unique_id'] for row in plan)
    occupied = {}
    for row in existing:
        if row['unique_id'] not in selected:
            occupied.setdefault((row.get('group', ''), text_type(row.get('old', '')).lower()), []).append(row)
    conflicts = []
    for row in plan:
        others = occupied.get((row.get('group', ''), row['new'].lower()), [])
        if others:
            conflicts.append('{} is already used by element {}.'.format(row['new'], others[0]['id']))
    return conflicts


def apply_assignments(plan, resolve, assign, temporary_prefix):
    """Two passes support swaps; caller must wrap this in one Revit transaction."""
    elements = []
    for row in plan:
        element = resolve(row['unique_id'])
        if element is None:
            raise ValueError('Element {} is no longer available.'.format(row['id']))
        elements.append((element, row))
    for index, (element, row) in enumerate(elements):
        assign(element, '{}-{}'.format(temporary_prefix, index))
    for element, row in elements:
        assign(element, row['new'])
