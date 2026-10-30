"""Reference-driven creation, coordinate placement and atomic-write regressions."""
import copy
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from hamdan_dimensioning import (paper_offset, reference_groups, coordinates, world_point,
                                collect_plan, collect_reference_plan, apply_plan, geometry_records,
                                element_bounds, reject_existing, supported_category, CATEGORIES, EPS)

FRAME = {'origin': (0, 0, 20), 'right': (1, 0, 0), 'up': (0, 1, 0), 'normal': (0, 0, 1), 'scale': 100}


def record(position, stable=None, axis='Horizontal'):
    return {'position': position, 'stable': stable or str(position), 'axis': axis, 'owner': 'owner', 'label': 'Wall'}


class Vector:
    def __init__(self, x, y, z): self.X, self.Y, self.Z = x, y, z
    def CrossProduct(self, b):
        return Vector(self.Y*b.Z-self.Z*b.Y, self.Z*b.X-self.X*b.Z, self.X*b.Y-self.Y*b.X)


class DimensionCreationTests(unittest.TestCase):
    def test_chain_sorted_and_coincident_planes_have_no_zero_segments(self):
        rows = [record(10), record(0), record(5), record(5+EPS/2, 'coincident'), record(10)]
        groups, notes = reference_groups(rows, FRAME, [0, 0, 10, 20], ['Horizontal'], 'Chain', 5, 'Positive')
        self.assertEqual([0, 5, 10], [r['position'] for r in groups[0]['records']])
        self.assertAlmostEqual(20+500/304.8, groups[0]['start'][1])
        self.assertEqual(20, groups[0]['start'][2])
        self.assertEqual([], notes)

    def test_overall_uses_outer_faces_vertical_negative_paper_offset(self):
        groups, notes = reference_groups([record(i, axis='Vertical') for i in (4, 0, 9)],
            FRAME, [1, 0, 10, 9], ['Vertical'], 'Overall', 5, 'Negative')
        self.assertEqual([0, 9], [r['position'] for r in groups[0]['records']])
        self.assertAlmostEqual(1-500/304.8, groups[0]['start'][0])
        self.assertEqual((groups[0]['start'][0], 9, 20), groups[0]['end'])

    def test_rotated_view_axes_roundtrip_and_dimension_plane(self):
        frame = dict(FRAME, origin=(100, 200, 50), right=(0, 1, 0), up=(-1, 0, 0))
        self.assertEqual((97, 202, 50), world_point(frame, 2, 3))
        self.assertEqual((2, 3), coordinates((97, 202, 50), frame))
        groups, _ = reference_groups([record(0), record(10)], frame, [0, 0, 10, 2], ['Horizontal'], 'Overall', 2, 'Positive')
        self.assertEqual(50, groups[0]['start'][2])
        self.assertEqual(groups[0]['start'][0], groups[0]['end'][0])

    def test_cabinet_placement_puts_width_above_and_heights_left(self):
        rows = [record(0), record(10), record(0, axis='Vertical'), record(20, axis='Vertical')]
        groups, _ = reference_groups(rows, FRAME, [0, 0, 10, 20], ['Horizontal', 'Vertical'], 'Chain', 5, 'AboveLeft')
        self.assertGreater(groups[0]['start'][1], 20)
        self.assertLess(groups[1]['start'][0], 0)

    def test_invalid_offsets_axes_and_oversized_chains_rejected(self):
        for offset in (0, -1, float('nan'), float('inf'), 'bad', 101):
            with self.assertRaises(ValueError): paper_offset(offset, 100)
        for axes in ([], ['Bad'], ['Horizontal', 'Horizontal']):
            with self.assertRaises(ValueError):
                reference_groups([], FRAME, [0, 0, 1, 1], axes, 'Chain', 5, 'Positive')
        with self.assertRaises(ValueError):
            reference_groups([record(i) for i in range(129)], FRAME, [0, 0, 128, 2], ['Horizontal'], 'Chain', 5, 'Positive')

    def test_insufficient_axis_skipped_without_inventing_reference(self):
        groups, notes = reference_groups([record(0), record(10)], FRAME, [0, 0, 10, 10],
            ['Horizontal', 'Vertical'], 'Chain', 5, 'Positive')
        self.assertEqual(['Horizontal'], [g['axis'] for g in groups])
        self.assertIn('Vertical', notes[0])

    def test_grid_uses_native_element_reference_and_skips_curves(self):
        class Line:
            Direction = Vector(0, 1, 0)
            def GetEndPoint(self, index): return Vector(8, 0, 0)
        class Grid:
            Id = NS(Value=42)
            UniqueId, Category, Name, Curve = 'grid', NS(Name='Grids'), 'A', Line()
        DB = NS(Grid=Grid, Line=Line, XYZ=Vector,
                Reference=lambda element: NS(ElementId=element.Id, ConvertToStableRepresentation=lambda doc: 'grid-ref'))
        grid = Grid()
        rows = geometry_records(DB, None, grid, None, FRAME, ['Horizontal', 'Vertical'])
        self.assertEqual([record(8, 'grid-ref') | {'owner': 'grid', 'label': 'Grids | A | ID 42'}], rows)
        grid.Curve = object()
        self.assertEqual([], geometry_records(DB, None, grid, None, FRAME, ['Horizontal']))

    def test_collect_keeps_skipped_owners_for_stale_preview_validation(self):
        wall, unsupported = NS(UniqueId='wall', Id=NS(Value=1)), NS(UniqueId='skip', Id=NS(Value=2))
        with patch('hamdan_dimensioning.view_frame', return_value=FRAME), \
             patch('hamdan_dimensioning.supported_category', side_effect=lambda db, e: e is wall), \
             patch('hamdan_dimensioning.geometry_records', return_value=[record(0), record(10)]), \
             patch('hamdan_dimensioning.element_bounds', return_value=[0, 0, 10, 2]):
            plan = collect_plan(None, None, NS(UniqueId='view'), [wall, unsupported], ['Horizontal'], 'Overall', 5, 'Positive')
        self.assertEqual(['wall', 'skip'], plan['owners'])
        self.assertIn('ID 2', plan['notes'][0])

    def test_grid_curve_positions_line_when_view_bbox_unavailable(self):
        grid = NS(get_BoundingBox=lambda view: None,
                  Curve=NS(GetEndPoint=lambda i: Vector(10, i*20, 0)))
        self.assertEqual([10, 0, 10, 20], element_bounds(grid, None, FRAME))

    def test_existing_grid_dimensions_use_canonical_refs_and_owner_view_filter(self):
        class Grid:
            def __init__(self, uid): self.UniqueId = uid
        grids = {'a': Grid('a'), 'b': Grid('b')}
        class Collector:
            def __init__(self, *args): self.disposed = False; self.filtered = None
            def OfClass(self, cls): return self
            def WherePasses(self, filter): self.filtered = filter; return self
            def __iter__(self):
                return iter([NS(Id=NS(Value=8), References=[NS(ElementId=uid, ConvertToStableRepresentation=lambda doc: uid+':0:LINEAR') for uid in ('a', 'b')])])
            def Dispose(self): self.disposed = True
        collector = Collector()
        DB = NS(Grid=Grid, Dimension=object, FilteredElementCollector=lambda *args: collector, ElementOwnerViewFilter=lambda view: view)
        groups = [{'records': [dict(record(i, uid), owner=uid) for i, uid in enumerate(('a', 'b'))]}]
        with self.assertRaisesRegex(ValueError, 'existing dimension'):
            reject_existing(DB, NS(GetElement=lambda uid: grids[uid]), NS(Id='view'), groups)
        self.assertEqual('view', collector.filtered)
        self.assertTrue(collector.disposed)

    def test_casework_is_selectable_and_unrelated_categories_are_excluded(self):
        values = dict((name, index) for index, name in enumerate(CATEGORIES))
        DB = NS(BuiltInCategory=NS(**values))
        self.assertTrue(supported_category(DB, NS(Category=NS(Id=NS(Value=values['OST_Casework'])))))
        self.assertFalse(supported_category(DB, NS(Category=NS(Id=NS(Value=99999)))))

    def test_manual_chain_uses_only_picked_faces_and_edges(self):
        owner = NS(UniqueId='owner', Id=NS(Value=1))
        references = [NS(ElementId=owner.Id, ConvertToStableRepresentation=lambda doc, value=v: value) for v in ('edge0', 'edge3')]
        candidates = [record(i, 'edge'+str(i)) for i in range(4)]
        with patch('hamdan_dimensioning.view_frame', return_value=FRAME), \
             patch('hamdan_dimensioning.supported_category', return_value=True), \
             patch('hamdan_dimensioning.geometry_records', return_value=candidates) as read_geometry, \
             patch('hamdan_dimensioning.element_bounds', return_value=[0, 0, 3, 2]):
            plan = collect_reference_plan(NS(Grid=type('Grid', (), {})), NS(GetElement=lambda eid: owner), NS(UniqueId='view'),
                                          references, ['Horizontal'], 'Chain', 5, 'Positive')
        self.assertEqual(['edge0', 'edge3'], [r['stable'] for r in plan['groups'][0]['records']])
        self.assertEqual(1, read_geometry.call_count)
        self.assertTrue(read_geometry.call_args.kwargs['include_edges'])
        self.assertEqual('References', plan['selection_mode'])

    def test_unresolved_manual_reference_is_never_replaced_with_click_point(self):
        owner = NS(UniqueId='owner', Id=NS(Value=1))
        references = [NS(ElementId=owner.Id, GlobalPoint=Vector(i, 0, 20), ConvertToStableRepresentation=lambda doc: 'missing') for i in (0, 10)]
        with patch('hamdan_dimensioning.view_frame', return_value=FRAME), \
             patch('hamdan_dimensioning.supported_category', return_value=True), \
             patch('hamdan_dimensioning.geometry_records', return_value=[]), self.assertRaisesRegex(ValueError, 'No usable picked references'):
            collect_reference_plan(NS(Grid=type('Grid', (), {})), NS(GetElement=lambda eid: owner), NS(UniqueId='view'),
                                   references, ['Horizontal'], 'Overall', 5, 'Positive')

    def fixture(self, fail_second=False):
        calls = []
        class Transaction:
            def __init__(self, *args): self.status = 'Started'
            def Start(self): calls.append('start')
            def GetStatus(self): return self.status
            def Commit(self): calls.append('commit'); self.status = 'Committed'; return self.status
            def RollBack(self): calls.append('rollback')
            def Dispose(self): calls.append('dispose')
        class DimensionType:
            StyleType = 'Linear'
        class Refs(list):
            def Append(self, ref): self.append(ref)
        owner = NS(Id=NS(Value=1))
        view = NS(Id=NS(Value=2))
        objects = {'view': view, 'viewport': NS(ViewId=view.Id), 'style': DimensionType(), 'owner': owner}
        def create(*args):
            calls.append('create')
            if fail_second and calls.count('create') == 2: raise ValueError('Unsupported reference')
            return NS(NumberOfSegments=0, Value=10)
        document = NS(GetElement=lambda uid: objects.get(uid), Create=NS(NewDimension=create), Regenerate=lambda: None)
        DB = NS(Transaction=Transaction, TransactionStatus=NS(Started='Started', Committed='Committed'),
                DimensionType=DimensionType, DimensionStyleType=NS(Linear='Linear'), ReferenceArray=Refs,
                Reference=NS(ParseFromStableRepresentation=lambda doc, stable: NS(ElementId=owner.Id)),
                XYZ=lambda *args: args, Line=NS(CreateBound=lambda a, b: (a, b)))
        groups, _ = reference_groups([record(0), record(10)], FRAME, [0, 0, 10, 2], ['Horizontal'], 'Chain', 5, 'Positive')
        plan = dict(view='view', viewport='viewport', style='style', frame=FRAME, owners=['owner'],
                    axes=['Horizontal'], mode='Chain', offset_mm=5, side='Positive', groups=groups, notes=[])
        return DB, document, plan, calls

    def test_native_creation_only_inside_explicit_atomic_apply(self):
        DB, document, plan, calls = self.fixture()
        with patch('hamdan_dimensioning.collect_plan', return_value=copy.deepcopy(plan)), \
             patch('hamdan_dimensioning.reject_existing'):
            self.assertEqual(1, apply_plan(DB, document, plan))
        self.assertEqual(['start', 'create', 'commit', 'dispose'], calls)

    def test_manual_apply_revalidates_picked_references_not_all_element_faces(self):
        DB, document, plan, calls = self.fixture()
        plan.update(selection_mode='References', picked=[{'owner': 'owner', 'stable': 'a'}, {'owner': 'owner', 'stable': 'b'}])
        with patch('hamdan_dimensioning.collect_reference_plan', return_value=copy.deepcopy(plan)) as manual, \
             patch('hamdan_dimensioning.collect_plan', side_effect=AssertionError('Do not expand manual picks')), \
             patch('hamdan_dimensioning.reject_existing'):
            self.assertEqual(1, apply_plan(DB, document, plan))
        self.assertEqual(1, manual.call_count)
        self.assertEqual(['start', 'create', 'commit', 'dispose'], calls)

    def test_sheet_activation_posts_native_command_without_opening_a_model_tab(self):
        import ast
        path = Path(__file__).resolve().parents[1] / 'lib/hamdan_dimension_ui.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'current_viewport']
        class Sheet:
            Id = NS(Value=9)
        class Viewport:
            Id, SheetId, ViewId = NS(Value=10), Sheet.Id, NS(Value=11)
        calls = []
        document = NS(ActiveView=Sheet(), GetElement=lambda eid: NS())
        uidoc = NS(ActiveView='sheet-tab', Selection=NS(GetElementIds=lambda: [], SetElementIds=lambda ids: calls.append('select-viewport')))
        class Lists:
            @classmethod
            def __class_getitem__(cls, key): return list
        namespace = dict(DB=NS(ViewSheet=Sheet, Viewport=Viewport, ElementId=object), revit=NS(uidoc=uidoc), List=Lists,
                         id_value=lambda eid: eid.Value, view_frame=lambda *a: FRAME, choose_viewport=lambda d: Viewport(),
                         RevitCommandId=NS(LookupPostableCommandId=lambda command: command), PostableCommand=NS(ActivateView='activate'),
                         HOST_APP=NS(uiapp=NS(CanPostCommand=lambda command: True, PostCommand=lambda command: calls.append(command))),
                         forms=NS(alert=lambda *a, **k: calls.append('instructions')))
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), namespace)
        self.assertIsNone(namespace['current_viewport'](document))
        self.assertEqual('sheet-tab', uidoc.ActiveView)
        self.assertEqual(['select-viewport', 'instructions', 'activate'], calls)

    def test_stale_geometry_or_deleted_viewport_rejected_before_write(self):
        for changed in ('geometry', 'viewport'):
            DB, document, plan, calls = self.fixture()
            fresh = copy.deepcopy(plan)
            fresh['groups'][0]['records'][0]['position'] += 1
            if changed == 'viewport': plan['viewport'] = 'missing'
            with patch('hamdan_dimensioning.collect_plan', return_value=fresh), \
                 patch('hamdan_dimensioning.reject_existing'), self.assertRaises(ValueError):
                apply_plan(DB, document, plan)
            self.assertEqual(['start', 'rollback', 'dispose'], calls)

    def test_second_dimension_failure_rolls_back_whole_batch(self):
        DB, document, plan, calls = self.fixture(fail_second=True)
        plan['groups'].append(copy.deepcopy(plan['groups'][0]))
        with patch('hamdan_dimensioning.collect_plan', return_value=copy.deepcopy(plan)), \
             patch('hamdan_dimensioning.reject_existing'), self.assertRaises(ValueError):
            apply_plan(DB, document, plan)
        self.assertEqual(['start', 'create', 'create', 'rollback', 'dispose'], calls)


if __name__ == '__main__':
    unittest.main()
