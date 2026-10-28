"""Fresh runs must not revive archived findings or old workflow decisions."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from xml.sax.saxutils import escape
from hamdan_session import state_for, begin_state, publish_state, read_state
from hamdan_tracking import edit_issue, load_register
from test_issue_review import AUDIT, extract_functions


class FreshScanTests(unittest.TestCase):
    def setUp(self):
        self.records = []
        self.doc = NS(IsValidObject=True)
        self.identity = {'project_unique_id': 'project', 'path': 'a.rvt', 'session': 'same-hash'}

    def state(self, doc=None, **changes):
        return state_for(self.records, self.doc if doc is None else doc,
                         dict(self.identity, **changes), lambda a, b: a is b)

    def write_result(self, state, folder, name='scan.json'):
        identity = begin_state(state)
        path = Path(folder) / name
        path.write_text(json.dumps({'review_model_identity': identity, 'review_issues': [{'issue_id': name}]}))
        publish_state(state, identity, str(path))
        return identity, path

    def test_archives_never_load_without_current_scan(self):
        state = self.state()
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'audit_old.json').write_text(json.dumps({'review_model_identity': self.identity}))
            self.assertIsNone(read_state(state))
            self.write_result(state, folder)
            self.assertIsNotNone(read_state(self.state()))

    def test_reopen_same_path_and_hash_is_fresh(self):
        first = self.state()
        with tempfile.TemporaryDirectory() as folder:
            self.write_result(first, folder)
            self.doc.IsValidObject = False
            reopened = self.state(NS(IsValidObject=True))
            self.assertNotEqual(first['open_session'], reopened['open_session'])
            self.assertIsNone(read_state(reopened))
            self.assertEqual(1, len(self.records))

    def test_other_open_document_is_independent_and_save_as_resets(self):
        first = self.state()
        with tempfile.TemporaryDirectory() as folder:
            self.write_result(first, folder)
            other = self.state(NS(IsValidObject=True), path='b.rvt')
            self.assertIsNone(read_state(other))
            self.assertIsNotNone(read_state(self.state()))
            self.assertIsNone(read_state(self.state(path='renamed.rvt')))

    def test_new_wrapper_for_same_live_native_document_keeps_current_scan(self):
        state = self.state()
        with tempfile.TemporaryDirectory() as folder:
            self.write_result(state, folder)
            wrapper = NS(IsValidObject=True)
            same_native = state_for(self.records, wrapper, self.identity, lambda a, b: True)
            self.assertIsNotNone(read_state(same_native))
            self.assertEqual(1, len(self.records))

    def test_each_run_clears_previous_even_if_new_run_fails(self):
        state = self.state()
        with tempfile.TemporaryDirectory() as folder:
            old_identity, old_path = self.write_result(state, folder)
            new_identity = begin_state(state)
            self.assertNotEqual(old_identity['scan_run'], new_identity['scan_run'])
            self.assertIsNone(read_state(state))  # Cancellation/failure has nothing to publish.
            with self.assertRaises(ValueError):
                publish_state(state, old_identity, str(old_path))
            self.assertIsNone(read_state(state))

    def test_wrong_or_replaced_report_is_not_current(self):
        state = self.state()
        with tempfile.TemporaryDirectory() as folder:
            identity, path = self.write_result(state, folder)
            path.write_text(json.dumps({'review_model_identity': dict(identity, scan_run='different')}))
            self.assertIsNone(read_state(state))

    def test_approval_assignment_does_not_cross_scan_runs(self):
        state = self.state()
        with tempfile.TemporaryDirectory() as folder:
            first = begin_state(state)
            issue = {'issue_id': 'same-finding', 'source_clash_id': 'clash'}
            edit_issue(folder, first, issue, 'Approved', 'QA', '', 'test', 'verified', 'tester')
            self.assertEqual('Approved', load_register(folder, first)['issues']['same-finding']['status'])
            second = begin_state(state)
            self.assertEqual({}, load_register(folder, second)['issues'])

    def test_fresh_dashboard_has_no_misleading_trends_or_delta(self):
        namespace = {'io': io, 'html_escape': escape, 'text_type': str}
        extract_functions(AUDIT, ('report_file_link', 'html_table', 'export_html_dashboard'), namespace)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'fresh.html'
            namespace['export_html_dashboard']({'history_mode': 'fresh'}, str(path))
            html = path.read_text(encoding='utf-8')
            self.assertIn('Fresh scan | No previous comparison', html)
            self.assertIn('Current scan', html)
            self.assertNotIn('Warning Trends', html)
            self.assertNotIn('Delta:', html)
            self.assertNotIn('Duplicate Mark groups', html)
            self.assertIn('Custom duplicate-element and repeated-Mark checks are excluded', html)


if __name__ == '__main__':
    unittest.main()
