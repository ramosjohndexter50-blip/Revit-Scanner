import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from hamdan_annotations import (DIMENSION_FIELDS, dimension_snapshot, dimension_changes, apply_dimensions,
                                tag_snapshot, tag_changes, apply_tags, leader_value, offset_value)


class Id:
    def __init__(self, value): self.Value = value
    def __eq__(self, other): return isinstance(other, Id) and self.Value == other.Value


INVALID = Id(-1)
Id.InvalidElementId = INVALID


class Vector:
    def __init__(self, x, y, z): self.X, self.Y, self.Z = x, y, z
    def Multiply(self, scale): return Vector(self.X * scale, self.Y * scale, self.Z * scale)
    def Add(self, other): return Vector(self.X + other.X, self.Y + other.Y, self.Z + other.Z)


class Dimension:
    def __init__(self):
        self.VersionGuid = 'v1'
        self.Id, self.OwnerViewId, self.GroupId = Id(2147483650), Id(312), INVALID
        self.UniqueId, self.Pinned, self.NumberOfSegments = 'dim-a', False, 0
        self.Value, self.ValueString = 10.0, '3048 mm'
        for field in DIMENSION_FIELDS: setattr(self, field, '')
    def GetTypeId(self): return Id(9)


class Tag:
    def __init__(self):
        self.VersionGuid, self.UniqueId = 'v1', 'tag-a'
        self.Id, self.OwnerViewId, self.GroupId, self.type_id = Id(10), Id(312), INVALID, Id(9)
        self.Pinned, self.IsOrphaned, self.HasLeader = False, False, True
        self.TagOrientation, self.TagHeadPosition = 'Horizontal', Vector(1, 2, 3)
    def GetTypeId(self): return self.type_id
    def GetTaggedElementIds(self): return [NS(LinkInstanceId=INVALID, LinkedElementId=INVALID, HostElementId=Id(123))]
    def GetValidTypes(self): return [Id(9), Id(11)]
    def ChangeTypeId(self, value): self.type_id = value; return INVALID


class AnnotationEditingTests(unittest.TestCase):
    def database(self):
        calls = self.calls = []
        class Tx:
            def __init__(self, *args): pass
            def Start(self): calls.append('start')
            def Commit(self): calls.append('commit'); return 'Committed'
            def GetStatus(self): return 'Started'
            def RollBack(self): calls.append('rollback')
            def Dispose(self): calls.append('dispose')
        return NS(Dimension=Dimension, IndependentTag=Tag, Transaction=Tx, ElementId=Id,
                  TransactionStatus=NS(Started='Started', Committed='Committed'), XYZ=Vector,
                  TagOrientation=NS(Horizontal='Horizontal', Vertical='Vertical'))

    def dimension_row(self, element, index=0, **updates):
        snapshot = dimension_snapshot(element)
        values = dict((f, snapshot['parts'][index][f]) for f in DIMENSION_FIELDS)
        values.update(updates)
        return {'unique_id': element.UniqueId, 'segment': index, 'snapshot': snapshot, 'values': values}

    def tag_row(self, tag, **updates):
        row = {'unique_id': tag.UniqueId, 'snapshot': tag_snapshot(tag), 'type_id': 9,
               'leader': 'Yes', 'orientation': 'Horizontal', 'right_mm': '0', 'up_mm': '0'}
        row.update(updates)
        return row

    def test_dimension_segment_clear_does_not_change_measured_value(self):
        dim = Dimension()
        a, b = Dimension(), Dimension()
        b.ValueOverride = 'CHECK'
        dim.NumberOfSegments, dim.Segments = 2, [a, b]
        plan = dimension_changes([self.dimension_row(dim, 1, ValueOverride='', Prefix='TYP '), self.dimension_row(dim, 0)])
        self.assertEqual(1, len(plan))
        db = self.database()
        apply_dimensions(db, NS(GetElement=lambda uid: dim), plan)
        self.assertEqual('', b.ValueOverride)
        self.assertEqual('TYP ', b.Prefix)
        self.assertEqual(10.0, b.Value)
        self.assertEqual('', a.Prefix)
        self.assertIn('commit', self.calls)

    def test_stale_dimension_measurement_blocks_all_writes(self):
        dim = Dimension()
        plan = dimension_changes([self.dimension_row(dim, Prefix='CHECK')])
        dim.Value = 12.0
        with self.assertRaisesRegex(ValueError, 'changed since preview'):
            apply_dimensions(self.database(), NS(GetElement=lambda uid: dim), plan)
        self.assertEqual('', dim.Prefix)
        self.assertIn('rollback', self.calls)

    def test_pinned_and_grouped_dimensions_rejected(self):
        for key, value in (('Pinned', True), ('GroupId', Id(50))):
            dim = Dimension()
            setattr(dim, key, value)
            plan = dimension_changes([self.dimension_row(dim, Suffix=' TYP')])
            with self.assertRaises(ValueError):
                apply_dimensions(self.database(), NS(GetElement=lambda uid: dim), plan)
            self.assertEqual('', dim.Suffix)

    def test_duplicate_dimension_segments_blocked(self):
        row = self.dimension_row(Dimension(), Above='NOTE')
        with self.assertRaises(ValueError): dimension_changes([row, row])

    def test_tag_noop_and_invalid_values(self):
        tag = Tag()
        self.assertEqual([], tag_changes([self.tag_row(tag)]))
        for changes in ({'leader': 'maybe'}, {'orientation': 'sideways'}, {'right_mm': 'nan'}, {'up_mm': 'inf'}, {'right_mm': 'two'}):
            with self.assertRaises(ValueError): tag_changes([self.tag_row(tag, **changes)])

    def test_tag_shift_uses_owner_view_axes_in_model_mm(self):
        tag = Tag()
        plan = tag_changes([self.tag_row(tag, right_mm='304.8', up_mm='609.6', leader='No', orientation='Vertical', type_id=11)])
        view = NS(IsTemplate=False, RightDirection=Vector(0, 1, 0), UpDirection=Vector(0, 0, 1))
        doc = NS(GetElement=lambda value: tag if value == 'tag-a' else view)
        self.assertEqual(1, apply_tags(self.database(), doc, plan))
        self.assertEqual((1, 3, 5), (tag.TagHeadPosition.X, tag.TagHeadPosition.Y, tag.TagHeadPosition.Z))
        self.assertFalse(tag.HasLeader)
        self.assertEqual('Vertical', tag.TagOrientation)
        self.assertEqual(11, tag.GetTypeId().Value)

    def test_tag_changed_reference_or_head_rejects_preview(self):
        tag = Tag()
        row = self.tag_row(tag, leader='No')
        tag.TagHeadPosition = Vector(2, 2, 3)
        with self.assertRaisesRegex(ValueError, 'changed since preview'):
            apply_tags(self.database(), NS(GetElement=lambda uid: tag), tag_changes([row]))
        self.assertTrue(tag.HasLeader)

    def test_incompatible_type_and_orphaned_tag_rollback(self):
        for type_id, orphaned in ((12, False), (11, True)):
            tag = Tag()
            tag.IsOrphaned = orphaned
            plan = tag_changes([self.tag_row(tag, type_id=type_id)])
            with self.assertRaises(ValueError):
                apply_tags(self.database(), NS(GetElement=lambda uid: tag), plan)
            self.assertEqual(9, tag.GetTypeId().Value)


if __name__ == '__main__': unittest.main()
