import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import escape

from test_issue_review import AUDIT, extract_functions


class CategoryReportingTests(unittest.TestCase):
    def test_host_category_inventory_does_not_require_links(self):
        elements = [SimpleNamespace(Id=1, cat='OST_LightingFixtures'),
                    SimpleNamespace(Id=2, cat='OST_ElectricalEquipment'),
                    SimpleNamespace(Id=3, cat='OST_Walls')]
        ns = {'scan_tick': lambda *a, **kw: None, 'get_level_name': lambda el: 'L1',
              'category_bic_name': lambda el: el.cat, 'category_key': lambda el: el.cat,
              'doc': None, 'Element': None, 'element_id_value': int,
              'describe_element': lambda doc, el, **kw: {'id': el.Id, 'display_name': 'Sample {}'.format(el.Id)}}
        extract_functions(AUDIT, ('discipline_name', 'breakdown_tables'), ns)
        result = ns['breakdown_tables'](elements, [])
        self.assertEqual(2, result['per_discipline']['Electrical']['elements'])
        self.assertEqual(1, result['per_category']['OST_LightingFixtures']['records'])
        self.assertEqual(1, result['per_category']['OST_ElectricalEquipment']['samples'][0]['id'] - 1)

    def test_quick_html_labels_host_counts_and_omits_clash_table_columns(self):
        ns = {'io': io, 'html_escape': escape, 'text_type': str}
        extract_functions(AUDIT, ('report_file_link', 'html_table', 'export_html_dashboard'), ns)
        data = {'scan_scope': {'mode': 'Quick Scan'}, 'standards': {'model_health': {'links': []}},
                'breakdowns': {'per_discipline': {'Electrical': {'elements': 57}},
                               'per_category': {'OST_LightingFixtures': {'name': 'Lighting Fixtures',
                                   'group': 'Electrical', 'records': 57, 'samples': [{'id': 42, 'display_name': 'Lamp'}]}}}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'report.html'
            ns['export_html_dashboard'](data, str(path))
            html = path.read_text(encoding='utf-8')
            self.assertIn('Revit link instances: 0 (0 loaded)', html)
            self.assertIn('do not indicate linked disciplines', html)
            self.assertIn('Lighting Fixtures', html)
            self.assertIn('Lamp [ID 42]', html)
            self.assertNotIn('Clashes by Discipline', html)
            group_section = html.split('<h2>Active-model Category Groups</h2>')[1].split('</section>')[0]
            self.assertNotIn('<th>Hard', group_section)
            self.assertNotIn('<th>Clearance', group_section)


if __name__ == '__main__':
    unittest.main()
