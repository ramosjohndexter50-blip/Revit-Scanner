# -*- coding: utf-8 -*-
"""Native pyRevit command entry points for sheet, parameter, profile and document tools."""
from __future__ import unicode_literals
import copy
import io
import json
import os
from pyrevit import DB, forms, revit, script
from System.Collections.Generic import List
from System.Diagnostics import Process
from hamdan_elements import safe_name, text_type, describe_element
from hamdan_editing import id_value, parameter_key, parameter_snapshot, apply_parameters, apply_sheet_changes, validate_sheet_numbers
from hamdan_issues import model_identity, attach_references, build_issues
from hamdan_profiles import effective_config, read_profiles, save_profile, activate_profile
from hamdan_storage import read_json, atomic_json, timestamp
from hamdan_tracking import load_register, overlay_issues
from hamdan_ui import Row, TableWindow, TextWindow
from hamdan_document_checks import collect, annotation_checks, sheet_layout_checks
from hamdan_reports import write_check_report
from hamdan_session import begin_scan, publish_scan

REPORT_FOLDER = os.path.join(os.path.expanduser('~'), 'Documents', 'RevitScanner', 'reports')


def identity():
    return model_identity(revit.doc, Process.GetCurrentProcess().Id)


def parameter_options(elements, strings_only=False):
    result = {}
    for element in elements:
        for p in element.Parameters:
            if id_value(p.Id) in (int(DB.BuiltInParameter.SHEET_NUMBER), int(DB.BuiltInParameter.SHEET_NAME)):
                continue  # Dedicated sheet fields have their own uniqueness/stale-preview checks.
            if p.IsReadOnly or p.StorageType.ToString() not in (('String',) if strings_only else ('String', 'Double', 'Integer')):
                continue
            key = parameter_key(p)
            label = '{} | {} | {}'.format(p.Definition.Name, p.StorageType.ToString(), key)
            result[label] = key
    return result


def sheet_manager():
    sheets = sorted(collect(DB, revit.doc, cls=DB.ViewSheet), key=lambda s: s.SheetNumber.lower())
    if not sheets:
        forms.alert('Create sheets first.', title='Hamdan', warn_icon=False)
        return
    options = parameter_options(sheets, strings_only=True)
    choices = ['No custom parameter'] + sorted(options)
    chosen = forms.SelectFromList.show(choices, title='Sheet Manager - optional sheet text parameter', button_name='Open manager')
    if chosen is None:
        return
    key = options.get(chosen)
    revisions = dict((id_value(r.Id), r) for r in collect(DB, revit.doc, cls=DB.Revision))
    records, rows = {}, []
    for sheet in sheets:
        old_revisions = sorted(id_value(v) for v in sheet.GetAdditionalRevisionIds())
        parameters = [p for p in sheet.Parameters if parameter_key(p) == key] if key else []
        parameter = parameters[0] if len(parameters) == 1 else None
        snapshot = parameter_snapshot(parameter) if parameter is not None else None
        record = {'unique_id': sheet.UniqueId, 'old_number': sheet.SheetNumber, 'old_name': sheet.Name,
                  'old_revisions': old_revisions, 'key': key, 'snapshot': snapshot}
        records[sheet.UniqueId] = record
        rows.append(Row(Include=False, Uid=sheet.UniqueId, Number=sheet.SheetNumber, Name=sheet.Name,
                        Revisions=', '.join(str(v) for v in old_revisions), Parameter=snapshot['display'] if snapshot else '',
                        CurrentRevision=safe_name(revit.doc.GetElement(sheet.GetCurrentRevision())),
                        ParameterState='Editable' if parameter is not None and not parameter.IsReadOnly else 'Unavailable'))
    def prepare(selected):
        plan, descriptions = [], []
        for row in selected:
            old = records[row.Uid]
            revision_values = sorted(set(int(v.strip()) for v in row.Revisions.split(',') if v.strip()))
            change = dict(old, number=row.Number.strip(), name=row.Name, revisions=revision_values)
            diffs = [('Number', old['old_number'], change['number']), ('Name', old['old_name'], change['name']),
                     ('Additional revision IDs', ', '.join(str(v) for v in old['old_revisions']), ', '.join(str(v) for v in revision_values))]
            if old['snapshot'] and row.Parameter != old['snapshot']['display']:
                change['parameter'] = {'key': old['key'], 'snapshot': old['snapshot'], 'new': row.Parameter}
                diffs.append((chosen, old['snapshot']['display'], row.Parameter))
            elif not old['snapshot'] and row.Parameter:
                raise ValueError('The chosen parameter is unavailable on sheet ' + old['old_number'])
            differences = [v for v in diffs if v[1] != v[2]]
            if differences:
                plan.append(change)
                descriptions.extend(dict(Target=old['old_number'], Field=f, Old=a, New=b) for f, a, b in differences)
        validate_sheet_numbers(plan, [{'unique_id': s.UniqueId, 'number': s.SheetNumber} for s in sheets])
        for change in plan:
            if any(v not in revisions for v in change['revisions']):
                raise ValueError('Unknown additional revision ID. Use the revision legend above the table.')
        return plan, descriptions
    help_text = 'Edit selected sheets, then Review changes. Additional revisions use comma-separated IDs and replace only the additional revision list; cloud-driven revisions remain. Custom parameter: {}. Revision legend: {}'.format(
        chosen, '; '.join('{} = {} ({})'.format(v, safe_name(r), r.Description) for v, r in sorted(revisions.items())) or 'No project revisions')
    window = TableWindow('Sheet Manager', help_text, rows,
        [('Number', 'Number', True), ('Name', 'Name', True), ('Current revision', 'CurrentRevision', False),
         ('Additional revision IDs', 'Revisions', True), ('Custom value', 'Parameter', bool(key)), ('Parameter state', 'ParameterState', False)],
        prepare, lambda plan: apply_sheet_changes(DB, revit.doc, plan, dict((v, r.Id) for v, r in revisions.items()), lambda ids: List[DB.ElementId](ids)))
    window.ShowDialog()


