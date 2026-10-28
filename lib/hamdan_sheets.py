# -*- coding: utf-8 -*-
"""Sheet creation plans use the same formatter and conflict rules as renumbering."""
from __future__ import unicode_literals
from hamdan_numbering import integer_value, plan_numbers, find_conflicts, validate_template


def plan_sheets(existing, count, pattern, name_pattern, start=1, step=1, floor='', tower=''):
    count = integer_value(count, 'Count')
    if not 1 <= count <= 500:
        raise ValueError('Create between 1 and 500 sheets per batch.')
    validate_template(name_pattern)
    records = [{'id': i + 1, 'unique_id': 'new-sheet-' + str(i), 'group': 'Sheets', 'name': '', 'old': ''}
               for i in range(count)]
    plan = plan_numbers(records, pattern, start, step, floor, tower, 'Element ID')
    for row in plan:
        name = name_pattern.format(n=row['sequence'], floor=floor, tower=tower, old='', name='').strip()
        if not name or any(ord(char) < 32 for char in name):
            raise ValueError('Sheet names must be non-empty and contain no line breaks.')
        row['name'] = name
    conflicts = find_conflicts(plan, existing)
    if conflicts:
        raise ValueError('\n'.join(conflicts[:5]))
    return plan


def create_batch(DB, document, plan, titleblock_id):
    """One atomic transaction; no partial batch after an assignment failure."""
    transaction = DB.Transaction(document, 'Hamdan - Create sheets')
    created = []
    try:
        transaction.Start()
        for row in plan:
            sheet = DB.ViewSheet.Create(document, titleblock_id)
            sheet.SheetNumber = row['new']
            sheet.Name = row['name']
            created.append(sheet)
        if transaction.Commit() != DB.TransactionStatus.Committed:
            raise RuntimeError('Revit did not confirm sheet creation.')
        return created
    except Exception:
        if transaction.GetStatus() == DB.TransactionStatus.Started:
            transaction.RollBack()
        raise
    finally:
        transaction.Dispose()
