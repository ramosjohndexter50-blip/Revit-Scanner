# -*- coding: utf-8 -*-
"""Controlled bulk sheet-number prefix tool for pyRevit."""
from pyrevit import forms, revit
from Autodesk.Revit.DB import FilteredElementCollector, Transaction, ViewSheet

doc = revit.doc
if doc is None:
    forms.alert('Open a Revit project before editing sheets.', title='Prefix Sheet Numbers')
else:
    sheets = sorted(list(FilteredElementCollector(doc).OfClass(ViewSheet).ToElements()), key=lambda item: item.SheetNumber)
    selected = forms.SelectFromList.show(sheets, name_attr='SheetNumber', multiselect=True,
                                         title='Select sheets to prefix', button_name='Continue')
    if selected:
        prefix = forms.ask_for_string(default='', prompt='Prefix to add before each selected sheet number (example: A-).',
                                      title='Prefix Sheet Numbers')
        if prefix:
            preview = '\n'.join(['{}  ->  {}{}'.format(sheet.SheetNumber, prefix, sheet.SheetNumber) for sheet in selected])
            if forms.alert('Apply this prefix?\n\n{}'.format(preview), title='Confirm Sheet Number Update', yes=True, no=True):
                transaction = Transaction(doc, 'Prefix selected sheet numbers')
                try:
                    transaction.Start()
                    for sheet in selected:
                        sheet.SheetNumber = prefix + sheet.SheetNumber
                    transaction.Commit()
                    forms.alert('Updated {} sheet number(s).'.format(len(selected)), title='Prefix Sheet Numbers')
                except Exception as error:
                    if transaction.HasStarted():
                        transaction.RollBack()
                    forms.alert('No sheet numbers were changed:\n\n{}'.format(error), title='Prefix Sheet Numbers')
