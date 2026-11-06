from types import SimpleNamespace

from django.test import SimpleTestCase

from .table_sorting import apply_sequence_sort, sort_table_values


class TableSortingTests(SimpleTestCase):
    def test_currency_numeric_natural_and_blank_sorting(self):
        rows = [['₹1,200.00'], ['₹95.00'], [''], ['₹95.00']]
        self.assertEqual(
            sort_table_values(rows, 0, 'money', 'asc'),
            [['₹95.00'], ['₹95.00'], ['₹1,200.00'], ['']],
        )
        self.assertEqual(
            sort_table_values([['Item 10'], ['Item 2'], ['Item 1']], 0, 'text', 'asc'),
            [['Item 1'], ['Item 2'], ['Item 10']],
        )

    def test_dates_sort_chronologically_and_descending_blanks_stay_last(self):
        rows = [['31-12-2025'], ['01-01-2026'], ['']]
        self.assertEqual(
            sort_table_values(rows, 0, 'date', 'asc'),
            [['31-12-2025'], ['01-01-2026'], ['']],
        )
        self.assertEqual(
            sort_table_values(rows, 0, 'date', 'desc'),
            [['01-01-2026'], ['31-12-2025'], ['']],
        )

    def test_report_link_cells_sort_by_visible_value(self):
        rows = [[('Beta', '/b')], [('Alpha', '/a')]]
        self.assertEqual(sort_table_values(rows, 0, 'text', 'asc'), [rows[1], rows[0]])

    def test_sequence_sort_is_allowlisted_and_stable(self):
        rows = [SimpleNamespace(pk=3, label='Same'), SimpleNamespace(pk=1, label='Same'),
                SimpleNamespace(pk=2, label='Other')]
        request = SimpleNamespace(GET={'sort': 'label', 'direction': 'asc'})
        sorted_rows, key, direction = apply_sequence_sort(rows, request, {'label': ('label', 'text')})
        self.assertEqual([row.pk for row in sorted_rows], [2, 1, 3])
        self.assertEqual((key, direction), ('label', 'asc'))
        invalid = SimpleNamespace(GET={'sort': '__class__', 'direction': 'desc'})
        unchanged, key, direction = apply_sequence_sort(rows, invalid, {'label': ('label', 'text')})
        self.assertIs(unchanged, rows)
        self.assertEqual((key, direction), ('', ''))
