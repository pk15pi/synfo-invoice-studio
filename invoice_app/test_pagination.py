"""Shared table pagination (t57u): page / page_size params, sizes 5..500, default 30."""
from django.test import RequestFactory, TestCase
from django.urls import reverse

from expense_management.models import Vendor
from invoice_app.pagination import DEFAULT_PAGE_SIZE, PAGE_SIZES, paginate, page_size_from


class PaginationHelperTests(TestCase):
    rf = RequestFactory()

    def test_sizes_and_default(self):
        self.assertEqual(PAGE_SIZES, (5, 10, 20, 30, 50, 75, 100, 200, 350, 500))
        self.assertEqual(DEFAULT_PAGE_SIZE, 30)
        for raw, want in [('', 30), ('abc', 30), ('7', 30), ('1000', 30), ('-5', 30), ('5', 5), ('500', 500), ('350', 350)]:
            self.assertEqual(page_size_from(self.rf.get('/', {'page_size': raw})), want, raw)

    def test_slices_and_bad_pages(self):
        rows = list(range(100))
        self.assertEqual(list(paginate(self.rf.get('/'), rows)), rows[:30])
        self.assertEqual(list(paginate(self.rf.get('/', {'page': '2'}), rows)), rows[30:60])
        self.assertEqual(list(paginate(self.rf.get('/', {'page': '99'}), rows)), rows[90:])
        self.assertEqual(list(paginate(self.rf.get('/', {'page': 'x'}), rows)), rows[:30])
        self.assertEqual(list(paginate(self.rf.get('/', {'t2_page_size': '10'}), rows, 't2_')), rows[:10])


class PaginationPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for i in range(65):
            Vendor.objects.create(name=f'Vend {i:03d}')

    def get(self, **params):
        return self.client.get(reverse('exp:vendors'), params)

    def test_default_30_with_selected_option(self):
        r = self.get()
        self.assertContains(r, 'Showing 1&ndash;30 of 65')
        self.assertContains(r, '<option value="30" selected>30</option>')
        for s in PAGE_SIZES:
            self.assertContains(r, f'<option value="{s}"')

    def test_size_and_page_two(self):
        self.assertContains(self.get(page_size=10), 'Showing 1&ndash;10 of 65')
        r = self.get(page=2)
        self.assertContains(r, 'Showing 31&ndash;60 of 65')
        names = [v.name for v in Vendor.objects.all()]
        self.assertContains(r, names[30]); self.assertNotContains(r, names[0] + '<')

    def test_filters_persist_and_fallbacks(self):
        r = self.get(q='Vend', page_size=10)
        self.assertContains(r, 'q=Vend&amp;page_size=10&amp;page=2')
        self.assertContains(r, '<input type="hidden" name="q" value="Vend">')  # size change keeps filters, resets page
        self.assertNotContains(r, '<input type="hidden" name="page"')
        self.assertContains(self.get(page_size=999), 'Showing 1&ndash;30 of 65')
        self.assertContains(self.get(page=999), 'Showing 61&ndash;65 of 65')

    def test_export_not_limited_to_page(self):
        from audit.models import AuditEvent
        n = AuditEvent.objects.count()
        if n <= 30:
            self.skipTest('vendors are not audited in this build')
        r = self.client.get(reverse('audit:export'))
        body = b''.join(r.streaming_content) if r.streaming else r.content
        self.assertGreaterEqual(len(body.decode('utf-8-sig').strip().splitlines()) - 1, n)
        self.assertContains(self.client.get(reverse('audit:log')), 'Showing 1&ndash;30 of')

class KeepPageSizeOnFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for i in range(65):
            Vendor.objects.create(name=f'Vend {i:03d}')

    def test_apply_keeps_chosen_size_and_resets_page(self):
        r = self.client.get(reverse('exp:vendors'), {'page_size': 50, 'page': 2})
        # The filter form carries page_size as a hidden field, but not page.
        self.assertContains(r, '<input type="hidden" name="page_size" value="50" data-keep-page-size>')
        self.assertNotContains(r, 'name="page" value="2" data-keep')
        # Submitting the filter form (filters + carried page_size) still uses 50 rows, from page 1.
        r = self.client.get(reverse('exp:vendors'), {'q': 'Vend', 'page_size': 50})
        self.assertContains(r, 'Showing 1&ndash;50 of 65')
        self.assertContains(r, '<option value="50" selected>50</option>')

    def test_fresh_visit_and_bad_size_not_carried(self):
        r = self.client.get(reverse('exp:vendors'))
        self.assertNotContains(r, 'data-keep-page-size')
        self.assertContains(r, 'Showing 1&ndash;30 of 65')
        self.assertNotContains(self.client.get(reverse('exp:vendors'), {'page_size': 999}), 'data-keep-page-size')
