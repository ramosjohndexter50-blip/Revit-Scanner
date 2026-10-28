# -*- coding: utf-8 -*-
"""Portable audit identity and scoring helpers."""
from __future__ import unicode_literals
import hashlib
import json


def stable_key(value):
    return hashlib.sha1(json.dumps(value, sort_keys=True, ensure_ascii=True).encode('utf-8')).hexdigest()


def identity_key(identity):
    values = [identity.get('project_unique_id'), identity.get('path')]
    if not identity.get('path'):
        values.append(identity.get('session'))
    return stable_key(values)


def warning_occurrence_key(failure_id, description, failing, additional=()):
    return 'revitwarning:' + stable_key([failure_id, description, sorted(failing), sorted(additional)])


def penalty_rows(clashes, standards, config):
    """The score and Fix First use the same evidence and weights."""
    weights = config.get('health_weights', {})
    rows = []

    def add(key, count, default, title, details):
        impact = count * max(0, float(weights.get(key, default)))
        if count and impact:
            rows.append({'impact': impact, 'count': count, 'title': title, 'details': details})

    active = [c for c in clashes if c.get('status') not in ('Approved', 'Resolved')]
    add('hard_clash', len([c for c in active if c.get('kind') == 'Hard' and c.get('evidence') == 'Solid intersection']),
        8, 'Resolve confirmed hard clashes', 'Review solid intersections and coordinate the affected geometry.')
    # AABB clearance candidates do not receive speculative score deductions.
    for severity in ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW'):
        count = sum(w.get('count', 1) for w in standards.get('revit_warnings', []) if w.get('severity') == severity)
        add(severity.lower() + '_revit_warning', count, {'CRITICAL': 5, 'HIGH': 3, 'MEDIUM': 1, 'LOW': 0}[severity],
            'Resolve {} Revit warnings'.format(severity.lower()), 'Inspect native warnings and affected elements.')
    add('missing_parameter', sum(len(c.get('missing', [])) for c in standards.get('required_parameters', [])),
        1, 'Complete required parameters', 'Review instance and type properties against the configured requirements.')
    add('naming_issue', len([i for i in standards.get('naming_issues', []) if i.get('type') == 'naming']),
        1, 'Fix naming convention violations', 'Check the configured BEP rules before renaming.')
    add('workset_issue', len([i for i in standards.get('workset_issues', []) if i.get('type') == 'workset_default']),
        2, 'Review default workset elements', 'Move elements only where the agreed workset strategy requires it.')
    add('performance_flag', len([i for i in standards.get('performance_flags', []) if i.get('type') == 'performance']),
        1, 'Review performance flags', 'Check the listed views or content before changing settings.')
    for issue in standards.get('model_health', {}).get('issues', []):
        if issue.get('type') == 'duplicate_marks':
            continue  # Legacy payloads must not restore the removed custom check.
        add('health_' + issue.get('type', ''), issue.get('count', 1),
            3 if issue.get('severity') == 'HIGH' else (2 if issue.get('severity') == 'MEDIUM' else 1),
            issue.get('message', 'Review model health'), 'Review the affected-item inventory in this report.')
    return sorted(rows, key=lambda row: (-row['impact'], row['title']))


def clash_history(clashes, previous, complete=True):
    old = dict((c['clash_id'], c) for c in
               (previous or {}).get('clashes', []) + (previous or {}).get('resolved_clashes', []))
    for clash in clashes:
        prior = old.get(clash['clash_id'])
        clash['status'] = ('Approved' if prior and prior.get('status') == 'Approved'
                           else 'Active' if prior and prior.get('status') != 'Resolved' else 'New')
    current = set(c['clash_id'] for c in clashes)
    if not complete:
        return []  # An incomplete geometry scan cannot prove resolution.
    return [dict(c, status='Resolved') for key, c in old.items() if key not in current]
