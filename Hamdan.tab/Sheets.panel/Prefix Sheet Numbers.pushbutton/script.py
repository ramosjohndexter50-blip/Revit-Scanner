# -*- coding: utf-8 -*-
"""Preview and apply custom series to sheet numbers or room numbers."""
import os
import uuid
from pyrevit import DB, forms, revit
from Autodesk.Revit.DB.Architecture import Room
from System import Object
from System.Collections.ObjectModel import ObservableCollection
from System.Windows.Media import Brushes

from hamdan_elements import safe_name
from hamdan_numbering import plan_numbers, find_conflicts, apply_assignments, text_type

TARGET_CLASSES = {'Sheets': DB.ViewSheet, 'Rooms': Room}
TARGET_PROPERTIES = {'Sheets': 'SheetNumber', 'Rooms': 'Number'}


def id_value(value):
    try:
        return int(value.Value)
    except Exception:
        return int(value.IntegerValue)


def collect_records(document, target):
    records = []
    for element in DB.FilteredElementCollector(document).OfClass(TARGET_CLASSES[target]).WhereElementIsNotElementType():
        name = safe_name(element, lambda item: DB.Element.Name.GetValue(item))
        group = 'Sheets'
        level = ''
        if target == 'Rooms':
            try:
                name = element.get_Parameter(DB.BuiltInParameter.ROOM_NAME).AsString() or name
            except Exception:
                pass
            try:
                group = str(id_value(element.CreatedPhaseId))
                level = safe_name(document.GetElement(element.LevelId))
            except Exception:
                group = 'Rooms'
        parameter_key = DB.BuiltInParameter.SHEET_NUMBER if target == 'Sheets' else DB.BuiltInParameter.ROOM_NUMBER
        parameter = element.get_Parameter(parameter_key)
        records.append({'id': id_value(element.Id), 'unique_id': element.UniqueId,
                        'old': getattr(element, TARGET_PROPERTIES[target]) or '', 'name': name,
                        'level': level, 'group': group,
                        'editable': parameter is None or not parameter.IsReadOnly})
    return records


class NumberOption(forms.TemplateListItem):
    @property
    def name(self):
        row = self.item
        return u'{} | {} | {} | ID {}'.format(row['old'], row['name'], row['level'], row['id'])


class PreviewRow(object):
    def __init__(self, row):
        self.Order = str(row['position'])
        self.Current = row['old']
        self.Proposed = row['new']
        self.Name = row['name']
        self.Level = row.get('level', '')
        self.ElementId = str(row['id'])


def apply_number_plan(document, target, plan):
    fresh = collect_records(document, target)
    conflicts = find_conflicts(plan, fresh)
    if conflicts:
        raise ValueError('\n'.join(conflicts[:5]))
    current = dict((row['unique_id'], row) for row in fresh)
    for row in plan:
        actual = current.get(row['unique_id'])
        if actual is None or actual['old'] != row['old']:
            raise ValueError('Numbers changed after the preview. Reopen the formatter and try again.')
        if not actual['editable']:
            raise ValueError('Element {} has a read-only number.'.format(row['id']))
    transaction = DB.Transaction(document, 'Hamdan - Format {} numbers'.format(target.lower()))
    try:
        transaction.Start()
        property_name = TARGET_PROPERTIES[target]
        apply_assignments(plan, document.GetElement,
                          lambda element, value: setattr(element, property_name, value),
                          'HDN-TMP-' + uuid.uuid4().hex)
        if transaction.Commit() != DB.TransactionStatus.Committed:
            raise RuntimeError('Revit did not confirm the update. Review the model before retrying.')
    except Exception:
        if transaction.GetStatus() == DB.TransactionStatus.Started:
            transaction.RollBack()
        raise
    finally:
        transaction.Dispose()


class FormatterWindow(forms.WPFWindow):
    def __init__(self, document, target, selected, existing):
        self.ready = False
        self.success = False
        self.document = document
        self.target = target
        self.selected = selected
        self.existing = existing
        self.plan = []
        forms.WPFWindow.__init__(self, os.path.join(os.path.dirname(__file__), 'ui.xaml'))
        self.target_text.Text = '{} - {} selected'.format(target, len(selected))
        self.format_box.Text = '{floor}{tower}-{n:02}'
        self.floor_box.Text = 'G'
        self.tower_box.Text = 'A'
        self.start_box.Text = '3'
        self.step_box.Text = '1'
        self.order_box.ItemsSource = ['Current number', 'Name', 'Element ID']
        self.order_box.SelectedIndex = 0
        self.ready = True
        self.rebuild()

    def rebuild(self):
        if not self.ready:
            return
        self.apply_button.IsEnabled = False
        try:
            self.plan = plan_numbers(self.selected, self.format_box.Text,
                                     self.start_box.Text, self.step_box.Text,
                                     self.floor_box.Text, self.tower_box.Text, self.order_box.SelectedItem)
            rows = ObservableCollection[Object]()
            for row in self.plan:
                rows.Add(PreviewRow(row))
            self.preview_list.ItemsSource = rows
            conflicts = find_conflicts(self.plan, self.existing)
            readonly = [row for row in self.plan if not row['editable']]
            if readonly:
                conflicts.append('{} selected number(s) are read-only.'.format(len(readonly)))
            if conflicts:
                raise ValueError('\n'.join(conflicts[:5]))
            changed = sum(1 for row in self.plan if row['old'] != row['new'])
            self.status_text.Foreground = Brushes.DarkSlateGray
            self.status_text.Text = '{} number(s) will change. Check the preview, then Apply.'.format(changed)
            self.apply_button.Content = 'Apply {} changes'.format(changed)
            self.apply_button.IsEnabled = changed > 0
        except Exception as error:
            self.status_text.Foreground = Brushes.Firebrick
            self.status_text.Text = text_type(error)

    def settings_changed(self, sender, args):
        self.rebuild()

    def apply_clicked(self, sender, args):
        self.rebuild()
        if not self.apply_button.IsEnabled:
            return
        try:
            changes = [row for row in self.plan if row['old'] != row['new']]
            apply_number_plan(self.document, self.target, changes)
        except Exception as error:
            self.status_text.Foreground = Brushes.Firebrick
            self.status_text.Text = u'Update failed: {}'.format(error)
            return
        self.success = True
        self.Close()

    def cancel_clicked(self, sender, args):
        self.Close()


def main():
    if revit.doc is None or revit.doc.IsFamilyDocument:
        forms.alert('Open a Revit project first.', title='Hamdan', warn_icon=False)
        return
    target = forms.CommandSwitchWindow.show(['Sheets', 'Rooms'], message='Hamdan - Choose numbering target')
    if not target:
        return
    existing = collect_records(revit.doc, target)
    if not existing:
        forms.alert('No {} exist in this project yet.'.format(target.lower()), title='Hamdan', warn_icon=False)
        return
    options = [NumberOption(row) for row in sorted(existing, key=lambda item: (item['old'], item['id']))]
    selected = forms.SelectFromList.show(options, multiselect=True,
                                          title='Hamdan - Select {} to number'.format(target.lower()),
                                          button_name='Format Numbers', width=850, height=650)
    if not selected:
        return
    selected = [row.item if isinstance(row, NumberOption) else row for row in selected]
    window = FormatterWindow(revit.doc, target, selected, existing)
    window.ShowDialog()
    if window.success:
        forms.alert('Complete', title='Hamdan', warn_icon=False)


if __name__ == '__main__':
    main()
