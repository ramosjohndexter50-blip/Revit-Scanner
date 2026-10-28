# -*- coding: utf-8 -*-
"""Reusable modal tables; all Revit API callbacks stay on the command's UI thread."""
from __future__ import unicode_literals
import os
from pyrevit import forms
from System import Object
from System.Collections.ObjectModel import ObservableCollection
from System.Windows.Controls import DataGridEditingUnit
from hamdan_elements import text_type


class Row(object):
    def __init__(self, **values):
        self.__dict__.update(values)


def collection(rows):
    result = ObservableCollection[Object]()
    for row in rows:
        result.Add(row)
    return result


class TableWindow(forms.WPFWindow):
    def __init__(self, title, help_text, rows, columns, prepare, apply):
        forms.WPFWindow.__init__(self, os.path.join(os.path.dirname(__file__), 'hamdan_table.xaml'))
        from System.Windows.Controls import DataGridTextColumn, DataGridCheckBoxColumn
        from System.Windows.Data import Binding
        self.Title, self.heading.Text, self.help_text.Text = 'Hamdan - ' + title, title, help_text
        self.rows, self.prepare, self.apply = rows, prepare, apply
        self.search_fields = [field for label, field, editable in columns]
        check = DataGridCheckBoxColumn()
        check.Header, check.Binding = 'Use', Binding('Include')
        self.table.Columns.Add(check)
        for label, field, editable in columns:
            column = DataGridTextColumn()
            column.Header, column.Binding, column.IsReadOnly = label, Binding(field), not editable
            self.table.Columns.Add(column)
        self.table.ItemsSource = collection(rows)
        self.status_text.Text = '{} rows. Select Use for the rows to edit.'.format(len(rows))

    def commit_grid(self):
        self.table.CommitEdit(DataGridEditingUnit.Cell, True)
        self.table.CommitEdit(DataGridEditingUnit.Row, True)

    def filter_changed(self, sender, args):
        if not hasattr(self, 'rows'):
            return
        self.commit_grid()
        words = self.search_box.Text.lower().split()
        visible = [r for r in self.rows if all(w in ' '.join(text_type(getattr(r, f, '')) for f in self.search_fields).lower() for w in words)]
        self.table.ItemsSource = collection(visible)
        self.status_text.Text = '{} shown / {} total. Hidden rows keep their Use selection.'.format(len(visible), len(self.rows))

    def select_all(self, sender, args):
        self.commit_grid()
        for row in self.table.ItemsSource:
            row.Include = True
        self.table.Items.Refresh()

    def clear_selection(self, sender, args):
        self.commit_grid()
        for row in self.rows:
            row.Include = False
        self.table.Items.Refresh()

    def review_clicked(self, sender, args):
        self.commit_grid()
        try:
            plan, descriptions = self.prepare([r for r in self.rows if r.Include])
            if not plan:
                self.status_text.Text = 'No changed values in the selected rows.'
                return
            preview = PreviewWindow(descriptions)
            preview.ShowDialog()
            if not preview.accepted:
                return
            count = self.apply(plan)
            forms.alert('{} row(s) updated.'.format(count), title=self.Title, warn_icon=False)
            self.Close()
        except Exception as error:
            self.status_text.Text = text_type(error)

    def cancel_clicked(self, sender, args):
        self.Close()


class PreviewWindow(forms.WPFWindow):
    def __init__(self, rows):
        forms.WPFWindow.__init__(self, os.path.join(os.path.dirname(__file__), 'hamdan_preview.xaml'))
        self.accepted = False
        self.preview.ItemsSource = collection([Row(**r) for r in rows])
        self.status_text.Text = '{} change(s), including selected rows hidden by filters. Apply commits one atomic batch.'.format(len(rows))

    def apply_clicked(self, sender, args):
        self.accepted = True
        self.Close()

    def cancel_clicked(self, sender, args):
        self.Close()


class TextWindow(forms.WPFWindow):
    def __init__(self, title, help_text, text):
        forms.WPFWindow.__init__(self, os.path.join(os.path.dirname(__file__), 'hamdan_text.xaml'))
        self.Title, self.heading.Text, self.help_text.Text, self.editor.Text = 'Hamdan - ' + title, title, help_text, text
        self.result = None

    def save_clicked(self, sender, args):
        self.result = self.editor.Text
        self.Close()

    def cancel_clicked(self, sender, args):
        self.Close()
