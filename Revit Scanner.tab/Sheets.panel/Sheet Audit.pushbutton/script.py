# -*- coding: utf-8 -*-
"""Read-only sheet completeness audit for pyRevit."""
from pyrevit import forms, revit, script
from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector, ViewSheet, Viewport

doc = revit.doc
output = script.get_output()
if doc is None:
    forms.alert('Open a Revit project before running Sheet Audit.', title='Sheet Audit')
else:
    sheets = list(FilteredElementCollector(doc).OfClass(ViewSheet).ToElements())
    titleblocks = list(FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_TitleBlocks).WhereElementIsNotElementType().ToElements())
    viewports = list(FilteredElementCollector(doc).OfClass(Viewport).ToElements())
    titleblock_by_sheet = {}
    for titleblock in titleblocks:
        titleblock_by_sheet[titleblock.OwnerViewId.IntegerValue] = titleblock
    viewports_by_sheet = {}
    for viewport in viewports:
        key = viewport.SheetId.IntegerValue
        viewports_by_sheet[key] = viewports_by_sheet.get(key, 0) + 1
    rows = []
    missing_titleblocks = 0
    empty_sheets = 0
    for sheet in sorted(sheets, key=lambda item: item.SheetNumber):
        sheet_id = sheet.Id.IntegerValue
        has_titleblock = sheet_id in titleblock_by_sheet
        view_count = viewports_by_sheet.get(sheet_id, 0)
        if not has_titleblock:
            missing_titleblocks += 1
        if view_count == 0:
            empty_sheets += 1
        rows.append([sheet.SheetNumber, sheet.Name, 'Yes' if has_titleblock else 'No', view_count])
    output.set_title('Sheet Audit')
    output.print_md('## Sheet Audit')
    output.print_md('- Sheets: {}\n- Missing title blocks: {}\n- Sheets without placed views: {}'.format(len(sheets), missing_titleblocks, empty_sheets))
    output.print_table(rows, columns=['Sheet Number', 'Sheet Name', 'Title Block', 'Placed Views'])
    forms.alert('Sheet Audit complete. See the pyRevit output window for details.', title='Sheet Audit')
