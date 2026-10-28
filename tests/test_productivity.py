"""BEP, workflow, transactional edit and documentation review boundaries."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from hamdan_profiles import save_profile, activate_profile, effective_config, validate_rules
from hamdan_storage import atomic_json, update_json, read_json
from hamdan_tracking import edit_issue, load_register, overlay_issues, overlay_clash_approvals
from hamdan_editing import validate_sheet_numbers, atomic_edit, apply_parameters, parameter_snapshot
from hamdan_document_checks import rectangle_overlap, rectangle_outside, annotation_checks
from hamdan_issues import build_issues
from hamdan_reports import write_check_report


IDENTITY = {'project_unique_id': 'project', 'path': 'a.rvt'}


class ProductivityTests(unittest.TestCase):
    def test_profiles_are_per_model_and_can_disable_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            save_profile(folder, 'Tower A', {'naming_regex': {}, 'tag_required_categories': ['OST_Windows']})
            activate_profile(folder, IDENTITY, 'Tower A')
            cfg = effective_config(folder, IDENTITY)
            self.assertEqual({}, cfg['naming_regex'])
            self.assertEqual('Tower A', cfg['bep_profile'])
            self.assertEqual(['OST_Windows'], cfg['tag_required_categories'])
            self.assertNotEqual('Tower A', effective_config(folder, dict(IDENTITY, path='b.rvt'))['bep_profile'])

    def test_invalid_profile_data_rejected_and_old_rules_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            save_profile(folder, 'A', {'naming_regex': {}})
            for rule in ({'naming_regex': {'views': '['}}, {'include_linked_models': 'false'},
                         {'hard_clash_categories': [['A']]}, {'sheet_layout_tolerance_mm': float('nan')},
                         {'score_thresholds': {'pass': 50, 'warning': 90}}, {'typo': True}):
                with self.assertRaises((ValueError, __import__('re').error)):
                    save_profile(folder, 'A', rule)
            activate_profile(folder, IDENTITY, 'A')
            self.assertEqual({}, effective_config(folder, IDENTITY)['naming_regex'])

    def test_malformed_storage_never_silently_resets(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'data.json'
            path.write_text('{broken')
            with self.assertRaises(ValueError):
                update_json(str(path), lambda data: data.update(a=1), {})
            self.assertEqual('{broken', path.read_text())
            self.assertFalse(Path(str(path) + '.lock').exists())

    def test_busy_register_preserves_existing_state(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'data.json')
            atomic_json(path, {'a': 1})
            Path(path + '.lock').write_text('')
            with self.assertRaises(ValueError):
                update_json(path, lambda data: data.update(a=2))
            self.assertEqual({'a': 1}, read_json(path))

    def test_tracking_reason_date_history_and_concurrent_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            issue = {'issue_id': 'x', 'source_clash_id': 'clash-x', 'message': 'Overlap'}
            with self.assertRaises(ValueError):
                edit_issue(folder, IDENTITY, issue, 'Approved', 'MEP', '', '', '', 'test')
            with self.assertRaises(ValueError):
                edit_issue(folder, IDENTITY, issue, 'Active', 'MEP', '2026-02-30', '', '', 'test')
            first = edit_issue(folder, IDENTITY, issue, 'Approved', 'MEP', '2026-11-01', 'Reviewed', 'Accepted opening', 'test')
            self.assertEqual(1, first['revision'])
            with self.assertRaises(ValueError):
                edit_issue(folder, IDENTITY, issue, 'Active', '', '', '', '', 'other', 0)
            second = edit_issue(folder, IDENTITY, issue, 'Active', 'AR', '', 'Reopened', '', 'test', 1)
            self.assertEqual(2, len(second['history']))
            self.assertEqual('Accepted opening', second['history'][0]['reason'])
            self.assertEqual({}, load_register(folder, dict(IDENTITY, path='b.rvt'))['issues'])

    def test_workflow_resolution_never_resolves_existing_geometry(self):
        register = {'issues': {'x': {'status': 'Resolved', 'source_clash_id': 'cx', 'reason': 'Reviewed'}}}
        clashes = [{'clash_id': 'cx', 'status': 'Active'}]
        overlay_clash_approvals(clashes, register)
        self.assertEqual('Active', clashes[0]['status'])
        rows = [{'issue_id': 'x', 'status': 'Active'}]
        overlay_issues(rows, register)
        self.assertEqual('Active', rows[0]['detected_status'])
        self.assertEqual('Resolved', rows[0]['workflow_status'])

    def test_reopening_approval_restores_active_clash(self):
        clashes = [{'clash_id': 'cx', 'status': 'New'}]
        overlay_clash_approvals(clashes, {'issues': {'x': {'source_clash_id': 'cx', 'status': 'Approved', 'reason': 'Intentional'}}})
        self.assertEqual('Approved', clashes[0]['status'])
        overlay_clash_approvals(clashes, {'issues': {'x': {'source_clash_id': 'cx', 'status': 'Active'}}})
        self.assertEqual('Active', clashes[0]['status'])

    def test_stable_issue_id_when_element_order_changes(self):
        data = {'document_findings': [{'type': 'layout_overlap', 'view_id': 9, 'element_ids': [1, 2], 'message': 'Overlap'}]}
        first = build_issues(data)[0]
        data['document_findings'][0]['element_ids'] = [2, 1]
        self.assertEqual(first['issue_id'], build_issues(data)[0]['issue_id'])

    def test_sheet_swaps_valid_but_unselected_collision_rejected(self):
        existing = [{'unique_id': 'a', 'number': 'A1'}, {'unique_id': 'b', 'number': 'A2'}]
        validate_sheet_numbers([{'unique_id': 'a', 'number': 'A2'}, {'unique_id': 'b', 'number': 'A1'}], existing)
        with self.assertRaises(ValueError):
            validate_sheet_numbers([{'unique_id': 'a', 'number': 'a2'}], existing)
        with self.assertRaises(ValueError):
            validate_sheet_numbers([{'unique_id': 'a', 'number': ''}], existing)

    def test_failed_write_rolls_back_entire_transaction(self):
        calls = []
        class Transaction:
            def __init__(self, *args): self.status = None
            def Start(self): self.status = 'Started'; calls.append('start')
            def Commit(self): calls.append('commit'); return 'Committed'
            def GetStatus(self): return self.status
            def RollBack(self): calls.append('rollback')
            def Dispose(self): calls.append('dispose')
        DB = NS(Transaction=Transaction, TransactionStatus=NS(Started='Started', Committed='Committed'))
        def fail(rows):
            calls.append('write-first')
            raise ValueError('Second value rejected')
        with self.assertRaises(ValueError):
            atomic_edit(DB, None, 'test', lambda: [1, 2], fail)
        self.assertEqual(['start', 'write-first', 'rollback', 'dispose'], calls)

    def test_stale_parameter_rejected_before_writes(self):
        writes = []
        parameter = NS(IsShared=False, Id=NS(Value=5), StorageType=NS(ToString=lambda: 'String'), HasValue=True,
                       AsString=lambda: 'changed elsewhere', Set=lambda value: writes.append(value))
        class Transaction:
            def __init__(self, *args): pass
            def Start(self): pass
            def GetStatus(self): return 'Started'
            def RollBack(self): pass
            def Dispose(self): pass
        DB = NS(Transaction=Transaction, TransactionStatus=NS(Started='Started'))
        document = NS(GetElement=lambda uid: NS(Parameters=[parameter]))
        with self.assertRaises(ValueError):
            apply_parameters(DB, document, [{'unique_id': 'x', 'key': 'id:5', 'snapshot': {'storage': 'String', 'raw': 'old', 'has_value': True}, 'new': 'new'}])
        self.assertEqual([], writes)

    def test_paper_space_tolerance_and_touching_edges(self):
        self.assertFalse(rectangle_overlap([0, 0, 1, 1], [1, 0, 2, 1]))
        self.assertTrue(rectangle_overlap([0, 0, 1, 1], [.9, .9, 2, 2]))
        self.assertFalse(rectangle_outside([-.001, 0, 1, 1], [0, 0, 1, 1], .01))
        self.assertTrue(rectangle_outside([-.1, 0, 1, 1], [0, 0, 1, 1], .01))

    def test_inventory_coverage_records_inaccessible_elements(self):
        from test_issue_review import AUDIT, extract_functions
        class Inaccessible:
            @property
            def Category(self):
                raise ValueError('Unavailable')
        class Collector:
            def __init__(self, doc): self.disposed = False
            def WhereElementIsNotElementType(self): return self
            def GetElementCount(self): return 3
            def __iter__(self): return iter([NS(Category=True, ViewSpecific=False), NS(Category=True, ViewSpecific=True), Inaccessible()])
            def Dispose(self): self.disposed = True
        collector = Collector(None)
        ns = {'doc': None, 'FilteredElementCollector': lambda doc: collector, 'SCAN_DIAGNOSTICS': [], 'CHECK_RESULTS': {}, 'scan_tick': lambda **kw: None}
        extract_functions(AUDIT, ('all_model_elements',), ns)
        self.assertEqual(1, len(ns['all_model_elements']()))
        self.assertTrue(collector.disposed)
        self.assertEqual('INCOMPLETE', ns['CHECK_RESULTS']['inventory']['status'])
        self.assertEqual(1, len(ns['SCAN_DIAGNOSTICS']))

    def test_annotation_view_tags_and_segments(self):
        from unittest.mock import patch
        view = NS(Id=NS(Value=1), IsTemplate=False, Name='A-Plan')
        dimension = NS(Id=NS(Value=10), NumberOfSegments=2, Segments=[NS(ValueOverride=''), NS(ValueOverride='CHECK')])
        target1, target2 = NS(Id=NS(Value=20), Name='Door'), NS(Id=NS(Value=21), Name='Door')
        tag = NS(IsOrphaned=False, GetTaggedLocalElementIds=lambda: [NS(Value=20)])
        DB = NS(Dimension='dimension', IndependentTag='tag', BuiltInCategory=NS(OST_Doors='doors'))
        def collect(*args, **kwargs):
            return {'dimension': [dimension], 'tag': [tag], 'doors': [target1, target2]}[kwargs.get('cls') or kwargs.get('category')]
        with patch('hamdan_document_checks.collect', collect):
            result = annotation_checks(DB, None, [view], {'tag_required_categories': ['OST_Doors']})
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual([10], result['findings'][0]['element_ids'])
        self.assertEqual([21], result['findings'][1]['element_ids'])

    def test_check_html_and_csv_escape_model_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_check_report(folder, 'Tags', '<model>', {'findings': [{'type': 'missing_tag', 'message': '=HYPERLINK("x")<script>', 'element_ids': [9]}], 'coverage': {'complete': True}})
            html = Path(path).read_text(encoding='utf-8')
            self.assertIn('&lt;model&gt;', html)
            self.assertIn('&lt;script&gt;', html)
            self.assertIn("'=HYPERLINK", Path(path.replace('.html', '.csv')).read_text(encoding='utf-8-sig'))


if __name__ == '__main__':
    unittest.main()
