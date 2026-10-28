import ast
import io
import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from hamdan_scan import (PRESETS, ScanCancelled, ScanProgress, in_level_band,
                         pair_rules, preset_config, scope_record)
from test_issue_review import AUDIT, extract_functions
from hamdan_clashes import ClashEngine
from hamdan_audit import identity_key, clash_history


class ScanModeTests(unittest.TestCase):
    def test_pair_supports_hard_and_clearance(self):
        config = {'hard_clash_categories': [['A', 'B']], 'clearance_tolerances_mm': {'B|A': 304.8}}
        rule = pair_rules(config)[0]
        class Intersection:
            Volume = 1
            def Dispose(self): pass
        DB = SimpleNamespace(BooleanOperationsUtils=SimpleNamespace(ExecuteBooleanOperation=lambda *args: Intersection()),
                             BooleanOperationsType=SimpleNamespace(Intersect='Intersect'))
        engine = ClashEngine(DB, None, {}, lambda *args: None, None, [])
        engine.geometry = lambda *args: [object()]
        def hit(box):
            result = engine.classify(None, {'key': 'linked'}, [0, 0, 0, 2, 2, 2], None, {'key': 'linked'}, box, rule)
            return result[0] if result else None
        self.assertEqual('Hard', hit([1, 1, 1, 3, 3, 3]))
        self.assertEqual('Clearance', hit([2.5, 0, 0, 4, 2, 2]))
        self.assertIsNone(hit([4, 0, 0, 6, 2, 2]))
        self.assertIsNone(hit([2.8, 2.8, 0, 4, 4, 2]))  # diagonal gap exceeds a one-foot tolerance

    def test_level_band_includes_crossing_elements(self):
        self.assertTrue(in_level_band([0, 0, -3, 2, 2, 11], [0, 10]))
        self.assertFalse(in_level_band([0, 0, 10, 2, 2, 12], [0, 10]))
        self.assertTrue(in_level_band([0, 0, 10, 2, 2, 12], [10, None]))
        self.assertTrue(in_level_band([0, 0, -10, 2, 2, -2], None))

    def test_presets_do_not_mutate_user_config(self):
        original = {'hard_clash_categories': [['A', 'B']], 'clearance_tolerances_mm': {}}
        configured = preset_config(original, 'Structure vs Electrical', 50)
        self.assertEqual([['A', 'B']], original['hard_clash_categories'])
        self.assertIn('OST_StructuralFraming|OST_Conduit',
                      ['|'.join(reversed(pair)) for pair in configured['hard_clash_categories']])
        for value in (-1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                preset_config(original, 'Structure vs Electrical', value)

    def test_history_is_scoped_by_mode_config_and_level(self):
        namespace = {'os': os, 'json': __import__('json'), 'open_text_compat':
                     lambda path, mode: io.open(path, mode, encoding='utf-8')}
        extract_functions(AUDIT, ('load_previous_audit',), namespace)
        saved_scope = scope_record('Quick Scan', {'a': 1})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'audit_one.json'
            path.write_text(__import__('json').dumps({'document_key': 'model', 'scan_scope': saved_scope, 'schema_version': '3.0', 'audit_complete': True}))
            namespace['previous_audit_files'] = lambda: [str(path)]
            self.assertIsNotNone(namespace['load_previous_audit']('model', saved_scope))
            path.write_text(__import__('json').dumps({'document_key': 'model', 'scan_scope': saved_scope, 'schema_version': '3.1', 'audit_complete': True}))
            self.assertIsNotNone(namespace['load_previous_audit']('model', saved_scope))
            for scope in (scope_record('Full Audit', {'a': 1}), scope_record('Quick Scan', {'a': 2}),
                          scope_record('Quick Scan', {'a': 1}, level_id='L2')):
                self.assertIsNone(namespace['load_previous_audit']('model', scope))

    def test_progress_cancel_after_ui_pump_and_throttle(self):
        calls = []
        bar = SimpleNamespace(cancelled=False, title='')
        clock = [100.0]
        bar.update_progress = lambda current, total: calls.append((current, total))
        progress = ScanProgress(bar, lambda: clock[0])
        progress.pulse('Reading', 0, 100)
        progress.pulse(current=1)
        self.assertEqual(1, len(calls))
        clock[0] += 65
        progress.pulse(current=2)
        self.assertIn('1m 05s', bar.title)
        bar.update_progress = lambda *args: setattr(bar, 'cancelled', True)
        with self.assertRaises(ScanCancelled):
            progress.pulse('Next check')

    def run_mode(self, mode, cancel=False):
        calls = []
        data_out = []
        def reject_history(*args):
            raise AssertionError('Fresh scans must not load a previous report or issue register.')
        def tick(label=None, *args, **kwargs):
            if cancel and label == 'Preparing findings':
                raise ScanCancelled()
        ns = {
            'doc': SimpleNamespace(Title='Model', PathName='test.rvt'),
            'CHECK_RESULTS': {}, 'SCAN_DIAGNOSTICS': [], 'CLASH_COVERAGE': {},
            'identity_key': identity_key, 'clash_history': clash_history,
            'load_register': reject_history, 'overlay_clash_approvals': reject_history,
            'overlay_issues': lambda *args: None,
            'scan_tick': tick, 'all_model_elements': lambda: calls.append('inventory') or [],
            'detect_clashes': lambda *args: calls.append('clashes') or [],
            'collect_standards': lambda *args: calls.append('health') or {},
            'now_iso': lambda: '2026-10-28T12:00:00', 'datetime': datetime, 'safe_name': str, 'os': os,
            'REPORT_DIR': 'reports', 'CONFIG_PATH': 'config.json',
            'load_previous_audit': reject_history, 'flatten_warnings': lambda *args: [],
            'health_score': lambda *args: 100, 'compare_history': lambda *args: [],
            'fix_first': lambda *args: [], 'breakdown_tables': lambda *args: {},
            'model_identity': lambda *args: {}, 'Process': SimpleNamespace(GetCurrentProcess=lambda: SimpleNamespace(Id=1)),
            'begin_scan': lambda *args: {'scan_run': 'fresh-run'},
            'publish_scan': lambda *args: None,
            'attach_references': lambda *args: [], 'build_issues': lambda *args: [],
            'describe_review_element': lambda *args: None,
            'export_clashes_csv': lambda *args: calls.append('csv'),
            'export_bcf21': lambda *args: calls.append('bcf'), 'SCAN_PROGRESS': None,
            'export_html_dashboard': lambda data, path: data_out.append(data),
            'open_text_compat': lambda *args: io.StringIO(), 'json': __import__('json')}
        extract_functions(AUDIT, ('run_selected_scan',), ns)
        if cancel:
            with self.assertRaises(ScanCancelled):
                ns['run_selected_scan']({}, scope_record(mode, {}))
        else:
            ns['run_selected_scan']({}, scope_record(mode, {}))
        return calls, data_out

    def test_quick_scan_skips_clash_work_and_exports(self):
        calls, data = self.run_mode('Quick Scan')
        self.assertEqual(['inventory', 'health'], calls)
        self.assertEqual({'HTML dashboard', 'JSON results'}, set(data[0]['exports']))
        self.assertEqual('fresh', data[0]['history_mode'])
        self.assertIsNone(data[0]['previous_health_score'])
        self.assertEqual([], data[0]['warning_trends'])

    def test_clash_scan_skips_whole_model_health(self):
        calls, data = self.run_mode('Clash Scan')
        self.assertEqual(['clashes', 'csv', 'bcf'], calls)

    def test_full_audit_runs_both(self):
        calls, data = self.run_mode('Full Audit')
        self.assertEqual(['inventory', 'clashes', 'health', 'csv', 'bcf'], calls)

    def test_cancel_does_not_export_or_save_completed_history(self):
        calls, data = self.run_mode('Clash Scan', cancel=True)
        self.assertEqual(['clashes'], calls)
        self.assertEqual([], data)

    def test_html_marks_skipped_checks_and_scope(self):
        ns = {'io': io, 'html_escape': escape, 'text_type': str}
        extract_functions(AUDIT, ('report_file_link', 'html_table', 'export_html_dashboard'), ns)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'report.html'
            ns['export_html_dashboard']({'scan_scope': scope_record('Clash Scan', {}, level_name='Level 2')}, str(path))
            html = path.read_text(encoding='utf-8')
            self.assertIn('Not scanned', html)
            self.assertIn('Not included in this scan.', html)
            self.assertIn('Level 2', html)

    def test_native_clash_prefilter_and_both_sides_of_level_scope(self):
        a = SimpleNamespace(Id=1, category='A', box=[0, 0, 0, 2, 2, 10])
        near = SimpleNamespace(Id=2, category='B', box=[1, 1, 1, 3, 3, 2])
        above = SimpleNamespace(Id=3, category='B', box=[1, 1, 8, 3, 3, 9])
        far = [SimpleNamespace(Id=i + 10, category='B', box=[100 + i, 0, 0, 101 + i, 1, 1]) for i in range(100)]
        all_elements = [a, near, above] + far
        prefilters, compared = [], []

        class Outline:
            def __init__(self, low, high):
                self.box = list(low) + list(high)
            def Dispose(self):
                pass

        class Collector:
            def __init__(self, doc):
                self.elements = all_elements[:]
            def OfCategory(self, category):
                self.elements = [el for el in self.elements if el.category == category]
                return self
            def WhereElementIsNotElementType(self):
                return self
            def WherePasses(self, outline):
                prefilters.append(True)
                self.elements = [el for el in self.elements if all(
                    el.box[k + 3] >= outline.box[k] and outline.box[k + 3] >= el.box[k] for k in range(3))]
                return self
            def __iter__(self):
                return iter(self.elements)
            def Dispose(self):
                pass

        class Vector:
            def __init__(self, x, y, z): self.X, self.Y, self.Z = x, y, z
            def __iter__(self): return iter((self.X, self.Y, self.Z))
        identity = SimpleNamespace(OfPoint=lambda p: p)
        identity.Inverse = identity
        document = SimpleNamespace(Title='Model')
        class Engine(ClashEngine):
            def sources(self):
                return [{'document': document, 'key': 'host', 'root_link': None, 'transform': identity}]
            def bbox(self, element, source): return element.box
            def record(self, element, source, box): return {'id': element.Id}
            def classify(self, a, sa, ba, b, sb, bb, rule):
                compared.append(b.Id)
                return super().classify(a, sa, ba, b, sb, bb, rule)
            def geometry(self, *args): return [object()]
        for item in all_elements:
            item.UniqueId = str(item.Id)
            class TestId(int):
                @property
                def Value(self): return int(self)
            item.Id = TestId(item.Id)
        DB = SimpleNamespace(BuiltInCategory=SimpleNamespace(A='A', B='B'), FilteredElementCollector=Collector,
                             Outline=Outline, XYZ=Vector, BoundingBoxIntersectsFilter=lambda outline: outline,
                             BooleanOperationsType=SimpleNamespace(Intersect='Intersect'),
                             BooleanOperationsUtils=SimpleNamespace(ExecuteBooleanOperation=lambda *args:
                                 SimpleNamespace(Volume=1, Dispose=lambda: None)))
        ns = {'ClashEngine': Engine, 'DB': DB, 'doc': document, 'CLASH_COVERAGE': {}, 'CHECK_RESULTS': {},
              'SCAN_DIAGNOSTICS': [], 'scan_tick': lambda *a, **k: None,
              'element_is_ignored_pair': lambda *args: False}
        extract_functions(AUDIT, ('detect_clashes',), ns)
        findings = ns['detect_clashes']({'hard_clash_categories': [['A', 'B']]}, [0, 5])
        self.assertEqual(1, len(prefilters))
        self.assertEqual([2], compared)
        self.assertEqual(1, len(findings))

    def test_clash_menu_returns_selected_preset_level_and_tolerance(self):
        answers = iter(['Clash Scan', 'Structure vs Electrical'])
        level = SimpleNamespace(Name='Level 2', Elevation=10, Id=2, UniqueId='level-2')
        higher = SimpleNamespace(Name='Roof', Elevation=20, Id=3, UniqueId='roof')
        forms = SimpleNamespace(
            CommandSwitchWindow=SimpleNamespace(show=lambda *a, **k: next(answers)),
            SelectFromList=SimpleNamespace(show=lambda labels, **k: next(label for label in labels if 'ID 2' in label)),
            ask_for_string=lambda **k: '50', alert=lambda *a, **k: None)
        class Collector:
            def __init__(self, doc):
                pass
            def OfClass(self, cls):
                return self
            def ToElements(self):
                return [level, higher]
        ns = {'forms': forms, 'CONFIG': {}, 'PRESETS': PRESETS, 'preset_config': preset_config,
              'scope_record': scope_record, 'FilteredElementCollector': Collector,
              'doc': None, 'Level': object, 'element_id_value': int}
        extract_functions(AUDIT, ('choose_scan',), ns)
        config, scope = ns['choose_scan']()
        self.assertEqual('Clash Scan', scope['mode'])
        self.assertEqual([10, 20], scope['level_band_internal'])
        self.assertEqual('level-2', scope['level_unique_id'])
        self.assertEqual({50}, set(config['clearance_tolerances_mm'].values()))

    def test_cancel_scan_menu_exits(self):
        ns = {'forms': SimpleNamespace(CommandSwitchWindow=SimpleNamespace(show=lambda *a, **k: None))}
        extract_functions(AUDIT, ('choose_scan',), ns)
        self.assertIsNone(ns['choose_scan']())


if __name__ == '__main__':
    unittest.main()
