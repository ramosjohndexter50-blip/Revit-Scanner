"""Run outside Revit: python -m unittest discover -s tests -v."""
import ast
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from hamdan_issues import attach_references, build_issues, identity_matches, latest_model_report

AUDIT = ROOT / 'Hamdan.tab/Audit.panel/Model Health & Clash Audit.pushbutton/script.py'
REVIEW = ROOT / 'Hamdan.tab/Audit.panel/Review Issues.pushbutton/script.py'


def extract_functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=body, type_ignores=[]), str(path), 'exec'), namespace)


class IssueReviewTests(unittest.TestCase):
    def test_issue_projection_and_resolved_exclusion(self):
        data = {'clashes': [
            {'kind': 'Hard', 'clash_id': 'active', 'element_a': {'id': 2147483650, 'level': 'L1'},
             'element_b': {'id': 2, 'level': 'L2'}, 'status': 'New'},
            {'kind': 'Hard', 'status': 'Resolved'}],
            'standards': {'required_parameters': [{'missing': [{'element_id': 3, 'parameter': 'Mark'}]}],
                          'revit_warnings': [{'severity': 'CRITICAL', 'failure_id': 'warning',
                                              'description': 'Overlap', 'element_ids': [4, 4, -1]}],
                          'model_health': {'duplicate_marks': [{'mark': 'D1', 'element_ids': [5, 6]}],
                                           'issues': [{'type': 'duplicate_marks', 'message': '2 marks'}]}}}
        rows = build_issues(data)
        self.assertEqual(4, len(rows))
        self.assertEqual('CRITICAL', rows[0]['severity'])
        self.assertEqual([4], rows[0]['element_ids'])
        self.assertTrue(all(row['suggested_fix'] for row in rows))
        self.assertTrue(any(2147483650 in row['element_ids'] for row in rows))
        self.assertEqual(rows, build_issues(data))

    def test_reference_lookup_is_cached_and_skips_deleted_elements(self):
        rows = build_issues({'standards': {'naming_issues': [
            {'name': 'Name', 'element_id': 9}, {'name': 'Other', 'element_id': 9}, {'element_id': 10}]}})
        calls = []

        def describe(value):
            calls.append(value)
            if value == 10:
                raise ValueError('Deleted')
            return {'id': value, 'unique_id': 'uid9', 'level': 'Level 2'}

        attach_references(rows, describe)
        self.assertEqual([9, 10], sorted(calls))
        self.assertEqual(2, sum(row['level'] == 'Level 2' for row in rows))
        self.assertTrue(any(row['element_ids'] == [10] and not row['element_refs'] for row in rows))

    def test_identity_does_not_match_another_model_or_unsaved_session(self):
        saved = {'project_unique_id': 'model-a', 'path': 'a.rvt', 'session': '1:2'}
        self.assertTrue(identity_matches(saved, dict(saved, session='9:10')))
        self.assertFalse(identity_matches(saved, dict(saved, project_unique_id='model-b')))
        self.assertFalse(identity_matches(saved, dict(saved, path='b.rvt')))
        unsaved = dict(saved, path='')
        self.assertTrue(identity_matches(unsaved, unsaved))
        self.assertFalse(identity_matches(unsaved, dict(unsaved, session='1:3')))
        self.assertFalse(identity_matches(None, saved))

    def test_report_loader_ignores_newer_unrelated_or_corrupt_reports(self):
        identity = {'project_unique_id': 'a', 'path': 'a.rvt', 'session': '1:2'}
        with tempfile.TemporaryDirectory() as folder:
            good = Path(folder) / 'audit_good.json'
            good.write_text(json.dumps({'review_model_identity': identity, 'marker': 'right'}))
            os.utime(good, (1, 1))
            (Path(folder) / 'audit_wrong.json').write_text(json.dumps({
                'review_model_identity': dict(identity, project_unique_id='other')}))
            (Path(folder) / 'audit_corrupt.json').write_text('{broken')
            (Path(folder) / 'audit_array.json').write_text('[]')
            self.assertEqual('right', latest_model_report(folder, identity)['marker'])
            self.assertIsNone(latest_model_report(folder, dict(identity, path='other.rvt')))

    def test_html_issue_register_escapes_content(self):
        namespace = {'io': io, 'html_escape': escape, 'text_type': str}
        extract_functions(AUDIT, ('report_file_link', 'html_table', 'export_html_dashboard'), namespace)
        data = {'review_issues': build_issues({'standards': {'naming_issues': [
            {'name': '<script>alert(1)</script>', 'element_id': 50, 'message': 'Invalid name'}]}})}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'dashboard.html'
            namespace['export_html_dashboard'](data, str(path))
            html = path.read_text(encoding='utf-8')
            self.assertIn('Review Issues', html)
            self.assertIn('Suggested Fix', html)
            self.assertIn('&lt;script&gt;', html)
            self.assertNotIn('<script>alert(1)</script>', html)

    def test_locator_uses_unique_ids_and_keeps_selection_if_zoom_fails(self):
        alerts, lookups, selection = [], [], []

        class NetList(list):
            def Add(self, item):
                self.append(item)

        class Element:
            Id = 2147483650

        class Document:
            def GetElement(self, uid):
                lookups.append(uid)
                return Element() if uid == 'still-present' else None

        class Selection:
            def SetElementIds(self, ids):
                selection.extend(ids)

        class UIDocument:
            def ShowElements(self, ids):
                raise ValueError('Not visible in this view')

        ui = UIDocument()
        ui.Selection = Selection()
        namespace = {'forms': type('Forms', (), {'alert': staticmethod(lambda msg, **kw: alerts.append(msg))}),
                     'List': {int: NetList}, 'ElementId': int, 'View': type('View', (), {})}
        extract_functions(REVIEW, ('locate_issue',), namespace)
        issue = {'suggested_fix': 'Review', 'element_refs': [
            {'id': 2147483650, 'unique_id': 'still-present'}, {'id': 7, 'unique_id': 'deleted'}]}
        namespace['locate_issue'](Document(), ui, issue)
        self.assertEqual(['still-present', 'deleted'], lookups)
        self.assertEqual([2147483650], selection)
        self.assertIn('could not zoom', alerts[0])


if __name__ == '__main__':
    unittest.main()
