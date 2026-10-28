# -*- coding: utf-8 -*-
"""Live named selection list. Selection events read only; locate uses an ExternalEvent."""
__persistentengine__ = True

import os
from pyrevit import DB, HOST_APP, forms, revit
from pyrevit.revit import events
from System import Object
from System.Collections.Generic import List
from System.Collections.ObjectModel import ObservableCollection

from hamdan_elements import ElementListState, describe_element


class ElementRow(object):
    def __init__(self, record):
        self.record = record
        self.DisplayName = record['display_name']
        self.Category = record['category_name']
        self.Family = record['family']
        self.Type = record['type']
        self.Level = record['level']
        self.ElementId = str(record['id'])
        self.UniqueId = record['unique_id']


class ElementWindow(forms.WPFWindow):
    def __init__(self, document):
        self.closed = False
        self.state = ElementListState()
        self.document_key = document.GetHashCode()
        self.document_title = document.Title
        self.selection_handler = None
        self.suppress_capture = False
        forms.WPFWindow.__init__(self, os.path.join(os.path.dirname(__file__), 'ui.xaml'), handle_esc=False)
        self.model_text.Text = self.document_title
        self.Closed += self.window_closed
        self.selection_handler = events.add_handler('selection-changed', self.selection_changed)
        self.capture(HOST_APP.uiapp)

    def same_document(self, document):
        return document is not None and document.IsValidObject and document.GetHashCode() == self.document_key

    def selection_changed(self, sender, args):
        # Reading metadata is permitted here. Do not select, open views, or transact.
        if self.closed or self.suppress_capture or not self.capture_toggle.IsChecked:
            return
        try:
            self.capture(HOST_APP.uiapp)
        except Exception as error:
            self.status_text.Text = 'Could not read this selection: {}'.format(error)

    def capture(self, app):
        uidoc = app.ActiveUIDocument
        if uidoc is None or not self.same_document(uidoc.Document):
            self.status_text.Text = 'Switch back to {} to continue this list.'.format(self.document_title)
            return
        records = []
        selected_ids = uidoc.Selection.GetElementIds()
        for index, element_id in enumerate(selected_ids):
            if index >= self.state.limit:
                break
            element = uidoc.Document.GetElement(element_id)
            if element is not None:
                records.append(describe_element(uidoc.Document, element,
                                               name_getter=lambda item: DB.Element.Name.GetValue(item)))
        skipped = self.state.upsert(records)
        self.render()
        if skipped or len(selected_ids) > self.state.limit:
            self.status_text.Text += ' List limit: {} elements; narrow the selection to collect more.'.format(self.state.limit)

    def render(self):
        selected = set(row.UniqueId for row in self.element_list.SelectedItems)
        items = ObservableCollection[Object]()
        for record in self.state.visible(self.search_box.Text or ''):
            items.Add(ElementRow(record))
        self.element_list.ItemsSource = items
        for row in items:
            if row.UniqueId in selected:
                self.element_list.SelectedItems.Add(row)
        self.status_text.Text = '{} element(s) in list | {} shown'.format(len(self.state.rows), len(items))

    def search_changed(self, sender, args):
        # The XAML loader can fire TextChanged before the other controls exist.
        if hasattr(self, 'element_list') and self.element_list is not None:
            self.render()

    def locate_clicked(self, sender, args):
        rows = [row.record for row in self.element_list.SelectedItems]
        if not rows:
            self.status_text.Text = 'Select a row in the list first.'
            return
        self.status_text.Text = 'Locating selected elements...'
        events.execute_in_revit_context(self.locate, rows)

    def locate(self, rows):
        if self.closed:
            return
        uidoc = HOST_APP.uiapp.ActiveUIDocument
        if uidoc is None or not self.same_document(uidoc.Document):
            self.status_text.Text = 'Switch back to {} before locating elements.'.format(self.document_title)
            return
        ids = List[DB.ElementId]()
        elements = []
        for row in rows:
            element = uidoc.Document.GetElement(row['unique_id'])
            if element is not None:
                elements.append(element)
                ids.Add(element.Id)
        if not elements:
            self.status_text.Text = 'These elements are no longer available. Remove them from the list.'
            return
        self.suppress_capture = True
        try:
            uidoc.Selection.SetElementIds(ids)
            if len(elements) == 1 and isinstance(elements[0], DB.View):
                uidoc.ActiveView = elements[0]
            else:
                uidoc.ShowElements(ids)
            self.status_text.Text = '{} element(s) selected and located.'.format(len(elements))
        except Exception:
            self.status_text.Text = 'Elements selected. Open an appropriate view or schedule to see them.'
        finally:
            self.suppress_capture = False

    def remove_clicked(self, sender, args):
        self.state.remove([row.UniqueId for row in self.element_list.SelectedItems])
        self.render()

    def clear_clicked(self, sender, args):
        self.state.clear()
        self.render()

    def detach(self):
        if self.selection_handler is not None:
            events.remove_handler('selection-changed', self.selection_handler)
            self.selection_handler = None

    def window_closed(self, sender, args):
        self.closed = True
        # Event registration changes also need Revit API context.
        if HOST_APP.has_api_context:
            self.detach()
        else:
            events.execute_in_revit_context(self.detach)


if revit.doc is None or revit.doc.IsFamilyDocument:
    forms.alert('Open a Revit project to use Element List.', title='Hamdan', warn_icon=False)
else:
    previous = globals().get('ELEMENT_WINDOW')
    if previous is not None and not previous.closed and previous.same_document(revit.doc):
        previous.Activate()
    else:
        if previous is not None and not previous.closed:
            previous.Close()
        ELEMENT_WINDOW = ElementWindow(revit.doc)
        ELEMENT_WINDOW.show(modal=False)
