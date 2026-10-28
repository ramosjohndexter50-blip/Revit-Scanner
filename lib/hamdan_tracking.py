# -*- coding: utf-8 -*-
"""Per-model issue workflow; approvals never erase the geometric scan evidence."""
from __future__ import unicode_literals
import os
import re
from hamdan_audit import identity_key, stable_key
from hamdan_storage import read_json, update_json, timestamp

STATUSES = ('New', 'Active', 'Resolved', 'Approved')


def register_path(folder, identity):
    key = identity_key(identity)
    if identity.get('scan_run'):
        key += '_' + stable_key(identity['scan_run'])
    return os.path.join(folder, 'issues_' + key + '.json')


def load_register(folder, identity):
    data = read_json(register_path(folder, identity), {'version': 1, 'issues': {}})
    if data.get('version') != 1 or not isinstance(data.get('issues'), dict):
        raise ValueError('Unsupported issue register format.')
    return data


def edit_issue(folder, identity, issue, status, assignee, due_date, comment, reason, user, expected_revision=0):
    if status not in STATUSES:
        raise ValueError('Choose a valid issue status.')
    if due_date:
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', due_date):
            raise ValueError('Due date must be YYYY-MM-DD or blank.')
        from datetime import datetime
        datetime.strptime(due_date, '%Y-%m-%d')
    if status in ('Approved', 'Resolved') and not reason.strip():
        raise ValueError('Approval or manual resolution requires a reason.')
    key = issue['issue_id']

    def update(data):
        previous = data['issues'].get(key, {})
        if previous.get('revision', 0) != expected_revision:
            raise ValueError('This issue changed since it was opened. Reopen it before saving.')
        event = {'timestamp': timestamp(), 'user': user, 'status': status,
                 'assignee': assignee.strip(), 'due_date': due_date, 'comment': comment.strip(), 'reason': reason.strip()}
        history = previous.get('history', []) + [event]
        data['issues'][key] = dict(event, revision=expected_revision + 1, history=history,
                                  source_clash_id=issue.get('source_clash_id', ''), finding=issue.get('message', ''))
        return data['issues'][key]
    return update_json(register_path(folder, identity), update, {'version': 1, 'issues': {}})


def overlay_issues(issues, register):
    for issue in issues:
        state = register.get('issues', {}).get(issue['issue_id'], {})
        issue['detected_status'] = issue.get('status', 'Active')
        issue['workflow_status'] = state.get('status', issue['detected_status'])
        for key in ('assignee', 'due_date', 'reason', 'comment', 'revision', 'history'):
            issue[key] = state.get(key, [] if key == 'history' else 0 if key == 'revision' else '')
    return issues


def overlay_clash_approvals(clashes, register):
    states = dict((r.get('source_clash_id'), r) for r in register.get('issues', {}).values() if r.get('source_clash_id'))
    for clash in clashes:
        state = states.get(clash['clash_id'])
        if state and state.get('status') == 'Approved':
            clash['status'] = 'Approved'
            clash['approval_reason'] = state.get('reason', '')
        elif state and clash.get('status') == 'Approved':
            clash['status'] = 'Active'
        # Manual workflow resolution cannot prove that an intersection disappeared.
