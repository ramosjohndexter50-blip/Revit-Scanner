import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from hamdan_numbering import plan_numbers, find_conflicts, apply_assignments
from test_issue_review import extract_functions

FORMATTER = ROOT / 'Hamdan.tab/Sheets.panel/Prefix Sheet Numbers.pushbutton/script.py'


class NumberFormatterTests(unittest.TestCase):
    def rows(self):
        return [{'id': value, 'unique_id': 'uid{}'.format(value), 'old': 'A{}'.format(value),
                 'name': 'Room {}'.format(value), 'group': 'Sheets', 'editable': True}
                for value in (10, 2, 1)]

    def test_floor_tower_increment_and_natural_order(self):
        rows = self.rows()
        before = copy.deepcopy(rows)
        plan = plan_numbers(rows, '{floor}{tower}-{n:02}', 3, 1, 'G', 'A')
        self.assertEqual(['GA-03', 'GA-04', 'GA-05'], [row['new'] for row in plan])
        self.assertEqual([1, 2, 10], [row['id'] for row in plan])
        self.assertEqual(before, rows)

    def test_number_first_step_padding_and_count_down(self):
        plan = plan_numbers(self.rows(), '{n:03}-{floor}{tower}', 7, 2, 'G', 'A')
        self.assertEqual(['007-GA', '009-GA', '011-GA'], [row['new'] for row in plan])
        down = plan_numbers(self.rows(), '{n:02}', 3, -1)
        self.assertEqual(['03', '02', '01'], [row['new'] for row in down])

    def test_existing_prefix_and_unicode_codes(self):
        plan = plan_numbers(self.rows(), '{tower}-{old}', tower='Tour É')
        self.assertEqual('Tour É-A1', plan[0]['new'])

    def test_invalid_templates_and_sequences_are_blocked(self):
        for template in ('', '{}', '{n.__class__}', '{n[0]}', '{unknown}', '{n!r}', '{n:{old}}', '{n:099}', '{'):
            with self.subTest(template=template), self.assertRaises(ValueError):
                plan_numbers(self.rows(), template)
        for start, step in ((-1, 1), (1, 0), ('1.5', 1), (1, -2)):
            with self.subTest(start=start, step=step), self.assertRaises(ValueError):
                plan_numbers(self.rows(), '{n}', start, step)
        with self.assertRaises(ValueError):
            plan_numbers(self.rows(), 'Same-number')

    def test_conflicts_ignore_selected_old_numbers_but_block_others(self):
        rows = self.rows()
        plan = [dict(rows[0], new=rows[1]['old']), dict(rows[1], new=rows[0]['old'])]
        self.assertEqual([], find_conflicts(plan, rows))
        self.assertTrue(find_conflicts([dict(rows[0], new='a1')], rows))

    def test_rooms_in_different_phases_can_reuse_number(self):
        first = {'id': 1, 'unique_id': 'r1', 'group': 'phase1', 'old': '01', 'name': 'Room'}
        other = {'id': 2, 'unique_id': 'r2', 'group': 'phase2', 'old': 'GA-03', 'name': 'Room'}
        plan = plan_numbers([first], 'GA-{n:02}', start=3)
        self.assertEqual([], find_conflicts(plan, [first, other]))
        self.assertTrue(find_conflicts(plan, [first, dict(other, group='phase1')]))

    def exercise_transaction(self, fail=False):
        objects = {}
        transactions = []

        class Sheet:
            def __init__(self, uid, number):
                self.uid = uid
                self.number = number
            @property
            def SheetNumber(self):
                return self.number
            @SheetNumber.setter
            def SheetNumber(self, value):
                if fail and value == 'A1':
                    raise RuntimeError('Cannot edit this sheet')
                if any(other is not self and other.number == value for other in objects.values()):
                    raise ValueError('Duplicate sheet number')
                self.number = value

        objects['uid1'], objects['uid2'] = Sheet('uid1', 'A1'), Sheet('uid2', 'A2')
        def collect(doc, target):
            return [{'id': index, 'unique_id': uid, 'old': obj.number, 'name': 'Sheet',
                     'group': 'Sheets', 'editable': True} for index, (uid, obj) in enumerate(objects.items(), 1)]

        class Transaction:
            def __init__(self, doc, name):
                self.status = 'New'
                transactions.append(self)
            def Start(self):
                self.snapshot = {uid: obj.number for uid, obj in objects.items()}
                self.status = 'Started'
            def Commit(self):
                self.status = 'Committed'
                return self.status
            def RollBack(self):
                for uid, number in self.snapshot.items():
                    objects[uid].number = number
                self.status = 'RolledBack'
            def GetStatus(self):
                return self.status
            def Dispose(self):
                pass

        ns = {'collect_records': collect, 'find_conflicts': find_conflicts, 'apply_assignments': apply_assignments,
              'TARGET_PROPERTIES': {'Sheets': 'SheetNumber'}, 'uuid': SimpleNamespace(uuid4=lambda: SimpleNamespace(hex='test')),
              'DB': SimpleNamespace(Transaction=Transaction, TransactionStatus=SimpleNamespace(Started='Started', Committed='Committed'))}
        extract_functions(FORMATTER, ('apply_number_plan',), ns)
        plan = [dict(row, new='A2' if row['unique_id'] == 'uid1' else 'A1') for row in collect(None, 'Sheets')]
        document = SimpleNamespace(GetElement=lambda uid: objects.get(uid))
        if fail:
            with self.assertRaises(RuntimeError):
                ns['apply_number_plan'](document, 'Sheets', plan)
        else:
            ns['apply_number_plan'](document, 'Sheets', plan)
        return objects, transactions

    def test_two_pass_renumbering_supports_swaps(self):
        objects, transactions = self.exercise_transaction()
        self.assertEqual('A2', objects['uid1'].number)
        self.assertEqual('A1', objects['uid2'].number)
        self.assertEqual('Committed', transactions[0].status)

    def test_assignment_failure_rolls_back_all_numbers(self):
        objects, transactions = self.exercise_transaction(fail=True)
        self.assertEqual('A1', objects['uid1'].number)
        self.assertEqual('A2', objects['uid2'].number)
        self.assertEqual('RolledBack', transactions[0].status)


if __name__ == '__main__':
    unittest.main()
