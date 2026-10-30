# -*- coding: utf-8 -*-
"""Read-only sheet completeness audit for pyRevit."""
from pyrevit import forms, revit, script
from Autodesk.Revit.DB import BuiltInCategory, FilteredElementCollector, ViewSheet, Viewport, ScheduleSheetInstance
from hamdan_clashes import id_value
from hamdan_session import begin_scan

doc = revit.doc
output = script.get_output()
if doc is None:
    forms.alert('Open a Revit project before running Sheet Audit.', title='Sheet Audit')
else:
    sheets = [s for s in FilteredElementCollector(doc).OfClass(ViewSheet) if not s.IsPlaceholder]
    if not sheets:
        forms.alert('No sheets exist in this project yet. Create a sheet first in Revit: View > Sheet.',
                    title='Sheet Audit')
        raise SystemExit
    begin_scan(doc)
    titleblocks = list(FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_TitleBlocks).WhereElementIsNotElementType().ToElements())
    viewports = list(FilteredElementCollector(doc).OfClass(Viewport).ToElements())
    titleblock_by_sheet = {}
    for titleblock in titleblocks:
        titleblock_by_sheet[id_value(titleblock.OwnerViewId)] = titleblock
    viewports_by_sheet = {}
    for viewport in viewports:
        key = id_value(viewport.SheetId)
        viewports_by_sheet[key] = viewports_by_sheet.get(key, 0) + 1
    for instance in FilteredElementCollector(doc).OfClass(ScheduleSheetInstance):
        if not instance.IsTitleblockRevisionSchedule:
            key = id_value(instance.OwnerViewId)
            viewports_by_sheet[key] = viewports_by_sheet.get(key, 0) + 1
    rows = []
    missing_titleblocks = 0
    empty_sheets = 0
    for sheet in sorted(sheets, key=lambda item: item.SheetNumber):
        sheet_id = id_value(sheet.Id)
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
    output.print_table(rows, columns=['Sheet Number', 'Sheet Name', 'Title Block', 'Placed Views / Schedules'])
    forms.alert('Sheet Audit complete. See the pyRevit output window for details.', title='Sheet Audit')
