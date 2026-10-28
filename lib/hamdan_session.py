# -*- coding: utf-8 -*-
"""Current-open-document scan results; archived reports are never a live baseline."""
from __future__ import unicode_literals
import os
import uuid
from hamdan_storage import read_json

STORE_KEY = 'HAMDAN_FRESH_SCANS_V2'


def state_for(records, document, identity, same_document):
    records[:] = [r for r in records if r['document'].IsValidObject]
    for record in records:
        if same_document(record['document'], document):
            if record['model'] != identity:
                record.update(model=dict(identity), run=None, report=None)
            return record
    record = {'document': document, 'model': dict(identity), 'open_session': uuid.uuid4().hex,
              'run': None, 'report': None}
    records.append(record)
    return record


def begin_state(state):
    state.update(run=uuid.uuid4().hex, report=None)
    return dict(state['model'], open_session=state['open_session'], scan_run=state['run'])


def publish_state(state, scan_identity, path):
    if (scan_identity.get('scan_run') != state['run'] or
            scan_identity.get('open_session') != state['open_session']):
        raise ValueError('A newer scan has started. This result is not the current scan.')
    state['report'] = path


def read_state(state):
    path = state['report']
    if not state['run'] or not path or not os.path.isfile(path):
        return None
    try:
        data = read_json(path)
    except (ValueError, IOError, OSError):
        return None
    expected = dict(state['model'], open_session=state['open_session'], scan_run=state['run'])
    if not isinstance(data, dict) or data.get('review_model_identity') != expected:
        return None
    return data


def current_state(document):
    # pyRevit shares this small registry between command engines for this Revit process.
    # Revit can return a new managed wrapper for the same open native document.
    # Equals bridges those wrappers; IsValidObject removes a closed document first.
    from pyrevit.coreutils import envvars
    from System import Object
    from System.Diagnostics import Process
    from hamdan_issues import model_identity
    records = envvars.get_pyrevit_env_var(STORE_KEY)
    if records is None:
        records = []
        envvars.set_pyrevit_env_var(STORE_KEY, records)
    identity = model_identity(document, Process.GetCurrentProcess().Id)
    return state_for(records, document, identity, Object.Equals)


def begin_scan(document):
    return begin_state(current_state(document))


def publish_scan(document, scan_identity, json_path):
    publish_state(current_state(document), scan_identity, json_path)


def current_report(document):
    return read_state(current_state(document))
