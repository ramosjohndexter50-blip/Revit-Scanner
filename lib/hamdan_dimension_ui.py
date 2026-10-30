# -*- coding: utf-8 -*-
"""Choose a sheet viewport and review dimensions from selected visible host elements."""
from __future__ import unicode_literals
from pyrevit import DB, forms, revit, HOST_APP
from Autodesk.Revit.UI import RevitCommandId, PostableCommand
from Autodesk.Revit.UI.Selection import ISelectionFilter, ObjectType
from Autodesk.Revit.Exceptions import OperationCanceledException
from System.Collections.Generic import List
from hamdan_dimensioning import (MAX_ELEMENTS, supported_category, view_frame, paper_offset,
                                collect_plan, collect_reference_plan, reject_existing, apply_plan)
from hamdan_document_checks import collect
from hamdan_elements import safe_name, text_type
from hamdan_editing import id_value
from hamdan_ui import PreviewWindow


class NamedOption(forms.TemplateListItem):
    def __init__(self, element, label):
        forms.TemplateListItem.__init__(self, element)
        self.label = label

    @property
    def name(self):
        return self.label


class ModelFilter(ISelectionFilter):
    def AllowElement(self, element):
        return supported_category(DB, element)

    def AllowReference(self, reference, position):
        return False  # Host element selection only; linked references are outside this command.


class ReferenceFilter(ModelFilter):
    def AllowReference(self, reference, position):
        element = revit.doc.GetElement(reference.ElementId)
        if element is None or not supported_category(DB, element):
            return False
        if isinstance(element, DB.Grid):
            return True
        try:
            geometry = element.GetGeometryObjectFromReference(reference)
            return isinstance(geometry, (DB.PlanarFace, DB.Edge))
        except Exception:
            return False


def current_viewport(document):
    """Preserve an activated sheet viewport. Never switch to a separate model-view tab."""
    active = document.ActiveView
    if isinstance(active, DB.ViewSheet):
        chosen = [document.GetElement(eid) for eid in revit.uidoc.Selection.GetElementIds()]
        chosen = [v for v in chosen if isinstance(v, DB.Viewport) and id_value(v.SheetId) == id_value(active.Id)]
        viewport = chosen[0] if len(chosen) == 1 else choose_viewport(document)
        if viewport is None:
            return None
        view_frame(DB, document.GetElement(viewport.ViewId))
        revit.uidoc.Selection.SetElementIds(List[DB.ElementId]([viewport.Id]))
        command = RevitCommandId.LookupPostableCommandId(PostableCommand.ActivateView)
        if not HOST_APP.uiapp.CanPostCommand(command):
            raise ValueError('Double-click the viewport on the sheet to activate it, then run Add Dimensions again.')
        forms.alert('The viewport will activate on this sheet. Click Add Dimensions again and choose Pick faces/edges on sheet. '
                    'You can also double-click a viewport before starting the tool.', title='Hamdan - Activate viewport', warn_icon=False)
        HOST_APP.uiapp.PostCommand(command)
        return None
    view_frame(DB, active)
    matches = [v for v in collect(DB, document, cls=DB.Viewport) if id_value(v.ViewId) == id_value(active.Id)]
    if not matches:
        raise ValueError('Activate a model view placed on a sheet first.')
    if len(matches) == 1:
        return matches[0]
    choice = forms.SelectFromList.show([NamedOption(v, document.GetElement(v.SheetId).SheetNumber) for v in matches],
                                      title='Choose the sheet containing this view')
    return (choice.item if isinstance(choice, NamedOption) else choice) if choice else None


def choose_viewport(document):
    sheets = [s for s in collect(DB, document, cls=DB.ViewSheet) if not s.IsPlaceholder and s.GetAllViewports().Count]
    if not sheets:
        raise ValueError('Create a sheet and place a plan, section, elevation or detail view on it first.')
    active = document.ActiveView
    if isinstance(active, DB.ViewSheet) and any(s.UniqueId == active.UniqueId for s in sheets):
        sheet = active
    else:
        sheet = forms.SelectFromList.show(
            [NamedOption(s, '{} | {}'.format(s.SheetNumber, s.Name)) for s in sorted(sheets, key=lambda s: s.SheetNumber)],
            title='Hamdan - Add Dimensions | Choose sheet', button_name='Choose sheet')
        if not sheet:
            return None
        sheet = sheet.item if isinstance(sheet, NamedOption) else sheet
    choices = []
    for viewport_id in sheet.GetAllViewports():
        viewport = document.GetElement(viewport_id)
        view = document.GetElement(viewport.ViewId)
        try:
            view_frame(DB, view)
            choices.append(NamedOption(viewport, '{} | 1:{} | {}'.format(view.Name, view.Scale, view.ViewType)))
        except Exception:
            continue
    if not choices:
        raise ValueError('This sheet has no supported plan, section, elevation or detail viewport.')
    chosen = forms.SelectFromList.show(choices, title='Hamdan - Choose viewport on ' + sheet.SheetNumber, button_name='Open view')
    return (chosen.item if isinstance(chosen, NamedOption) else chosen) if chosen else None


