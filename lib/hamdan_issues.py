# -*- coding: utf-8 -*-
"""Portable audit-to-issue projection. No Revit imports or model writes."""
import hashlib
import io
import json
import os

SEVERITY_ORDER = {'CRITICAL': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3}


def valid_ids(values):
    result = []
    for value in values:
        try:
            number = int(value)
            if number > 0 and number not in result:
                result.append(number)
        except (ValueError, TypeError):
            continue
    return result


def build_issues(data):
    """Create actionable rows from current findings, including old audit JSON."""
    issues = []

    def add(kind, severity, message, ids, fix, status='Active', source='', levels=None):
        if status == 'Resolved':
            return
        ids = valid_ids(ids)
        key = json.dumps([kind, source or message, sorted(ids)], ensure_ascii=True)
        issues.append({
            'issue_id': hashlib.sha1(key.encode('utf-8')).hexdigest()[:12],
            'kind': kind, 'severity': severity.upper(), 'message': message,
            'element_ids': ids, 'element_refs': [], 'suggested_fix': fix,
            'status': status, 'level': ' / '.join(sorted(set(levels or []))) or 'Not assigned'
        })

    for clash in data.get('clashes', []):
        a, b = clash.get('element_a', {}), clash.get('element_b', {})
        add('Clash: ' + clash.get('kind', 'Candidate'),
            'HIGH' if clash.get('kind') == 'Hard' else 'MEDIUM',
            clash.get('message', 'Review clash candidate'), [a.get('id'), b.get('id')],
            ('Positive-volume solid intersection; coordinate an adjustment or opening.'
             if clash.get('evidence') == 'Solid intersection' else
             'Bounding-box clearance candidate; verify the actual separation before changing geometry.') +
            (' Linked components: select the host link and use the saved viewpoint/IDs to locate the component.'
             if a.get('linked_unique_id') or b.get('linked_unique_id') else ''),
            clash.get('status', 'Active'), clash.get('clash_id', ''),
            [r.get('level', 'Not assigned') for r in (a, b)])
        if issues and clash.get('status') != 'Resolved':
            issues[-1]['source_clash_id'] = clash.get('clash_id', '')

    for finding in data.get('document_findings', []):
        source = '{}:{}:{}'.format(finding.get('type'), finding.get('view_id', ''), finding.get('sheet_id', ''))
        add('Document: ' + finding.get('type', 'Review'), finding.get('severity', 'MEDIUM'),
            finding.get('message', ''), finding.get('element_ids', []),
            'Open the referenced view or sheet and verify the configured BEP requirement before editing.', source=source)

    standards = data.get('standards', {})
    for warning in standards.get('revit_warnings', []):
        add('Revit warning', warning.get('severity', 'MEDIUM'),
            '{} occurrence(s): {}'.format(warning.get('count', 1), warning.get('description', '')),
            warning.get('element_ids', []),
            'Inspect the affected elements and the corresponding Revit warning. '
            'Confirm the cause before changing joins, constraints, marks, or room boundaries.',
            source=warning.get('failure_id', ''))
    for finding in standards.get('naming_issues', []):
        add('Naming', 'LOW', '{}: {}'.format(finding.get('name', ''), finding.get('message', '')),
            [finding.get('element_id')],
            'Check the {} naming regex in audit_config.json, then rename only if it matches your BEP.'.format(
                finding.get('rule', 'configured')))
    for category in standards.get('required_parameters', []):
        for finding in category.get('missing', []):
            add('Required parameter', 'MEDIUM',
                '{}: missing {}'.format(finding.get('element_name', ''), finding.get('parameter', 'value')),
                [finding.get('element_id')],
                'Check instance and type properties for {}. Fill the required value or correct the configured rule.'.format(
                    finding.get('parameter', 'this parameter')))
    fixes = {
        'workset_issues': 'Review the workset settings and ownership with the model coordinator.',
        'link_and_view_issues': 'Review the linked model transform or view settings against the coordination requirements.',
        'performance_flags': 'Review this view or family before changing its section box or content.'
    }
    for group in ('workset_issues', 'link_and_view_issues', 'performance_flags'):
        for finding in standards.get(group, []):
            if finding.get('type', '').endswith('_info'):
                continue
            add(group.replace('_', ' ').title(), 'MEDIUM', finding.get('message', ''),
                [finding.get('element_id')] + finding.get('element_ids', []), fixes[group])

    health = standards.get('model_health', {})
    for room_id in health.get('unplaced_rooms', []):
        add('Unplaced room', 'MEDIUM', 'Room is not placed', [room_id],
            'Locate the room in a room schedule and place it on the intended level, or remove it after review.')
    for room_id in health.get('unenclosed_rooms', []):
        add('Room with zero area', 'MEDIUM', 'Placed room has zero area', [room_id],
            'Review room boundaries, phase and redundant rooms on the intended level.')
    for finding in health.get('unplaced_views', []):
        add('View not on sheet', 'LOW', finding.get('name', 'Printable view not placed on a sheet'),
            [finding.get('id')], 'Place this view on the intended sheet or confirm that it is a working view.')
    for finding in health.get('imported_cad_elements', []):
        add('Imported CAD', 'MEDIUM', finding.get('name', 'Imported CAD instance'), [finding.get('id')],
            'Review the CAD import, its layer visibility, and whether a linked CAD workflow is appropriate.')
    for finding in health.get('issues', []):
        if finding.get('type') == 'duplicate_marks':
            continue
        represented = {
            'unplaced_rooms': health.get('unplaced_rooms'),
            'unplaced_views': health.get('unplaced_views'), 'imported_cad': health.get('imported_cad_elements')
        }
        if represented.get(finding.get('type')):
            continue
        add('Model health', finding.get('severity', 'LOW'), finding.get('message', ''),
            finding.get('element_ids', []), 'Review this project-level finding in the audit report.')
    return sorted(issues, key=lambda row: (SEVERITY_ORDER.get(row['severity'], 4), row['kind'], row['message']))


