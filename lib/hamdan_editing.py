# -*- coding: utf-8 -*-
"""Explicit parameter identity, stale previews and atomic Revit edits."""
from __future__ import unicode_literals
import uuid
from hamdan_elements import text_type


def id_value(value):
    try:
        return int(value.Value)
    except Exception:
        return int(value.IntegerValue)


def parameter_key(parameter):
    return ('guid:' + text_type(parameter.GUID)) if parameter.IsShared else ('id:' + text_type(id_value(parameter.Id)))


def resolve_parameter(element, key):
    matches = [p for p in element.Parameters if parameter_key(p) == key]
    if len(matches) != 1:
        raise ValueError('The parameter is missing or ambiguous on element ' + text_type(element.Id))
    return matches[0]


def parameter_snapshot(parameter):
    storage = parameter.StorageType.ToString()
    if storage == 'String':
        raw = parameter.AsString()
        display = raw or ''
    elif storage == 'Integer':
        raw = parameter.AsInteger()
        display = parameter.AsValueString() or text_type(raw)
    elif storage == 'Double':
        raw = parameter.AsDouble()
        display = parameter.AsValueString() or text_type(raw)
    else:
        raw, display = None, ''
    return {'storage': storage, 'raw': raw, 'display': display, 'has_value': bool(parameter.HasValue)}


def check_snapshot(actual, expected):
    if any(actual.get(k) != expected.get(k) for k in ('storage', 'raw', 'has_value')):
        raise ValueError('A value changed since preview. Reload and review the new values.')


def set_parameter(parameter, text):
    if parameter.IsReadOnly:
        raise ValueError('A selected parameter is read-only.')
    storage = parameter.StorageType.ToString()
    if storage == 'String':
        result = parameter.Set(text)
    elif storage == 'Integer':
        try:
            value = int(text.strip())
        except ValueError:
            raise ValueError('Integer parameters require whole numbers (Yes/No uses 1/0).')
        result = parameter.Set(value)
    elif storage == 'Double':
        if not text.strip():
            raise ValueError('Numeric values cannot be blank.')
        result = parameter.SetValueString(text)
    else:
        raise ValueError('Element reference parameters are not supported by this editor.')
    if result is False:
        raise ValueError('Revit rejected the parameter value: ' + text)


def atomic_edit(DB, document, label, prepare, apply):
    transaction = DB.Transaction(document, label)
    try:
        transaction.Start()
        if hasattr(DB, 'IFailuresPreprocessor'):
            class RollBackErrors(DB.IFailuresPreprocessor):
                def PreprocessFailures(self, accessor):
                    if any(f.GetSeverity() == DB.FailureSeverity.Error for f in accessor.GetFailureMessages()):
                        return DB.FailureProcessingResult.ProceedWithRollBack
                    return DB.FailureProcessingResult.Continue
            policy = RollBackErrors()
            options = transaction.GetFailureHandlingOptions()
            options.SetFailuresPreprocessor(policy)
            options.SetClearAfterRollback(True)
            transaction.SetFailureHandlingOptions(options)
        prepared = prepare()
        result = apply(prepared)
        if transaction.Commit() != DB.TransactionStatus.Committed:
            raise ValueError('Revit did not commit the batch. No successful result is reported.')
        return result
    except Exception:
        if transaction.GetStatus() == DB.TransactionStatus.Started:
            transaction.RollBack()
        raise
    finally:
        transaction.Dispose()


def apply_parameters(DB, document, changes):
    if len(set((r['unique_id'], r['key']) for r in changes)) != len(changes):
        raise ValueError('Duplicate parameter targets in this preview.')
    def prepare():
        prepared = []
        for row in changes:
            element = document.GetElement(row['unique_id'])
            if element is None:
                raise ValueError('An element was removed since preview.')
            parameter = resolve_parameter(element, row['key'])
            check_snapshot(parameter_snapshot(parameter), row['snapshot'])
            prepared.append((parameter, row['new']))
        return prepared
    def apply(prepared):
        for parameter, value in prepared:
            set_parameter(parameter, value)
        return len(prepared)
    return atomic_edit(DB, document, 'Hamdan - Batch parameter edit', prepare, apply)


def validate_sheet_numbers(proposed, existing):
    changed_ids = set(row['unique_id'] for row in proposed)
    occupied = set(row['number'].strip().lower() for row in existing if row['unique_id'] not in changed_ids)
    for row in proposed:
        number = row['number'].strip()
        if not number or any(ord(c) < 32 for c in number):
            raise ValueError('Sheet numbers must be non-empty and have no line breaks.')
        if number.lower() in occupied:
            raise ValueError('Duplicate sheet number: ' + number)
        occupied.add(number.lower())


def apply_sheet_changes(DB, document, rows, revision_ids, list_factory):
    def prepare():
        existing = [{'unique_id': s.UniqueId, 'number': s.SheetNumber}
                    for s in DB.FilteredElementCollector(document).OfClass(DB.ViewSheet)]
        validate_sheet_numbers(rows, existing)
        prepared = []
        for row in rows:
            sheet = document.GetElement(row['unique_id'])
            if sheet is None or sheet.SheetNumber != row['old_number'] or sheet.Name != row['old_name']:
                raise ValueError('A sheet changed since preview. Reload the manager.')
            if sorted(id_value(v) for v in sheet.GetAdditionalRevisionIds()) != row['old_revisions']:
                raise ValueError('Sheet revisions changed since preview.')
            requested = sorted(set(row['revisions']))
            if any(value not in revision_ids for value in requested):
                raise ValueError('An additional revision ID is not available.')
            if not row['name'].strip() or any(ord(c) < 32 for c in row['name']):
                raise ValueError('Sheet names must be non-empty and have no line breaks.')
            if row.get('parameter'):
                parameter = resolve_parameter(sheet, row['parameter']['key'])
                check_snapshot(parameter_snapshot(parameter), row['parameter']['snapshot'])
            prepared.append((sheet, row))
        return prepared
    def apply(prepared):
        for sheet, row in prepared:
            if row['number'] != row['old_number']:
                sheet.SheetNumber = 'HAMDAN-TEMP-' + uuid.uuid4().hex
        for sheet, row in prepared:
            sheet.SheetNumber, sheet.Name = row['number'], row['name']
            if row['revisions'] != row['old_revisions']:
                sheet.SetAdditionalRevisionIds(list_factory([revision_ids[v] for v in row['revisions']]))
            if row.get('parameter'):
                set_parameter(resolve_parameter(sheet, row['parameter']['key']), row['parameter']['new'])
        return len(prepared)
    return atomic_edit(DB, document, 'Hamdan - Sheet Manager', prepare, apply)
