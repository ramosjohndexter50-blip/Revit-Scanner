# -*- coding: utf-8 -*-
"""Preview and create sheets atomically in the active Revit project."""
import os
from pyrevit import DB, forms, revit
from System import Object
from System.Collections.ObjectModel import ObservableCollection
from hamdan_elements import safe_name
from hamdan_sheets import plan_sheets, create_batch


class PreviewRow(object):
    def __init__(self, row):
        self.Number, self.Name = row['new'], row['name']


class CreateSheetsWindow(forms.WPFWindow):
    def __init__(self, document):
        self.document, self.plan = document, []
        self.existing = [{'unique_id': sheet.UniqueId, 'id': str(sheet.Id), 'group': 'Sheets', 'old': sheet.SheetNumber}
                         for sheet in DB.FilteredElementCollector(document).OfClass(DB.ViewSheet)]
        self.titleblocks = {'No title block': DB.ElementId.InvalidElementId}
        for item in DB.FilteredElementCollector(document).OfCategory(DB.BuiltInCategory.OST_TitleBlocks).WhereElementIsElementType():
            label = '{} | {} | ID {}'.format(item.FamilyName, safe_name(item, lambda e: DB.Element.Name.GetValue(e)), item.Id)
            self.titleblocks[label] = item.Id
        self.ready = False
        forms.WPFWindow.__init__(self, os.path.join(os.path.dirname(__file__), 'ui.xaml'))
        self.titleblock_box.ItemsSource = sorted(self.titleblocks)
        self.titleblock_box.SelectedItem = next((key for key in sorted(self.titleblocks) if key != 'No title block'), 'No title block')
        self.ready = True
        self.changed(None, None)

    def changed(self, sender, args):
        if not self.ready:
            return
        try:
            self.plan = plan_sheets(self.existing, self.count_box.Text, self.pattern_box.Text, self.name_box.Text,
                                   self.start_box.Text, self.step_box.Text, self.floor_box.Text, self.tower_box.Text)
            items = ObservableCollection[Object]()
            for row in self.plan:
                items.Add(PreviewRow(row))
            self.preview.ItemsSource = items
            self.create_button.IsEnabled = True
            self.status_text.Text = '{} sheet(s) ready. Click Create to add this preview to the model.'.format(len(self.plan))
        except Exception as error:
            self.plan = []
            self.preview.ItemsSource = None
            self.create_button.IsEnabled = False
            self.status_text.Text = str(error)

    def create_clicked(self, sender, args):
        self.changed(None, None)
        if not self.plan:
            return
        fresh = set(sheet.SheetNumber.lower() for sheet in DB.FilteredElementCollector(self.document).OfClass(DB.ViewSheet))
        if any(row['new'].lower() in fresh for row in self.plan):
            self.status_text.Text = 'A sheet number changed after preview. Close and reopen Create Sheets.'
            return
        try:
            created = create_batch(DB, self.document, self.plan, self.titleblocks[self.titleblock_box.SelectedItem])
        except Exception as error:
            forms.alert('No sheets were added.\n\n{}'.format(error), title='Hamdan - Create Sheets')
            return
        self.Close()
        forms.alert('{} sheet(s) created.'.format(len(created)), title='Hamdan', warn_icon=False)

    def cancel_clicked(self, sender, args):
        self.Close()


if revit.doc is None or revit.doc.IsFamilyDocument or revit.doc.IsReadOnly:
    forms.alert('Open an editable Revit project to create sheets.', title='Hamdan', warn_icon=False)
else:
    CreateSheetsWindow(revit.doc).show_dialog()