def attach_references(issues, describe):
    """Resolve element metadata once per ID; describe is provided by the Revit host."""
    cache = {}
    for issue in issues:
        refs = []
        for element_id in issue['element_ids']:
            if element_id not in cache:
                try:
                    cache[element_id] = describe(element_id)
                except Exception:
                    cache[element_id] = None
            if cache[element_id]:
                refs.append(cache[element_id])
        issue['element_refs'] = refs
        levels = sorted(set(r.get('level') for r in refs if r.get('level') not in (None, '', 'Unassigned', 'Not assigned')))
        if levels:
            issue['level'] = ' / '.join(levels)
    return issues


def identity_matches(saved, current):
    """Unsaved models may only reuse snapshots from the same Revit document session."""
    if not isinstance(saved, dict) or not saved.get('project_unique_id') or not current.get('project_unique_id'):
        return False
    if saved.get('project_unique_id') != current.get('project_unique_id'):
        return False
    if saved.get('path') != current.get('path'):
        return False
    if not current.get('path'):
        return saved.get('session') == current.get('session') and bool(current.get('session'))
    return True


def latest_model_report(folder, current):
    if not os.path.isdir(folder):
        return None
    paths = [os.path.join(folder, name) for name in os.listdir(folder)
             if name.startswith('audit_') and name.endswith('.json')]
    for path in sorted(paths, key=os.path.getmtime, reverse=True):
        try:
            with io.open(path, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
            if identity_matches(data.get('review_model_identity'), current):
                return data
        except (ValueError, IOError, OSError, TypeError, AttributeError):
            continue
    return None


def model_identity(document, process_id):
    return {
        'project_unique_id': document.ProjectInformation.UniqueId,
        'path': (document.PathName or '').lower(),
        'session': '{}:{}'.format(process_id, document.GetHashCode())
    }