def batch_parameters(elements_override=None, source_note=''):
    if elements_override is not None:
        elements = elements_override
        scope = 'Tagged elements'
    else:
        scope = forms.CommandSwitchWindow.show(['Current selection', 'Active view', 'Whole model'], message='Choose parameter edit scope')
        if not scope:
            return
        if scope == 'Current selection':
            elements = [revit.doc.GetElement(v) for v in revit.uidoc.Selection.GetElementIds()]
        elif scope == 'Active view':
            elements = collect(DB, revit.doc, view=revit.doc.ActiveView)
        else:
            elements = collect(DB, revit.doc)
    categories = {}
    for element in elements:
        if element is not None and element.Category is not None and not isinstance(element, DB.ElementType):
            label = '{} | {}'.format(element.Category.Name, id_value(element.Category.Id))
            categories.setdefault(label, []).append(element)
    selected = forms.SelectFromList.show(sorted(categories), title='Choose categories to edit', multiselect=True, button_name='Choose parameters')
    if not selected:
        return
    elements = [e for label in selected for e in categories[label]]
    target = forms.CommandSwitchWindow.show(['Instance parameters', 'Type parameters'], message='Choose edit target. Type changes affect every instance using that type.')
    if not target:
        return
    if target == 'Type parameters':
        types = {}
        for e in elements:
            item = revit.doc.GetElement(e.GetTypeId())
            if item is not None:
                types[item.UniqueId] = item
        elements = list(types.values())
    options = parameter_options(elements)
    if not options:
        forms.alert('No supported writable parameters in this scope.', title='Hamdan', warn_icon=False)
        return
    chosen = forms.SelectFromList.show(sorted(options), title='Choose parameter (identity distinguishes duplicate names)')
    if chosen is None:
        return
    key = options[chosen]
    rows, unavailable = [], 0
    for element in elements:
        matches = [p for p in element.Parameters if parameter_key(p) == key and not p.IsReadOnly]
        if len(matches) != 1:
            unavailable += 1
            continue
        snapshot = parameter_snapshot(matches[0])
        record = describe_element(revit.doc, element, lambda e: DB.Element.Name.GetValue(e))
        rows.append(Row(Include=False, Uid=element.UniqueId, Label=record['display_name'], Id=record['id'], Old=snapshot['display'], New=snapshot['display'], Snapshot=snapshot))
    if not rows:
        forms.alert('The chosen parameter is unavailable on the target elements.', title='Hamdan', warn_icon=False)
        return
    value = forms.ask_for_string(prompt='Optional common value to prefill every row. Cancel keeps current values. Double values use project units; integer values use whole numbers.', title='Prefill value')
    if value is not None:
        for row in rows:
            row.New = value
    def prepare(selected_rows):
        changed = [r for r in selected_rows if r.New != r.Old]
        plan = [dict(unique_id=r.Uid, key=key, snapshot=r.Snapshot, new=r.New) for r in changed]
        return plan, [dict(Target='{} [ID {}]'.format(r.Label, r.Id), Field=chosen, Old=r.Old, New=r.New) for r in changed]
    TableWindow('Batch Parameter Editor', source_note + '\n' + '{}. {}. {} targets unavailable/read-only. Type targets affect all their instances. Double values use project display units; integers use whole numbers. Element-reference parameters are excluded.'.format(
        chosen, target, unavailable), rows, [('Element / Type', 'Label', False), ('ID', 'Id', False), ('Current', 'Old', False), ('Proposed', 'New', True)],
        prepare, lambda plan: apply_parameters(DB, revit.doc, plan)).ShowDialog()


