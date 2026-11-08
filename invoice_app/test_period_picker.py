"""Shared period picker: progressive disclosure, FY window and unchanged period maths."""
import re
from datetime import date
from unittest import mock

from django.http import QueryDict
from django.test import TestCase

from reporting import periods

TODAY = date(2026, 11, 8)


def picker(html):
    return re.search(r'<div class="standard-period-filter".*?</div>', html, re.S).group(0)


def visible(block):
    """Names of controls that are shown and enabled (i.e. would be submitted)."""
    names = []
    for lab in re.finditer(r'<label class="compact-filter-label"([^>]*)>(.*?)</label>', block, re.S):
        if ' hidden' in lab.group(1):
            continue
        for ctl in re.finditer(r'<(?:select|input)\b([^>]*)>', lab.group(2)):
            if ' disabled' not in ctl.group(1):
                names.append(re.search(r'name="([^"]+)"', ctl.group(1)).group(1))
    return names


@mock.patch('reporting.periods.today', lambda: TODAY)
class PeriodPickerTests(TestCase):
    def block(self, qs=''):
        r = self.client.get('/receipts/' + qs)
        self.assertEqual(r.status_code, 200)
        return picker(r.content.decode())

    def test_first_dropdown_labels_and_default(self):
        b = self.block()
        self.assertIn('<option value="calendar">Yearly</option>', b)
        self.assertIn('<option value="financial" selected>Financial year</option>', b)
        self.assertIn('<option value="custom">Custom date</option>', b)
        self.assertEqual(visible(b), ['period_mode', 'fy', 'period_type'])
        self.assertIn('<option value="full_fy" selected>Full year</option>', b)

    def test_yearly_shows_only_year_and_month(self):
        b = self.block('?period_mode=calendar&calendar_year=2025')
        self.assertEqual(visible(b), ['period_mode', 'calendar_year', 'calendar_month'])
        self.assertIn('<option value="">All months</option>', b)
        self.assertIn('>December</option>', b)

    def test_custom_shows_only_dates(self):
        b = self.block('?period_mode=custom&date_from=2026-04-01&date_to=2026-04-30')
        self.assertEqual(visible(b), ['period_mode', 'date_from', 'date_to'])

    def test_fy_breakdowns(self):
        self.assertEqual(visible(self.block('?period_mode=financial&fy=2026&period_type=quarterly&period_value=Q3')),
                         ['period_mode', 'fy', 'period_type', 'period_value'])
        b = self.block('?period_mode=financial&fy=2026&period_type=quarterly&period_value=Q3')
        self.assertIn('<option value="Q3" selected>Q3 (Oct\u2013Dec)</option>', b)
        b = self.block('?period_mode=financial&fy=2026&period_type=half_yearly&period_value=H2')
        self.assertIn('<option value="H2" selected>H2 (Oct\u2013Mar)</option>', b)
        self.assertEqual(visible(b).count('period_value'), 1)
        b = self.block('?period_mode=financial&fy=2026&period_type=monthly&period_value=1')
        months = re.search(r'data-period-value[^>]*>(.*?)</select>', b, re.S).group(1)
        self.assertEqual(re.findall(r'>(\w+)</option>', months),
                         ['April', 'May', 'June', 'July', 'August', 'September', 'October', 'November',
                          'December', 'January', 'February', 'March'])
        self.assertIn('<option value="1" selected>January</option>', months)
        self.assertNotIn('period_value', visible(self.block('?period_mode=financial&fy=2026&period_type=full_fy')))

    def test_fy_window_previous_five_current_next_one(self):
        b = self.block()
        fys = re.findall(r'<option value="(\d{4})"[^>]*>FY', b)
        self.assertEqual(fys, ['2027', '2026', '2025', '2024', '2023', '2022', '2021'])
        self.assertIn('<option value="2026" selected>FY 2026-27</option>', b)
        b = self.block('?period_mode=financial&fy=2015')
        self.assertIn('<option value="2015" selected>', b)

    def test_period_maths_unchanged(self):
        r = periods.resolve(QueryDict('period_mode=financial&fy=2026&period_type=quarterly&period_value=Q4'))
        self.assertEqual((r.start, r.end), (date(2027, 1, 1), date(2027, 3, 31)))
        r = periods.resolve(QueryDict('period_mode=financial&fy=2026&period_type=monthly&period_value=2'))
        self.assertEqual((r.start, r.end), (date(2027, 2, 1), date(2027, 2, 28)))
        r = periods.resolve(QueryDict('period_mode=financial&fy=2026&period_type=half_yearly&period_value=H1'))
        self.assertEqual((r.start, r.end), (date(2026, 4, 1), date(2026, 9, 30)))
        r = periods.resolve(QueryDict('period_mode=financial'))
        self.assertEqual((r.start, r.end), (date(2026, 4, 1), date(2027, 3, 31)))

    def test_js_toggles_without_apply(self):
        b = self.client.get('/receipts/').content.decode()
        self.assertIn("mode.addEventListener('change', sync)", b)
        self.assertIn("type.addEventListener('change', sync)", b)