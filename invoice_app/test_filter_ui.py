"""Shared filter bars: visible labels, Apply + Clear, applied-filters summary and More filters."""
import re

from django.test import TestCase

from .models import Client

PAGES = {
    'invoices': '/invoices/', 'customers': '/customers/', 'projects': '/projects/', 'receipts': '/receipts/',
    'recruitment': '/recruitment/', 'manpower': '/manpower/', 'journal': '/accounting/journal/',
    'expenses': '/expenses/all/', 'claims': '/expenses/claims/', 'review': '/expenses/claims/review/',
    'employees': '/employees/', 'payroll': '/payroll/', 'attendance': '/attendance/',
    'gst_input': '/compliance/gst/input/', 'report': '/reports/r/outstanding/', 'builder': '/reports/custom/',
    'doc_library': '/doc-index/', 'audit': '/audit/',
}
FORM_PAGES = [k for k in PAGES if k not in ('customers', 'projects')]


def filter_block(html):
    m = re.search(r'<form[^>]*data-filter-form[^>]*>.*?</form>', html, re.S)
    return m.group(0) if m else ''


class FilterBarTests(TestCase):
    def get(self, url):
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200, url)
        return r.content.decode()

    def test_every_page_has_apply_and_clear_resetting_to_bare_url(self):
        for key, url in PAGES.items():
            html = self.get(url)
            self.assertIn('data-filter-actions', html, key)
            self.assertIn(f'href="{url}" data-filter-clear>Clear</a>', html, key)
            if key in FORM_PAGES:
                block = filter_block(html)
                self.assertIn('<button class="button primary" type="submit">Apply</button>', block, key)
                self.assertNotRegex(block, r'>\s*(Filter|Show|Reset)\s*</(button|a)>', key)

    def test_filter_controls_have_visible_labels(self):
        for key in FORM_PAGES:
            block = filter_block(self.get(PAGES[key]))
            for ctl in re.finditer(r'<(select|input)\b[^>]*name="([^"]+)"[^>]*>', block):
                if re.search(r'type="(hidden|checkbox|radio)"', ctl.group(0)):
                    continue
                before = block[:ctl.start()]
                self.assertGreater(before.count('<label'), before.count('</label>'), f'{key}: {ctl.group(2)} unlabeled')

    def test_customer_wording_not_client(self):
        for key in ('recruitment', 'manpower', 'receipts'):
            block = filter_block(self.get(PAGES[key]))
            self.assertIn('Customer', block, key)
            self.assertNotRegex(block, r'<label[^>]*>(<span>)?Client\b', key)
            self.assertNotIn('All clients', block, key)

    def test_summary_hidden_on_defaults_and_shown_when_filtered(self):
        c = Client.objects.create(name='Summary Co')
        self.assertNotIn('data-filter-summary', self.get('/receipts/'))
        self.assertNotIn('data-filter-summary', self.get('/customers/?status=all'))
        self.assertNotIn('data-filter-summary', self.get('/expenses/claims/review/'))
        html = self.get(f'/receipts/?client={c.pk}&q=UTR1')
        self.assertIn('<b>Filtered:</b>', html)
        self.assertIn('Customer: ', html)
        self.assertIn('Search: UTR1', html)
        html = self.get('/invoices/?period_mode=calendar&calendar_year=2025&calendar_month=10')
        self.assertIn('Period: October 2025', html)
        self.assertIn('Status: active', self.get('/projects/?status=active'))

    def test_more_filters_disclosure(self):
        for key in ('recruitment', 'expenses', 'claims'):
            html = self.get(PAGES[key])
            self.assertIn('<details class="more-filters"', html, key)
            self.assertNotRegex(html, r'<details class="more-filters"\s+open', key)
        self.assertRegex(self.get('/recruitment/?designation=Engineer'), r'<details class="more-filters"\s+open')
        self.assertRegex(self.get('/expenses/all/?vendor=1'), r'<details class="more-filters"\s+open')
        main = filter_block(self.get('/recruitment/')).split('<details')[0]
        for name in ('client', 'status', 'q'):
            self.assertIn(f'name="{name}"', main)

    def test_report_with_many_filters_folds_them(self):
        from reporting import reports as R
        key = next(k for k, f in R.FILTERS.items() if len(f) > 3)
        html = self.get(f'/reports/r/{key}/')
        self.assertIn('<details class="more-filters"', html)
        self.assertIn('More options: columns', self.get('/reports/custom/'))

    def test_projects_actions_separate_from_filters(self):
        html = self.get('/projects/')
        bar = re.search(r'<div class="filters list-filters" data-filter-bar>.*?</div>', html, re.S).group(0)
        self.assertNotIn('Description templates', bar)
        self.assertNotIn('Add project', bar)
        self.assertIn('<div class="list-actions">', html)
        self.assertIn('<span>Search</span>', bar)