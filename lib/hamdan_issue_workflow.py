# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import io
import json
import os
from datetime import datetime
from xml.sax.saxutils import escape
from pyrevit import forms
from hamdan_elements import text_type
from hamdan_tracking import STATUSES, edit_issue, load_register, overlay_issues
from hamdan_reports import table
from hamdan_storage import atomic_json


class IssueEditor(forms.WPFWindow):
    def __init__(self, folder, identity, issue):
        forms.WPFWindow.__init__(self, os.path.join(os.path.dirname(__file__), 'hamdan_issue_editor.xaml'))
        self.folder, self.identity, self.issue = folder, identity, issue
        self.finding.Text = '{} | {}'.format(issue['issue_id'], issue['message'])
        self.status_box.ItemsSource = STATUSES
        self.status_box.SelectedItem = issue.get('workflow_status', 'Active')
        self.assignee_box.Text = issue.get('assignee', '')
        self.due_box.Text = issue.get('due_date', '')
        self.reason_box.Text = issue.get('reason', '')
        self.history_box.Text = '\n\n'.join('{} | {} | {} | {} | due {}\nReason: {}\n{}'.format(
            r.get('timestamp'), r.get('user'), r.get('status'), r.get('assignee'), r.get('due_date'), r.get('reason'), r.get('comment'))
            for r in issue.get('history', [])) or 'No workflow edits yet.'

    def save_clicked(self, sender, args):
        try:
            edit_issue(self.folder, self.identity, self.issue, self.status_box.SelectedItem,
                       self.assignee_box.Text, self.due_box.Text.strip(), self.comment_box.Text,
                       self.reason_box.Text, os.environ.get('USERNAME', 'unknown'), self.issue.get('revision', 0))
            self.Close()
        except Exception as error:
            self.error_text.Text = text_type(error)

    def cancel_clicked(self, sender, args):
        self.Close()


def issue_dashboard(folder, model, issues):
    path = os.path.join(folder, 'issue_register_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    atomic_json(path + '.json', {'model': model, 'issues': issues})
    html = '<!doctype html><html><head><meta charset="utf-8"><title>Hamdan Issue Register</title><style>body{font:14px Segoe UI;background:#f3f6fa;color:#13253e;margin:28px}table{background:white;border-collapse:collapse;width:100%}td,th{padding:12px;border-bottom:1px solid #ddd;text-align:left}th{background:#e8f1ff}input{padding:12px;width:90%;margin-bottom:18px}</style></head><body><h1>Hamdan - Issue Register</h1><p>' + escape(model) + '</p><p>Workflow resolution is a review decision. The detected status records the scan evidence; rerun the same scan scope to verify resolution.</p><input id="q" aria-label="Search issue register" placeholder="Search assignee, due date, status, ID or finding"/>' + table(
        ['ID', 'Severity', 'Detected', 'Workflow', 'Assignee', 'Due', 'Element IDs', 'Element names', 'Finding', 'Reason', 'Latest comment', 'History'], [
            [r['issue_id'], r['severity'], r.get('detected_status'), r.get('workflow_status'), r.get('assignee'), r.get('due_date'),
             ', '.join(str(v) for v in r.get('element_ids', [])),
             ' / '.join(ref.get('display_name', ref.get('name', '')) for ref in r.get('element_refs', [])),
             r['message'], r.get('reason'), r.get('comment'),
             '\n'.join('{} | {} | {} | {}'.format(h.get('timestamp'), h.get('user'), h.get('status'), h.get('comment')) for h in r.get('history', []))] for r in issues]) + '<script>document.getElementById("q").oninput=function(){var q=this.value.toLowerCase();document.querySelectorAll("tbody tr").forEach(function(r){r.hidden=r.textContent.toLowerCase().indexOf(q)<0})}</script></body></html>'
    with io.open(path + '.html', 'w', encoding='utf-8') as stream:
        stream.write(html)
    return path + '.html'