def run():
    document = revit.doc
    if document is None or document.IsFamilyDocument or document.IsReadOnly:
        forms.alert('Open an editable Revit project with a view placed on a sheet.', title='Hamdan - Add Dimensions', warn_icon=False)
        return
    try:
        selection = forms.CommandSwitchWindow.show(['Pick faces/edges on sheet', 'Automatic from selected elements'],
                                                   message='For cabinet details, activate the viewport on the sheet and pick the exact references.')
        if not selection:
            return
        manual = selection == 'Pick faces/edges on sheet'
        viewport = current_viewport(document) if manual else choose_viewport(document)
        if viewport is None:
            return
        view = document.GetElement(viewport.ViewId)
        collector = DB.FilteredElementCollector(document).OfClass(DB.DimensionType).WhereElementIsElementType()
        try:
            styles = [s for s in collector if s.StyleType == DB.DimensionStyleType.Linear]
        finally:
            collector.Dispose()
        if not styles:
            raise ValueError('Load a linear dimension style into the project first.')
        style = forms.SelectFromList.show(
            [NamedOption(s, '{} | ID {}'.format(safe_name(s, lambda e: DB.Element.Name.GetValue(e)), id_value(s.Id))) for s in styles],
            title='Hamdan - Choose linear dimension style', button_name='Use style')
        if not style:
            return
        direction = forms.CommandSwitchWindow.show(['Horizontal', 'Vertical', 'Both'], message='Dimensions follow the view axes; rotated viewports rotate the dimensions too.')
        if not direction:
            return
        axes = ['Horizontal', 'Vertical'] if direction == 'Both' else [direction]
        mode = forms.CommandSwitchWindow.show(['Overall', 'Chain'], message='Overall uses the two outermost faces/grids. Chain includes all distinct parallel faces/grids.')
        if not mode:
            return
        side = forms.CommandSwitchWindow.show(['Above / Left', 'Above / Right', 'Below / Left', 'Below / Right'],
                                             message='First side is horizontal placement; second is vertical. Offset uses paper millimetres.')
        if not side:
            return
        offset = forms.ask_for_string(default='5', prompt='Offset in PAPER millimetres (5 mm at 1:100 = 500 mm in the model).', title='Hamdan - Dimension offset')
        if offset is None:
            return
        paper_offset(offset, view.Scale)
        if manual:
            picked = list(revit.uidoc.Selection.PickObjects(ObjectType.PointOnElement, ReferenceFilter(),
                'Pick exact parallel cabinet/building faces or straight edges. Use Tab to cycle references, then Finish. Esc cancels.'))
            elements = picked
        else:
            revit.uidoc.ActiveView = view
            picked = revit.uidoc.Selection.PickObjects(ObjectType.Element, ModelFilter(),
                'Select host building elements, cabinets/furniture/equipment; Finish. Esc cancels. Maximum {}.'.format(MAX_ELEMENTS))
            elements = list(dict((document.GetElement(r.ElementId).UniqueId, document.GetElement(r.ElementId)) for r in picked).values())
        with forms.ProgressBar(title='Hamdan - Reading dimension references', cancellable=True) as bar:
            counter = [0]
            def tick():
                bar.update_progress(counter[0], max(1, len(elements)))
                counter[0] += 1
                if bar.cancelled:
                    raise OperationCanceledException('Cancelled')
            planner = collect_reference_plan if manual else collect_plan
            placement = {'Above / Right': 'Positive', 'Below / Left': 'Negative',
                         'Above / Left': 'AboveLeft', 'Below / Right': 'BelowRight'}[side]
            plan = planner(DB, document, view, elements, axes, mode, offset, placement, tick)
        reject_existing(DB, document, view, plan['groups'])
        style = style.item if isinstance(style, NamedOption) else style
        plan.update(viewport=viewport.UniqueId, style=style.UniqueId)
        rows = []
        for group in plan['groups']:
            for record in group['records']:
                rows.append({'Target': record['label'], 'Field': group['axis'] + ' ' + record.get('reference_kind', 'reference'), 'Old': '',
                             'New': '{:.2f} mm along view axis'.format(record['position'] * 304.8)})
        preview = PreviewWindow(rows)
        preview.Title = 'Hamdan - Review Add Dimensions'
        preview.status_text.Text = ('{} | {} dimension(s), {}, {} mm paper offset. {} '
            'Apply creates dimensions in this view; they appear wherever the same view is used.\n{}').format(
                view.Name, len(plan['groups']), mode, offset,
                'Only your picked faces/edges are used.' if manual else 'Uses all qualifying faces/grids on the selected elements.',
                '\n'.join(plan['notes'][:20]) or 'All selections provided usable references.')
        preview.ShowDialog()
        if preview.accepted:
            count = apply_plan(DB, document, plan)
            forms.alert('{} dimension(s) created in {}.'.format(count, view.Name), title='Hamdan - Complete', warn_icon=False)
    except OperationCanceledException:
        return
    except Exception as error:
        forms.alert(text_type(error), title='Hamdan - Add Dimensions', warn_icon=False)
