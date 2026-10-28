# -*- coding: utf-8 -*-
"""Review a saved audit in a modal picker, then locate an issue in the active RVT."""
import os

from pyrevit import DB, forms, revit
from Autodesk.Revit.DB import ElementId, View
from System.Collections.Generic import List
from System.Diagnostics import Process

from hamdan_session import current_report
from hamdan_elements import describe_element
from hamdan_tracking import load_register, overlay_issues
from hamdan_issue_workflow import IssueEditor, issue_dashboard


class IssueOption(forms.TemplateListItem):
    @property
    def name(self):
        row = self.item
        ids = ', '.join(str(value) for value in row.get('element_ids', [])) or 'Project check'
        names = ' / '.join(ref.get('display_name') or ref.get('name', '') for ref in row.get('element_refs', []))
        return u'[{}] {} | {} | {} | {} | IDs: {}'.format(
            row['severity'], row['kind'], row.get('workflow_status', row.get('status', 'Active')) + ' | ' + row.get('assignee', ''), names, row['message'], ids)

    @property
    def description(self):
        row = self.item
        refs = row.get('element_refs', [])
        elements = u'\n'.join(u'{} | {} | {} | {}'.format(
            ref['id'], ref.get('category_name', ''), ref.get('display_name') or ref.get('name', ''), ref.get('level', '')) for ref in refs)
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
    data = current_report(revit.doc)
    if data is None:
        forms.alert('Run a new scan in this open model first. Previous findings are not restored automatically.', title='Hamdan', warn_icon=False)
        return
    identity = data['review_model_identity']
    issues = list(data.get('review_issues', []))
    if not issues:
        forms.alert('No review issues were recorded in the current scan.', title='Hamdan', warn_icon=False)
        return
    # Upgrade readable labels in older compatible reports in memory only.
    for issue in issues:
        for ref in issue.get('element_refs', []):
            if not ref.get('display_name') and ref.get('unique_id'):
                try:
                    element = revit.doc.GetElement(ref['unique_id'])
                    if element is not None:
                        ref.update(describe_element(revit.doc, element,
                                   name_getter=lambda item: DB.Element.Name.GetValue(item)))
                except Exception:
                    pass
    # Saved UniqueIds remain authoritative. Never rebind an old finding to a reused numeric Id.
    while True:
        overlay_issues(issues, load_register(folder, identity))
        action = forms.CommandSwitchWindow.show(['Select & Zoom', 'Edit tracking', 'Open issue dashboard'],
                                                message='Hamdan - Review Issues | current scan only')
        if not action:
            return
        if action == 'Open issue dashboard':
            Process.Start('explorer.exe', issue_dashboard(folder, revit.doc.Title, issues))
            return
        chosen = forms.SelectFromList.show([IssueOption(row) for row in issues],
            title='Hamdan - Review Issues | {} | {}'.format(revit.doc.Title, data.get('timestamp', 'Document checks')),
            width=1150, height=700, multiselect=False, info_panel=True, button_name=action)
        if chosen is None:
            continue
        if isinstance(chosen, IssueOption):
            chosen = chosen.item
        if action == 'Edit tracking':
            IssueEditor(folder, identity, chosen).ShowDialog()
        else:
            locate_issue(revit.doc, revit.uidoc, chosen)
            return


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        forms.alert(str(error), title='Hamdan - Review Issues', warn_icon=False)