def bep_profiles():
    action = forms.CommandSwitchWindow.show(['Use profile', 'Save active rules as profile', 'Edit profile JSON', 'Import profile JSON', 'Export profile JSON'], message='Hamdan - BEP Profiles')
    if not action:
        return
    store = read_profiles(REPORT_FOLDER)
    if action == 'Use profile':
        chosen = forms.SelectFromList.show(['Project rules (audit_config.json)'] + sorted(store['profiles']), title='Use profile for this model')
        if chosen is not None:
            activate_profile(REPORT_FOLDER, identity(), '' if chosen == 'Project rules (audit_config.json)' else chosen)
    elif action in ('Save active rules as profile', 'Import profile JSON'):
        name = forms.ask_for_string(prompt='Profile name', title='BEP Profiles')
        if name is None:
            return
        if name.strip() in store['profiles']:
            forms.alert('That profile already exists. Use Edit profile JSON.', title='Hamdan', warn_icon=False)
            return
        if action == 'Import profile JSON':
            path = forms.pick_file(file_ext='json')
            if not path:
                return
            rules = read_json(path)
        else:
            rules = effective_config(REPORT_FOLDER, identity())
            rules.pop('bep_profile', None)
        save_profile(REPORT_FOLDER, name, rules)
        activate_profile(REPORT_FOLDER, identity(), name.strip())
    else:
        chosen = forms.SelectFromList.show(sorted(store['profiles']), title=action)
        if chosen is None:
            return
        rules = store['profiles'][chosen]
        if action == 'Export profile JSON':
            path = forms.save_file(file_ext='json', default_name='bep_profile.json')
            if path:
                atomic_json(path, rules)
        else:
            text = json.dumps(rules, indent=2, ensure_ascii=False)
            while True:
                window = TextWindow('Edit BEP profile: ' + chosen, 'JSON rules only. This shared profile applies to every model that uses it. Empty sections disable those rules; no model content is changed.', text)
                window.ShowDialog()
                if window.result is None:
                    return
                text = window.result
                try:
                    save_profile(REPORT_FOLDER, chosen, json.loads(text))
                    break
                except Exception as error:
                    forms.alert(text_type(error), title='Invalid profile', warn_icon=False)
    forms.alert('Complete', title='BEP Profiles', warn_icon=False)


class NamedOption(forms.TemplateListItem):
    @property
    def name(self):
        item = self.item
        return '{} | {} | ID {}'.format(safe_name(item), item.ViewType, item.Id)


def document_scan(kind):
    config = effective_config(REPORT_FOLDER, identity())
    if kind == 'annotations':
        views = [v for v in collect(DB, revit.doc, cls=DB.View) if not v.IsTemplate and v.ViewType in (
            DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan, DB.ViewType.EngineeringPlan, DB.ViewType.Section, DB.ViewType.Elevation, DB.ViewType.Detail, DB.ViewType.ThreeD)]
        selected = forms.SelectFromList.show([NamedOption(v) for v in views], title='Dimension & Tag Audit - choose host views', multiselect=True, button_name='Scan views')
        title, check = 'Dimension & Tag Audit', annotation_checks
    else:
        sheets = sorted(collect(DB, revit.doc, cls=DB.ViewSheet), key=lambda s: s.SheetNumber)
        labels = dict(('{} - {} | ID {}'.format(s.SheetNumber, s.Name, s.Id), s) for s in sheets)
        chosen = forms.SelectFromList.show(sorted(labels), title='Sheet Layout Checker - choose sheets', multiselect=True, button_name='Scan sheets')
        selected = [labels[v] for v in chosen] if chosen else []
        title, check = 'Sheet Layout Checker', sheet_layout_checks
    if not selected:
        return
    selected = [v.item if isinstance(v, NamedOption) else v for v in selected]
    scan_identity = begin_scan(revit.doc)
    with forms.ProgressBar(title='Hamdan - ' + title, cancellable=True) as bar:
        from hamdan_scan import ScanProgress
        progress = ScanProgress(bar)
        data = check(DB, revit.doc, selected, config, progress.pulse)
        data['timestamp'], data['review_model_identity'], data['bep_profile'] = timestamp(), scan_identity, config.get('bep_profile')
        data['history_mode'], data['baseline_applied'] = 'fresh', False
        data['review_issues'] = attach_references(build_issues({'document_findings': data['findings']}),
            lambda value: describe_element(revit.doc, revit.doc.GetElement(DB.ElementId(value)), lambda e: DB.Element.Name.GetValue(e)))
        path = write_check_report(REPORT_FOLDER, title, revit.doc.Title, data)
        publish_scan(revit.doc, scan_identity, os.path.splitext(path)[0] + '.json')
    Process.Start('explorer.exe', path)
    forms.alert('Complete', title=title, warn_icon=False)


def dimensions_and_tags():
    from hamdan_annotation_ui import annotation_menu
    annotation_menu(lambda: document_scan('annotations'), batch_parameters)


def run(command):
    if revit.doc is None or revit.doc.IsFamilyDocument:
        forms.alert('Open a Revit project first.', title='Hamdan', warn_icon=False)
        return
    try:
        {'sheets': sheet_manager, 'parameters': batch_parameters, 'profiles': bep_profiles,
         'annotations': dimensions_and_tags, 'layout': lambda: document_scan('layout')}[command]()
    except BaseException as error:
        from hamdan_scan import ScanCancelled
        if isinstance(error, ScanCancelled):
            forms.alert('Scan cancelled. No complete report was saved.', title='Hamdan', warn_icon=False)
        elif isinstance(error, Exception):
            forms.alert(text_type(error), title='Hamdan', warn_icon=False)
        else:
            raise
