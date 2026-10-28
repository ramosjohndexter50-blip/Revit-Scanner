# -*- coding: utf-8 -*-
"""Review a saved audit in a modal picker, then locate an issue in the active RVT."""
import os

from pyrevit import forms, revit
from Autodesk.Revit.DB import ElementId, View
from System.Collections.Generic import List
from System.Diagnostics import Process

from hamdan_issues import latest_model_report, model_identity


class IssueOption(forms.TemplateListItem):
    @property
    def name(self):
        row = self.item
        ids = ', '.join(str(value) for value in row.get('element_ids', [])) or 'Project check'
        return '[{}] {} | {} | {} | IDs: {}'.format(
            row['severity'], row['kind'], row.get('level', 'Not assigned'), row['message'], ids)

    @property
    def description(self):
        row = self.item
        refs = row.get('element_refs', [])
        elements = '\n'.join('{} | {} | {} | {}'.format(
            ref['id'], ref.get('category_name', ''), ref.get('name', ''), ref.get('level', '')) for ref in refs)
        return ('{}\n\nSeverity: {}\nStatus: {}\nLevel: {}\n\nAffected elements:\n{}'
                '\n\nSuggested fix:\n{}').format(
                    row['message'], row['severity'], row['status'], row.get('level', 'Not assigned'),
                    elements or 'No element references available for this project/configuration check.', row['suggested_fix'])


def locate_issue(document, uidocument, issue):
    elements = []
    missing = 0
    for ref in issue.get('element_refs', []):
        unique_id = ref.get('unique_id')
        try:
            element = document.GetElement(unique_id) if unique_id else None
        except Exception:
            element = None
        if element is None:
            missing += 1
        else:
            elements.append(element)
    if not elements:
        forms.alert('This finding has no available elements to locate.\n\n{}\n\n'
                    'If elements have changed since the scan, run Audit again.'.format(issue['suggested_fix']),
                    title='Hamdan - Review Issues', warn_icon=False)
        return

    ids = List[ElementId]()
    for element in elements:
        ids.Add(element.Id)
    uidocument.Selection.SetElementIds(ids)
    try:
        # Open view/sheet findings directly; ShowElements is for visible model elements.
        if len(elements) == 1 and isinstance(elements[0], View):
            if elements[0].IsTemplate:
                raise ValueError('A view template cannot be opened.')
            uidocument.ActiveView = elements[0]
        else:
            uidocument.ShowElements(ids)
    except Exception:
        forms.alert('The available elements are selected, but Revit could not zoom to them. '
                    'Open an appropriate view or schedule to inspect this finding.\n\n{}'.format(issue['suggested_fix']),
                    title='Hamdan - Review Issues', warn_icon=False)
        return
    if missing:
        forms.alert('{} element(s) were no longer available. The remaining elements are selected. '
                    'Run Audit again to refresh the report.'.format(missing),
                    title='Hamdan - Review Issues', warn_icon=False)


def main():
    if revit.doc is None or revit.doc.IsFamilyDocument:
        forms.alert('Open a Revit project to review its audit findings.', title='Hamdan', warn_icon=False)
        return
    folder = os.path.join(os.path.expanduser('~'), 'Documents', 'RevitScanner', 'reports')
    identity = model_identity(revit.doc, Process.GetCurrentProcess().Id)
    data = latest_model_report(folder, identity)
    if data is None:
        forms.alert('Run Audit for this model first to create the issue register.', title='Hamdan', warn_icon=False)
        return
    issues = data.get('review_issues', [])
    if not issues:
        forms.alert('No review issues were recorded in the latest audit for this model.', title='Hamdan', warn_icon=False)
        return
    options = [IssueOption(row) for row in issues]
    chosen = forms.SelectFromList.show(
        options, title='Hamdan - Review Issues | {} | {}'.format(data.get('model_name', ''), data.get('timestamp', '')),
        width=1150, height=700, multiselect=False, info_panel=True, button_name='Select & Zoom')
    if chosen is not None:
        if isinstance(chosen, IssueOption):
            chosen = chosen.item
        locate_issue(revit.doc, revit.uidoc, chosen)


if __name__ == '__main__':
    main()
