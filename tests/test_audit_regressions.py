"""Regression cases from the repository review; no live Revit model required."""
import csv
import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace as NS
import xml.etree.ElementTree as ET

from test_issue_review import AUDIT, ROOT, extract_functions
from hamdan_audit import warning_occurrence_key, penalty_rows, clash_history, identity_key
from hamdan_clashes import ClashEngine, transformed_box
from hamdan_exports import write_bcf21, write_clash_csv
from hamdan_elements import ElementListState
from hamdan_issues import build_issues
from hamdan_profiles import validate_rules
from hamdan_sheets import plan_sheets, create_batch


class AuditRegressionTests(unittest.TestCase):
    def test_inventory_does_not_scan_repeated_geometry_or_marks(self):
        class ModelElements:
            def __iter__(self):
                raise AssertionError('Inventory must not enumerate elements for duplicate checks.')
        def unavailable(*args):
            raise RuntimeError('Metadata unavailable in this fixture')
        ns = {'doc': NS(PathName='', IsWorkshared=False), 'os': os,
              'FilteredElementCollector': unavailable, 'collect_rooms': lambda *args: [],
              'DB': NS(), 'scan_tick': lambda: None}
        extract_functions(AUDIT, ('model_health_inventory',), ns)
        result = ns['model_health_inventory'](ModelElements())
        self.assertNotIn('duplicate_marks', result)
        self.assertEqual(0, result['room_count'])
        self.assertFalse(any(row.get('type') == 'duplicate_marks' for row in result['issues']))

    def test_duplicate_payloads_excluded_but_native_warnings_preserved(self):
        native = {'failure_id': 'native-duplicate', 'description': 'Duplicate instances',
                  'severity': 'CRITICAL', 'count': 1, 'element_ids': [1, 2],
                  'occurrences': [{'key': 'native-key', 'element_ids': [1, 2]}]}
        standards = {'duplicates': [{'element_a': 1, 'element_b': 2}],
                     'model_health': {'duplicate_marks': [{'mark': 'M1', 'element_ids': [1, 2]}],
                                      'issues': [{'type': 'duplicate_marks', 'severity': 'HIGH', 'count': 99}]}}
        self.assertEqual([], build_issues({'standards': standards}))
        legacy = validate_rules({'duplicate_location_tolerance_mm': 10,
                                 'health_weights': {'duplicate': 999, 'health_duplicate_marks': 999}})
        self.assertEqual([], penalty_rows([], standards, legacy))
        ns = {}
        extract_functions(AUDIT, ('flatten_warnings',), ns)
        self.assertEqual([], ns['flatten_warnings']([], standards))
        standards['revit_warnings'] = [native]
        rows = build_issues({'standards': standards})
        self.assertEqual(['Revit warning'], [row['kind'] for row in rows])
        self.assertEqual([1, 2], rows[0]['element_ids'])
        self.assertEqual(['native-key'], [row['key'] for row in ns['flatten_warnings']([], standards)])
        self.assertEqual(5, sum(row['impact'] for row in penalty_rows([], standards, legacy)))

    def test_warning_trend_tracks_changed_affected_elements(self):
        first = warning_occurrence_key('same failure', 'overlap', [1, 2])
        second = warning_occurrence_key('same failure', 'overlap', [1, 3])
        ns = {}
        extract_functions(AUDIT, ('compare_history',), ns)
        trends = ns['compare_history']([{'key': second}], {'warnings': [{'key': first}]})
        self.assertEqual({'NEW', 'RESOLVED'}, {r['trend'] for r in trends})
        self.assertEqual(first, warning_occurrence_key('same failure', 'overlap', [2, 1]))
        self.assertEqual([], ns['compare_history']([], {'warnings': [{'key': first}]}, complete=False))

    def test_resolved_clash_and_approval_survive_more_than_one_run(self):
        previous = {'clashes': [{'clash_id': 'approved', 'status': 'Approved'}],
                    'resolved_clashes': [{'clash_id': 'old', 'status': 'Resolved'}]}
        current = [{'clash_id': 'approved'}]
        resolved = clash_history(current, previous)
        self.assertEqual('Approved', current[0]['status'])
        self.assertEqual('old', resolved[0]['clash_id'])
        self.assertEqual([], clash_history([], previous, complete=False))

    def test_identity_survives_saved_model_reopen_but_not_unsaved_replacement(self):
        saved = {'project_unique_id': 'x', 'path': 'a.rvt', 'session': 'one'}
        self.assertEqual(identity_key(saved), identity_key(dict(saved, session='two')))
        unsaved = dict(saved, path='')
        self.assertNotEqual(identity_key(unsaved), identity_key(dict(unsaved, session='two')))

    def test_candidates_approved_clashes_and_information_do_not_reduce_score(self):
        clashes = [{'kind': 'Hard', 'evidence': 'Solid intersection', 'status': 'Approved'},
                   {'kind': 'Clearance', 'status': 'New'}]
        standards = {'duplicates': [{'element_a': 1, 'element_b': 2}],
                     'workset_issues': [{'type': 'workset_info'}],
                     'link_and_view_issues': [{'type': 'link_positioning_info'}]}
        self.assertEqual([], penalty_rows(clashes, standards, {}))
        clashes.append({'kind': 'Hard', 'evidence': 'Solid intersection', 'status': 'New'})
        self.assertEqual(8, sum(r['impact'] for r in penalty_rows(clashes, standards, {})))

    def test_failed_check_suppresses_health_score(self):
        ns = {'CHECK_RESULTS': {'worksets': {'status': 'FAILED'}}, 'penalty_rows': penalty_rows}
        extract_functions(AUDIT, ('health_score', 'fix_first'), ns)
        self.assertIsNone(ns['health_score']([], {}, {}))
        ns['CHECK_RESULTS'].clear()
        self.assertEqual(100, ns['health_score']([], {}, {}))

    def test_populated_type_mark_is_not_reported_missing(self):
        p = NS(HasValue=True, StorageType=NS(ToString=lambda: 'String'), AsString=lambda: 'D-01')
        typ = NS(get_Parameter=lambda key: p, LookupParameter=lambda name: p)
        element = NS(get_Parameter=lambda key: None, LookupParameter=lambda name: None,
                     GetTypeId=lambda: 10, Id=1)
        ns = {'doc': NS(GetElement=lambda key: typ), 'BuiltInParameter': NS(ALL_MODEL_TYPE_MARK=7),
              'SCAN_DIAGNOSTICS': [], 'text_type': str, 'element_id_value': int}
        extract_functions(AUDIT, ('get_param', 'get_param_text'), ns)
        self.assertEqual('D-01', ns['get_param_text'](element, 'Type Mark'))

    def test_inplace_root_is_not_mistaken_for_nested_component(self):
        class Family:
            SuperComponent = None
            Symbol = NS(Family=NS(IsInPlace=True))
        ns = {'FamilyInstance': Family}
        extract_functions(AUDIT, ('is_nested_family',), ns)
        self.assertFalse(ns['is_nested_family'](Family()))
        nested = Family()
        nested.SuperComponent = Family()
        self.assertTrue(ns['is_nested_family'](nested))

    def test_workset_health_counts_checkout_without_unsupported_element_properties(self):
        class Collector:
            def __init__(self, document): pass
            def WhereElementIsNotElementType(self): return self
            def __iter__(self):
                return iter([NS(Id=1, WorksetId=10), NS(Id=2, WorksetId=10)])
            def Dispose(self): pass
        class Worksets:
            def __init__(self, document): pass
            def OfKind(self, kind): return [NS(Id=10, Name='Workset1', IsOpen=False)]
        ns = {'doc': NS(IsWorkshared=True), 'FilteredElementCollector': Collector, 'element_id_value': int,
              'scan_tick': lambda: None, 'WorksetKind': NS(UserWorkset='user'),
              'DB': NS(FilteredWorksetCollector=Worksets, CheckoutStatus=NS(OwnedByCurrentUser=1, OwnedByOtherUser=2),
                       WorksharingUtils=NS(GetCheckoutStatus=lambda doc, eid: eid))}
        extract_functions(AUDIT, ('workset_health',), ns)
        rows = ns['workset_health']()
        self.assertEqual(2, rows[0]['count'])
        self.assertEqual('workset_closed_info', rows[1]['type'])
        self.assertEqual((1, 1), (rows[-1]['owned_by_current_user'], rows[-1]['owned_by_other_user']))

    def test_one_failed_health_check_does_not_stop_other_checks(self):
        calls = []
        def failed(): raise RuntimeError('Warning API failed')
        def forbidden_duplicates(*args): raise AssertionError('Duplicate detection must not run')
        ns = {'CHECK_RESULTS': {}, 'SCAN_DIAGNOSTICS': [], 'scan_tick': lambda *args: None,
              'document_warnings': failed, 'model_health_inventory': lambda e: calls.append('health') or {},
              'validate_naming': lambda c: [], 'required_parameter_check': lambda e, c: [],
              'workset_health': lambda: [], 'link_coordination_checks': lambda: [],
              'duplicate_detection': forbidden_duplicates, 'unused_and_performance_checks': lambda c: []}
        extract_functions(AUDIT, ('collect_standards',), ns)
        result = ns['collect_standards']([], {})
        self.assertEqual('FAILED', ns['CHECK_RESULTS']['revit_warnings']['status'])
        self.assertEqual('PASS', ns['CHECK_RESULTS']['model_health']['status'])
        self.assertEqual(['health'], calls)
        self.assertEqual(7, len(result))
        self.assertNotIn('duplicates', result)
        self.assertNotIn('duplicates', ns['CHECK_RESULTS'])

    def test_list_can_contain_all_elements_beyond_old_2000_limit(self):
        state = ElementListState()
        self.assertEqual(0, state.upsert({'id': i, 'unique_id': str(i), 'display_name': 'Element'} for i in range(2501)))
        self.assertEqual(2501, len(state.rows))

    def test_sheet_plan_increment_first_and_collision(self):
        rows = plan_sheets([], 3, '{n:02}-{floor}{tower}', 'Room {n:02}', 3, 2, 'G', 'A')
        self.assertEqual(['03-GA', '05-GA', '07-GA'], [r['new'] for r in rows])
        self.assertEqual('Room 07', rows[-1]['name'])
        with self.assertRaises(ValueError):
            plan_sheets([{'unique_id': 'existing', 'id': 10, 'group': 'Sheets', 'old': 'GA-03'}],
                        1, 'GA-{n:02}', 'Plan', 3)
        with self.assertRaises(ValueError):
            plan_sheets([], 501, 'A-{n}', 'Plan')

    def test_xaml_handlers_exist_on_python_windows(self):
        import ast
        for path in (ROOT / 'Hamdan.tab').rglob('*.xaml'):
            tree = ast.parse(path.with_name('script.py').read_text(encoding='utf-8'))
            methods = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
            for node in ET.parse(str(path)).iter():
                for event in ('Click', 'TextChanged', 'SelectionChanged', 'Checked', 'Unchecked'):
                    if node.get(event):
                        self.assertIn(node.get(event), methods, str(path))

    def test_failed_sheet_assignment_rolls_back_whole_batch(self):
        calls = []
        class Transaction:
            def __init__(self, *args): self.status = 'Started'
            def Start(self): calls.append('start')
            def GetStatus(self): return self.status
            def RollBack(self): calls.append('rollback')
            def Dispose(self): calls.append('dispose')
        def create(doc, titleblock):
            if 'created' in calls:
                raise RuntimeError('Revit rejected sheet')
            calls.append('created')
            return NS()
        DB = NS(Transaction=Transaction, ViewSheet=NS(Create=create), TransactionStatus=NS(Started='Started'))
        with self.assertRaises(RuntimeError):
            create_batch(DB, None, [{'new': 'A1', 'name': 'Plan'}, {'new': 'A2', 'name': 'Plan'}], None)
        self.assertEqual(['start', 'created', 'rollback', 'dispose'], calls)

    def sample_clashes(self, count=2):
        return [{'clash_id': 'test-' + str(i), 'kind': 'Hard', 'evidence': 'Solid intersection',
                 'status': 'New', 'message': 'Clash ' + str(i), 'point_internal': [-10, 20, 30],
                 'element_a': {'id': 2**32, 'unique_id': 'revit-a', 'display_name': '=HYPERLINK("bad")',
                               'name': 'Fenêtre', 'bbox_internal': [0, 0, 0, 1, 1, 1]},
                 'element_b': {'id': 2, 'unique_id': 'revit-b', 'name': '管道'}} for i in range(count)]

    def test_csv_python3_unicode_coordinates_and_formula_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'clashes.csv'
            write_clash_csv(self.sample_clashes(), str(path))
            with path.open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(2, len(rows))
            self.assertTrue(rows[0]['element_a_name'].startswith("'="))
            self.assertEqual(-3.048, float(rows[0]['x_m']))
            self.assertEqual('管道', rows[0]['element_b_name'])

    def test_bcf_independent_topics_metres_and_official_schemas(self):
        from lxml import etree
        schemas = {name: etree.XMLSchema(etree.parse(str(ROOT / 'tests/schemas' / filename)))
                   for name, filename in [('markup.bcf', 'markup.xsd'), ('viewpoint.bcfv', 'visinfo.xsd'), ('bcf.version', 'version.xsd')]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'clashes.bcf'
            write_bcf21(self.sample_clashes(25), str(path), 'Project')
            with zipfile.ZipFile(str(path)) as package:
                self.assertEqual(51, len(package.namelist()))
                for filename in package.namelist():
                    xml = etree.fromstring(package.read(filename))
                    schemas[filename.split('/')[-1]].assertValid(xml)
                    if filename.endswith('markup.bcf'):
                        self.assertEqual(1, len(xml.findall('Topic')))
                    elif filename.endswith('.bcfv'):
                        self.assertAlmostEqual(-3.048, float(xml.findtext('PerspectiveCamera/CameraViewPoint/X')))
            write_bcf21([], str(path), 'Empty')
            with zipfile.ZipFile(str(path)) as package:
                self.assertEqual(['bcf.version'], package.namelist())

    def test_transformed_link_bbox_uses_all_eight_corners(self):
        DB = NS(XYZ=lambda x, y, z: NS(X=x, Y=y, Z=z))
        transform = NS(OfPoint=lambda p: NS(X=10-p.Y, Y=20+p.X, Z=30+p.Z))
        self.assertEqual([6, 20, 30, 10, 22, 36], transformed_box(DB, [0, 0, 0, 2, 4, 6], transform))

    def test_loaded_but_unavailable_link_document_is_incomplete_not_unloaded(self):
        class Collector:
            def __init__(self, document): pass
            def OfClass(self, cls): return self
            def __iter__(self): return iter([link])
            def Dispose(self): pass
        link = NS(Id=NS(Value=10), GetTypeId=lambda: 20, GetLinkDocument=lambda: None)
        DB = NS(Transform=NS(Identity=object()), FilteredElementCollector=Collector, RevitLinkInstance=object,
                RevitLinkType=NS(IsLoaded=lambda doc, type_id: True))
        document = NS(Title='Host', GetHashCode=lambda: 1)
        notes = []
        engine = ClashEngine(DB, document, {}, lambda *args: None, None, notes)
        self.assertEqual([], engine.run())
        self.assertEqual(0, engine.stats['unloaded_links'])
        self.assertEqual(1, engine.stats['unavailable_link_documents'])
        self.assertFalse(engine.stats['complete'])
        self.assertIn('loaded', notes[0]['message'])

    def test_bbox_overlap_does_not_become_hard_without_positive_solid_volume(self):
        class Intersection:
            Volume = 0
            def Dispose(self): pass
        DB = NS(BooleanOperationsUtils=NS(ExecuteBooleanOperation=lambda *args: Intersection()),
                BooleanOperationsType=NS(Intersect='Intersect'))
        engine = ClashEngine(DB, NS(Title='Host'), {}, lambda *a: None, None, [])
        engine.geometry = lambda *args: [object()]
        source = {'key': 'linked'}
        rule = {'hard': True, 'clearance_mm': None}
        self.assertIsNone(engine.classify(NS(), source, [0, 0, 0, 3, 3, 3], NS(), source, [1, 1, 1, 4, 4, 4], rule))
        Intersection.Volume = 1
        self.assertEqual(('Hard', 'Solid intersection', 1), engine.classify(NS(), source, [0, 0, 0, 3, 3, 3],
            NS(), source, [1, 1, 1, 4, 4, 4], rule))


if __name__ == '__main__':
    unittest.main()
