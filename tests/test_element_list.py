import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from hamdan_elements import ElementListState, describe_element


class ElementListTests(unittest.TestCase):
    def window_class(self, namespace):
        path = ROOT / 'Hamdan.tab/Elements.panel/Element List.pushbutton/script.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ElementWindow')
        node.bases = [ast.Name(id='object', ctx=ast.Load())]
        module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
        exec(compile(module, str(path), 'exec'), namespace)
        return namespace['ElementWindow']

    def sample(self, legacy=False):
        family = SimpleNamespace(Name='W-Aluminum_Fixed_Exterior')
        symbol = SimpleNamespace(Name='1200 x 3180', FamilyName=family.Name, Family=family)
        element = SimpleNamespace(Name=symbol.Name, Symbol=SimpleNamespace(Family=family),
                                  Id=SimpleNamespace(IntegerValue=123) if legacy else SimpleNamespace(Value=2147483650),
                                  UniqueId='window-unique', LevelId='level', GetTypeId=lambda: 'type',
                                  Category=SimpleNamespace(Name='Windows'))
        doc = SimpleNamespace(GetElement=lambda key: {'type': symbol, 'level': SimpleNamespace(Name='Level 2')}.get(key))
        return doc, element

    def test_family_and_type_match_revit_properties(self):
        doc, element = self.sample()
        row = describe_element(doc, element)
        self.assertEqual('W-Aluminum_Fixed_Exterior — 1200 x 3180', row['display_name'])
        self.assertEqual(2147483650, row['id'])
        self.assertEqual('Windows', row['category_name'])
        self.assertEqual('Level 2', row['level'])

    def test_legacy_id_and_instance_name_fallback(self):
        doc, element = self.sample(legacy=True)
        element.GetTypeId = lambda: None
        del element.Symbol
        element.Name = 'Survey Point'
        element.LevelId = None
        row = describe_element(doc, element)
        self.assertEqual(123, row['id'])
        self.assertEqual('Survey Point', row['display_name'])
        self.assertEqual('Not assigned', row['level'])

    def test_duplicate_click_updates_row_without_duplicating(self):
        doc, element = self.sample()
        state = ElementListState()
        state.upsert([describe_element(doc, element)])
        element.Name = 'Updated instance name'
        state.upsert([describe_element(doc, element)])
        self.assertEqual(1, len(state.rows))
        self.assertEqual('Updated instance name', state.visible()[0]['name'])
        self.assertEqual(1, len(state.visible('Windows 2147483650')))
        self.assertEqual([], state.visible('Doors'))

    def test_limit_remove_and_clear_affect_list_only(self):
        state = ElementListState(limit=1)
        one = {'id': 1, 'unique_id': 'one', 'display_name': 'Fenêtre'}
        two = {'id': 2, 'unique_id': 'two', 'display_name': 'Door'}
        self.assertEqual(1, state.upsert([one, two]))
        self.assertEqual(1, len(state.visible('FENÊTRE')))
        state.remove(['one'])
        self.assertFalse(state.rows)
        state.upsert([two])
        state.clear()
        self.assertFalse(state.rows)

    def test_modeless_actions_schedule_api_work_and_do_not_transact(self):
        path = ROOT / 'Hamdan.tab/Elements.panel/Element List.pushbutton/script.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ElementWindow')
        handlers = {node.name: node for node in window.body if isinstance(node, ast.FunctionDef)}
        self.assertTrue(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr == 'execute_in_revit_context' for node in ast.walk(handlers['locate_clicked'])))
        self.assertFalse(any(isinstance(node, ast.Attribute) and node.attr in ('SetElementIds', 'ShowElements', 'Transaction')
                             for node in ast.walk(handlers['selection_changed'])))
        self.assertFalse(any(isinstance(node, ast.Attribute) and node.attr == 'Transaction' for node in ast.walk(tree)))

    def test_live_capture_does_not_mix_documents(self):
        doc, element = self.sample()
        doc.IsValidObject = True
        doc.GetHashCode = lambda: 42
        original_lookup = doc.GetElement
        doc.GetElement = lambda key: element if key == 'selected-id' else original_lookup(key)
        cls = self.window_class({'ElementListState': ElementListState, 'describe_element': describe_element})
        window = cls.__new__(cls)
        window.document_key = 42
        window.document_title = 'Model A'
        window.state = ElementListState()
        window.status_text = SimpleNamespace(Text='')
        window.render = lambda: None
        uidoc = SimpleNamespace(Document=doc, Selection=SimpleNamespace(GetElementIds=lambda: ['selected-id']))
        window.capture(SimpleNamespace(ActiveUIDocument=uidoc))
        self.assertEqual(1, len(window.state.rows))
        self.assertIn('W-Aluminum', window.state.visible()[0]['display_name'])
        wrong = SimpleNamespace(IsValidObject=True, GetHashCode=lambda: 99)
        window.capture(SimpleNamespace(ActiveUIDocument=SimpleNamespace(Document=wrong)))
        self.assertEqual(1, len(window.state.rows))
        self.assertIn('Switch back', window.status_text.Text)

    def test_close_defers_unsubscribe_to_api_context(self):
        pending, removed = [], []
        events = SimpleNamespace(execute_in_revit_context=lambda fn: pending.append(fn),
                                 remove_handler=lambda *args: removed.append(args))
        cls = self.window_class({'events': events, 'HOST_APP': SimpleNamespace(has_api_context=False)})
        window = cls.__new__(cls)
        window.selection_handler = 'handler'
        window.closed = False
        window.window_closed(None, None)
        self.assertTrue(window.closed)
        self.assertFalse(removed)
        pending[0]()
        self.assertEqual([('selection-changed', 'handler')], removed)
        self.assertIsNone(window.selection_handler)


if __name__ == '__main__':
    unittest.main()
