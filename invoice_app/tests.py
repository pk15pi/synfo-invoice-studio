import json
import re
import shutil
import tempfile
from datetime import date
from decimal import Decimal as D
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from . import billing, services
from .models import BusinessProfile, Client, Invoice, ProjectTemplate
from .pdf import layout as L
from .pdf import render_invoice_pdf
from .pdf.from_models import build_invoice_data

ASSETS = Path(__file__).parent / 'assets'


class BillingTests(TestCase):
    def test_option_b_calendar_matches_sample(self):
        # Sample: 100000/month, 23 of 31 days -> 74,193.00 (floored to rupee)
        c = billing.calculate_line('fixed', 23, monthly_amount=100000, basis='calendar', period_end=date(2026, 10, 23))
        self.assertEqual(c.amount, D('74193.00'))
        self.assertEqual(c.divisor, 31)
        c = billing.calculate_line('fixed', 23, monthly_amount=110000, basis='calendar', period_end=date(2026, 10, 31))
        self.assertEqual((c.unit_price, c.amount), (D('3548.39'), D('81612.00')))
        c = billing.calculate_line('fixed', 37, monthly_amount=120000, basis='calendar', period_end=date(2026, 10, 31))
        self.assertEqual((c.unit_price, c.amount), (D('3870.97'), D('143225.00')))

    def test_sample_total(self):
        amounts = [D('74193'), D('110000'), D('81612'), D('143225')]
        self.assertEqual(billing.format_indian(sum(amounts)), '4,09,030.00')

    def test_option_b_thirty(self):
        c = billing.calculate_line('fixed', 15, monthly_amount=90000, basis='30', period_end=date(2026, 2, 15))
        self.assertEqual((c.unit_price, c.amount, c.divisor), (D('3000.00'), D('45000.00'), 30))

    def test_february_calendar(self):
        c = billing.calculate_line('fixed', 14, monthly_amount=28000, basis='calendar', period_end=date(2027, 2, 14))
        self.assertEqual((c.divisor, c.amount), (28, D('14000.00')))

    def test_option_a_variable(self):
        c = billing.calculate_line('variable', D('2.5'), daily_rate='1000.50')
        self.assertEqual(c.amount, D('2501.00'))  # 2501.25 floored
        c = billing.calculate_line('variable', D('2.5'), daily_rate='1000.50', rounding='paise')
        self.assertEqual(c.amount, D('2501.25'))

    def test_option_c_full_month(self):
        c = billing.calculate_line('fixed', 10, monthly_amount=50000, full_month=True, period_end=date(2026, 10, 10))
        self.assertEqual((c.amount, c.unit_price), (D('50000.00'), D('5000.00')))

    def test_rounding_policy(self):
        self.assertEqual(billing.q2('1.005'), D('1.01'))           # HALF_UP for unit prices
        self.assertEqual(billing.round_amount('99.999'), D('99.00'))  # rupee_down for amounts
        self.assertEqual(billing.round_amount('99.995', 'paise'), D('100.00'))

    def test_decimal_no_float(self):
        self.assertIsInstance(billing.calculate_line('fixed', 1, monthly_amount=0.1 + 0.2).amount, D)

    def test_indian_grouping(self):
        for v, s in [(0, '0.00'), (999, '999.00'), (1000, '1,000.00'), (100000, '1,00,000.00'),
                     (12345678.9, '1,23,45,678.90'), (-150000, '-1,50,000.00')]:
            self.assertEqual(billing.format_indian(v), s)
        self.assertEqual(billing.format_western(110000), '110,000.00')

    def test_due_date_and_description(self):
        self.assertEqual(billing.due_date_for(date(2026, 11, 2), 31), date(2026, 12, 3))
        self.assertEqual(billing.build_description('Support', date(2026, 10, 1), date(2026, 10, 23)),
                         'Support - October 01 - October 23, 2026')
        self.assertEqual(billing.build_description('Support', date(2026, 12, 20), date(2027, 1, 5)),
                         'Support - December 20, 2026 - January 05, 2027')
        self.assertEqual(billing.inclusive_days(date(2026, 9, 25), date(2026, 10, 31)), 37)


class Fixture(TestCase):
    def setUp(self):
        self.profile = BusinessProfile.current()
        self.client_obj = Client.objects.create(name='American Chase LLC', address='3055 NW Yeon Ave # 530\nPortland, Oregon\nUSA - 97210',
                                                country='USA', default_terms_days=31)
        self.p1 = ProjectTemplate.objects.create(client=self.client_obj, name='Mohammad - John Hopkins', monthly_amount=100000)
        self.p2 = ProjectTemplate.objects.create(client=self.client_obj, name='Vervint', monthly_amount=120000)

    def post_data(self, **extra):
        d = {'client': self.client_obj.pk, 'invoice_date': '2026-11-02', 'terms_days': '31', 'due_date': '',
             'line_project': [self.p1.pk, self.p2.pk], 'line_name': ['', ''], 'line_desc': ['', ''],
             'line_from': ['2026-10-01', '2026-09-25'], 'line_to': ['2026-10-23', '2026-10-31'],
             'line_days': ['23', '37'], 'line_unit': ['', ''], 'line_amount': ['', ''], 'action': 'save'}
        d.update(extra)
        return d


class NumberingTests(Fixture):
    def test_save_consumes_one_number(self):
        start = self.profile.next_invoice_number
        r = self.client.post(reverse('new_invoice'), self.post_data())
        inv = Invoice.objects.get()
        self.assertRedirects(r, reverse('invoice_detail', args=[inv.pk]))
        self.assertEqual(inv.invoice_number, self.profile.format_number(start))
        self.assertEqual(BusinessProfile.current().next_invoice_number, start + 1)
        self.assertEqual(inv.total, D('74193.00') + D('143225.00'))
        self.assertEqual(inv.due_date, date(2026, 12, 3))
        self.assertEqual(inv.lines.first().description, 'Support - October 01 - October 23, 2026')

    def test_preview_and_download_do_not_consume(self):
        start = self.profile.next_invoice_number
        for action in ('preview', 'download'):
            r = self.client.post(reverse('new_invoice'), self.post_data(action=action))
            self.assertEqual(r['Content-Type'], 'application/pdf')
            self.assertTrue(r.content.startswith(b'%PDF'))
        self.assertIn('attachment', r['Content-Disposition'])
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(BusinessProfile.current().next_invoice_number, start)

    def test_edit_does_not_consume(self):
        self.client.post(reverse('new_invoice'), self.post_data())
        inv = Invoice.objects.get()
        n = BusinessProfile.current().next_invoice_number
        self.client.post(reverse('invoice_edit', args=[inv.pk]), self.post_data(line_amount=['70000', '']))
        inv.refresh_from_db()
        self.assertEqual(BusinessProfile.current().next_invoice_number, n)
        self.assertEqual(inv.total, D('70000') + D('143225'))
        self.assertTrue(inv.lines.first().amount_overridden)

    def test_sequential_and_collision_skip(self):
        Invoice.objects.create(client=self.client_obj, invoice_number=self.profile.next_number(), due_date=date.today())
        data = services.parse_invoice_post(self._qd(), self.client_obj, self.profile)
        a = services.save_invoice(data)
        b = services.save_invoice(services.parse_invoice_post(self._qd(), self.client_obj, self.profile))
        self.assertNotEqual(a.invoice_number, b.invoice_number)
        self.assertEqual(Invoice.objects.count(), 3)

    def _qd(self):
        from django.http import QueryDict
        q = QueryDict(mutable=True)
        for k, v in self.post_data().items():
            q.setlist(k, [str(x) for x in v]) if isinstance(v, list) else q.__setitem__(k, str(v))
        return q

    def test_pdf_number_override_only_changes_pdf(self):
        self.client.post(reverse('new_invoice'), self.post_data())
        inv = Invoice.objects.get()
        self.client_obj.refresh_from_db()
        self.assertEqual(self.client_obj.last_pdf_ref_no, '')
        n = BusinessProfile.current().next_invoice_number
        self.client.post(reverse('invoice_detail', args=[inv.pk]), {'pdf_number_override': 'QB020'})
        inv.refresh_from_db()
        self.client_obj.refresh_from_db()
        self.assertEqual(inv.display_number, inv.invoice_number)
        self.assertEqual(self.client_obj.last_pdf_ref_no, 'QB020')
        self.assertNotEqual(inv.invoice_number, 'QB020')
        self.assertEqual(BusinessProfile.current().next_invoice_number, n)
        self.assertEqual(build_invoice_data(inv, BusinessProfile.current()).number, inv.invoice_number)
        r = self.client.get(reverse('invoice_pdf', args=[inv.pk]))
        self.assertIn(f'{inv.invoice_number}.pdf', r['Content-Disposition'])

    def test_create_page_has_explicit_manual_number_preview_button(self):
        response = self.client.get(reverse('new_invoice'))
        self.assertContains(response, 'Preview PDF With Manual Invoice number')
        self.assertContains(response, 'name="action" value="preview_manual"')

    def test_manual_number_is_used_only_by_explicit_preview_action(self):
        data = self.post_data(pdf_number_override='MANUAL-REF-7')
        with patch('invoice_app.views.render_for_invoice', return_value=b'%PDF-preview') as render:
            response = self.client.post(reverse('new_invoice'), {**data, 'action': 'preview'})
            self.assertEqual(response['Content-Type'], 'application/pdf')
            self.assertIn('QB021-preview.pdf', response['Content-Disposition'])
            self.assertIsNone(render.call_args.kwargs['number'])

            response = self.client.post(
                reverse('new_invoice'), {**data, 'action': 'preview_manual'},
            )
            self.assertEqual(response['Content-Type'], 'application/pdf')
            self.assertIn('MANUAL-REF-7-preview.pdf', response['Content-Disposition'])
            self.assertEqual(render.call_args.kwargs['number'], 'MANUAL-REF-7')

        self.assertEqual(Invoice.objects.count(), 0)

    def test_manual_preview_requires_a_manual_number(self):
        response = self.client.post(
            reverse('new_invoice'),
            self.post_data(action='preview_manual'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Enter a manual PDF number to preview with that number.')
        self.assertEqual(Invoice.objects.count(), 0)

    def test_manual_pdf_reference_is_saved_on_customer_and_shown_in_invoice_form(self):
        response = self.client.post(
            reverse('new_invoice'),
            self.post_data(pdf_number_override='MANUAL-REF-7'),
        )
        self.assertEqual(response.status_code, 302)
        self.client_obj.refresh_from_db()
        self.assertEqual(self.client_obj.last_pdf_ref_no, 'MANUAL-REF-7')

        response = self.client.get(reverse('new_invoice'))
        projects = json.loads(response.context['projects_json'])
        self.assertEqual(
            projects[str(self.client_obj.pk)]['last_pdf_ref_no'],
            'MANUAL-REF-7',
        )
        self.assertContains(response, 'Last PDF ref:')
        self.assertNotContains(response, 'Last invoice number generated for this customer')

    def test_validation_errors(self):
        r = self.client.post(reverse('new_invoice'), self.post_data(line_days=['', ''], line_from=['', ''], line_to=['', '']))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Invoice.objects.count(), 0)


class ViewTests(Fixture):
    def test_pages_render(self):
        self.client.post(reverse('new_invoice'), self.post_data())
        inv = Invoice.objects.get()
        for url in [reverse('dashboard'), reverse('profile_edit'), reverse('clients'), reverse('client_add'),
                    reverse('client_detail', args=[self.client_obj.pk]), reverse('client_edit', args=[self.client_obj.pk]),
                    reverse('projects', args=[self.client_obj.pk]), reverse('project_add', args=[self.client_obj.pk]),
                    reverse('new_invoice'), reverse('invoice_list'), reverse('invoice_detail', args=[inv.pk]),
                    reverse('invoice_edit', args=[inv.pk])]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertNotRegex(r.content.decode('utf-8'), 'Ã|â€|\ufffd')
        r = self.client.get(reverse('invoice_detail', args=[inv.pk]))
        for h in ('Consultant -Project Name', 'Memo/Description', 'Qty', 'Unit Price', 'Amount'):
            self.assertContains(r, h)

    def test_customer_crud(self):
        r = self.client.post(reverse('client_add'), {'name': 'Acme', 'address': 'x', 'country_ref': __import__('invoice_app.models', fromlist=['Country']).Country.objects.get(iso2='IN').pk, 'state_ref': __import__('invoice_app.models', fromlist=['State']).State.objects.get(country__iso2='IN', name='Haryana').pk,
                                                     'contact_person': 'A', 'phone': '1', 'email': 'a@b.co',
                                                     'tax_id': 'T', 'billing_type': 'per_day', 'terms_mode': 'custom', 'terms_text': '15', 'notes': ''})
        c = Client.objects.get(name='Acme')
        self.assertRedirects(r, reverse('client_detail', args=[c.pk]))
        self.assertEqual((c.country, c.terms_text), ('India', '15'))

    def test_legacy_manual_pdf_url(self):
        self.client.post(reverse('new_invoice'), self.post_data())
        inv = Invoice.objects.get()
        r = self.client.get(reverse('invoice_pdf_manual', args=[inv.pk, 'QB999']) + '?download=1')
        self.assertIn('attachment', r['Content-Disposition'])
        self.assertIn(f'{inv.invoice_number}.pdf', r['Content-Disposition'])
        inv.refresh_from_db()
        self.assertEqual(inv.pdf_number_override, '')


class ProfileTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.media, ignore_errors=True)

    def test_signature_upload_and_colors(self):
        with override_settings(MEDIA_ROOT=self.media):
            p = BusinessProfile.current()
            fields = {f: getattr(p, f) for f in ['name', 'address', 'phone', 'email', 'gstin', 'bank_name', 'account_holder',
                      'account_number', 'bank_country', 'ifsc', 'signature_text', 'footer_text', 'invoice_prefix',
                      'next_invoice_number', 'number_padding', 'payment_terms_days', 'amount_rounding',
                      'primary_color', 'alternate_row_color', 'white_row_color', 'border_color', 'label_color']}
            fields['primary_color'] = '#112233'
            fields['signature_image'] = SimpleUploadedFile('sig.png', (ASSETS / 'signature.png').read_bytes(), 'image/png')
            r = self.client.post(reverse('profile_edit'), fields)
            self.assertEqual(r.status_code, 302)
            p.refresh_from_db()
            self.assertEqual(p.primary_color, '#112233')
            self.assertTrue(Path(p.signature_image.path).exists())
            fields['primary_color'] = 'navy'
            fields.pop('signature_image')
            r = self.client.post(reverse('profile_edit'), fields)
            self.assertEqual(r.status_code, 200)  # rejected

    def test_numbering_format(self):
        p = BusinessProfile.current()
        self.assertEqual(p.format_number(20), 'QB020')


def _sample_invoice(n_lines=4, **kw):
    c = Client(name='American Chase LLC', address='3055 NW Yeon Ave # 530\nPortland, Oregon\nUSA - 97210')
    from .models import InvoiceLine
    lines = [InvoiceLine(project_name=f'Consultant {i}', description='Support - October 01 - October 31, 2026',
                         quantity_days=D('31'), unit_price=D('3548.39'), amount=D('110000')) for i in range(n_lines)]
    inv = Invoice(client=c, invoice_number='QB020', invoice_date=date(2026, 11, 2), terms_days=31,
                  due_date=date(2026, 12, 5), total=sum(l.amount for l in lines), **kw)
    return inv, lines


class PdfTests(TestCase):
    def render(self, n=4, profile=None, **kw):
        inv, lines = _sample_invoice(n, **kw)
        return render_invoice_pdf(build_invoice_data(inv, profile or BusinessProfile(), lines=lines))

    def pages(self, pdf):
        return len(re.findall(rb'/Type /Page[^s]', pdf))

    def test_letter_single_page(self):
        pdf = self.render()
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertIn(b'/MediaBox [ 0 0 612 792 ]', pdf)
        self.assertEqual(self.pages(pdf), 1)

    def test_pagination_never_shrinks(self):
        per = L.TABLE['body_rows']
        self.assertEqual(self.pages(self.render(per)), 1)
        self.assertEqual(self.pages(self.render(per + 1)), 2)
        self.assertEqual(self.pages(self.render(per * 3)), 3)

    def test_layout_constants(self):
        self.assertEqual((L.PAGE_W, L.PAGE_H), (612, 792))
        self.assertEqual(len(L.TABLE['headers']), 5)
        self.assertEqual([h.strip() for h in L.TABLE['headers']], ['Consultant -Project Name', 'Memo/Description', 'Qty', 'Unit Price', 'Amount'])

    def test_colors_do_not_change_layout(self):
        from .pdf.render import InvoiceCanvas  # noqa: F401  (import check)
        a = BusinessProfile()
        b = BusinessProfile(primary_color='#FF0000', alternate_row_color='#00FF00', white_row_color='#EEEEEE',
                            border_color='#000000', label_color='#123456')
        from reportlab import rl_config
        saved = rl_config.pageCompression, rl_config.useA85
        rl_config.pageCompression, rl_config.useA85 = 0, 0
        try:
            pa, pb = self.render(profile=a), self.render(profile=b)
        finally:
            rl_config.pageCompression, rl_config.useA85 = saved
        self.assertNotEqual(pa, pb)

        def streams(pdf):  # page content streams with every colour operator masked out
            out = [m.group(1) for m in re.finditer(rb'stream\r?\n(.*?)endstream', pdf, re.S) if b' Tf' in m.group(1)]
            self.assertTrue(out)
            return [re.sub(rb'[\d.]+ [\d.]+ [\d.]+ (rg|RG)', b'C', s) for s in out]
        self.assertEqual(streams(pa), streams(pb))

    def test_no_hardcoded_business_data(self):
        src = ''.join(p.read_text(encoding='utf-8') for p in (Path(__file__).parent / 'pdf').glob('*.py'))
        for s in ('QUANTBUD', 'QuantBud LLP', 'IDFC', '10217638591', 'American Chase', 'pk15pi', '06AABFQ2999K1ZQ'):
            self.assertNotIn(s, src)

    def test_manual_pdf_number_requires_explicit_preview_override(self):
        inv, lines = _sample_invoice(pdf_number_override='X-1')
        self.assertEqual(build_invoice_data(inv, BusinessProfile(), lines=lines).number, inv.invoice_number)
        self.assertEqual(
            build_invoice_data(inv, BusinessProfile(), lines=lines, number=inv.pdf_number_override).number,
            'X-1',
        )

    def test_long_text_truncated_not_shrunk(self):
        from reportlab.pdfgen import canvas as rl
        from .pdf.render import InvoiceCanvas
        inv, lines = _sample_invoice(1)
        lines[0].description = 'Support ' * 40
        data = build_invoice_data(inv, BusinessProfile(), lines=lines)
        ic = InvoiceCanvas(rl.Canvas(BytesIO()), data)
        ic.table_rows(data.lines, total=data.total)
        self.assertTrue(ic.clipped)


class SampleCommandTests(TestCase):
    def test_make_sample_does_not_consume(self):
        out = Path(tempfile.mkdtemp()) / 'QB020_test.pdf'
        before = BusinessProfile.current().next_invoice_number
        call_command('make_sample_invoice', out=str(out), stdout=StringIO())
        self.assertTrue(out.read_bytes().startswith(b'%PDF'))
        self.assertEqual(BusinessProfile.current().next_invoice_number, before)
        self.assertEqual(Invoice.objects.count(), 0)
        shutil.rmtree(out.parent)


class BillingTypeTests(TestCase):
    """The customer's payment term (billing type) drives every line."""
    def make(self, bt, **proj):
        c = Client.objects.create(name=f'C {bt}', billing_type=bt, default_terms_days=31)
        return ProjectTemplate.objects.create(client=c, name='Consultant - P', **proj)

    def test_mode1_monthly_prorated(self):
        p = self.make(Client.MONTHLY_PRORATED, monthly_amount=100000)
        c = billing.calculate_for_project(p, 23, period_end=date(2026, 10, 23))
        self.assertEqual((c.unit_price, c.amount), (D('3225.81'), D('74193.00')))
        for end, days in ((date(2026, 10, 31), 31), (date(2026, 9, 30), 30), (date(2027, 2, 28), 28)):
            c = billing.calculate_for_project(p, days, period_end=end)
            self.assertEqual(c.amount, D('100000.00'))  # a full month always = monthly amount
        self.assertEqual(billing.calculate_for_project(p, 28, period_end=date(2027, 2, 28)).unit_price, D('3571.43'))

    def test_mode1_ignores_legacy_project_settings(self):
        p = self.make(Client.MONTHLY_PRORATED, monthly_amount=90000, pricing_type='variable', proration_basis='30',
                      daily_rate=1)
        self.assertEqual(billing.calculate_for_project(p, 31, period_end=date(2026, 10, 31)).amount, D('90000.00'))

    def test_mode2_per_day(self):
        p = self.make(Client.PER_DAY, daily_rate=4000, monthly_amount=999)
        self.assertEqual(billing.calculate_for_project(p, 23, period_end=date(2026, 10, 23)).amount, D('92000.00'))
        c = billing.calculate_for_project(p, 31, period_end=date(2026, 10, 31))
        self.assertEqual((c.unit_price, c.amount), (D('4000.00'), D('124000.00')))

    def test_mode3_blank_unit(self):
        p = self.make(Client.MONTHLY_FIXED, monthly_amount=150000)
        c = billing.calculate_for_project(p, 12, period_end=date(2026, 10, 12))
        self.assertEqual((c.quantity, c.unit_price, c.amount), (D('12.00'), None, D('150000.00')))

    def test_mode3_saved_and_pdf_unit_cell_empty(self):
        from django.http import QueryDict
        from reportlab import rl_config
        p = self.make(Client.MONTHLY_FIXED, monthly_amount=150000)
        q = QueryDict(mutable=True)
        q.update({'invoice_date': '2026-11-02', 'terms_days': '31'})
        for k, v in {'line_project': [p.pk], 'line_name': [''], 'line_desc': [''], 'line_from': ['2026-10-01'],
                     'line_to': ['2026-10-12'], 'line_days': ['12'], 'line_unit': ['500'], 'line_amount': ['']}.items():
            q.setlist(k, [str(x) for x in v])
        data = services.parse_invoice_post(q, p.client, BusinessProfile.current())
        inv = services.save_invoice(data)
        line = inv.lines.get()
        self.assertIsNone(line.unit_price)  # even a typed unit override is ignored in mode 3
        self.assertEqual((line.quantity_days, line.amount, inv.total), (D('12.00'), D('150000.00'), D('150000.00')))
        saved = rl_config.pageCompression, rl_config.useA85
        rl_config.pageCompression, rl_config.useA85 = 0, 0
        try:
            pdf = render_invoice_pdf(build_invoice_data(inv, BusinessProfile.current()))
        finally:
            rl_config.pageCompression, rl_config.useA85 = saved
        self.assertNotIn(b'(0.00)', pdf)
        self.assertNotIn(b'(500.00)', pdf)
        self.assertIn(b'(12.00)', pdf)
        r = self.client.get(reverse('invoice_detail', args=[inv.pk]))
        self.assertNotContains(r, '0.00</td><td class="num">150000')

    def test_customer_form_saves_billing_type(self):
        r = self.client.post(reverse('client_add'), {'name': 'Mode3 Co', 'country_ref': __import__('invoice_app.models', fromlist=['Country']).Country.objects.get(iso2='US').pk, 'billing_type': Client.MONTHLY_FIXED,
                                                     'terms_mode': 'custom', 'terms_text': '31'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Client.objects.get(name='Mode3 Co').billing_type, Client.MONTHLY_FIXED)
        r = self.client.get(reverse('client_add'))
        self.assertContains(r, 'Payment term (billing type)')
        self.assertContains(r, 'Terms (days until due)')

    def test_project_form_follows_customer_type(self):
        p = self.make(Client.PER_DAY)
        r = self.client.get(reverse('project_add', args=[p.client.pk]))
        self.assertContains(r, 'name="daily_rate"')
        self.assertNotContains(r, 'name="monthly_amount"')
        self.assertNotContains(r, 'name="pricing_type"')

    def test_invoice_form_uses_sample_labels(self):
        r = self.client.get(reverse('new_invoice'))
        html = r.content.decode()
        for label in ('Bill To', 'Invoice Date', 'Terms', 'Due Date', 'Consultant -Project Name', 'Memo/Description',
                      '<th>Qty</th>', 'Unit Price', 'Amount', 'Total', 'Payment term (billing type)'):
            self.assertIn(label, html)
        for generic in ('>Customer<', '>Days<', '>Description<', '>Project<', '>Rate<'):
            self.assertNotIn(generic, html)


class TermsTypeTests(TestCase):
    """Terms (days until due): type 1 = days in invoice month, type 2 = custom text/number."""
    def setUp(self):
        self.profile = BusinessProfile.current()
        self.c = Client.objects.create(name='T Co', terms_mode='month_days')
        self.p = ProjectTemplate.objects.create(client=self.c, name='Consultant - P', monthly_amount=1000)

    def post(self, **extra):
        from django.http import QueryDict
        q = QueryDict(mutable=True)
        q.update({'invoice_date': '2026-11-02', **extra})
        for k, v in {'line_project': [self.p.pk], 'line_name': [''], 'line_desc': [''], 'line_from': ['2026-10-01'],
                     'line_to': ['2026-10-31'], 'line_days': ['31'], 'line_unit': [''], 'line_amount': ['']}.items():
            q.setlist(k, [str(x) for x in v])
        return services.parse_invoice_post(q, self.c, self.profile)

    def test_resolve(self):
        self.assertEqual(billing.resolve_terms('month_days', 'x', date(2026, 11, 2)), ('30', 30))
        self.assertEqual(billing.resolve_terms('month_days', '', date(2027, 2, 1)), ('28', 28))
        self.assertEqual(billing.resolve_terms('month_days', '', date(2028, 2, 1)), ('29', 29))
        self.assertEqual(billing.resolve_terms('custom', ' 15 ', date(2026, 11, 2)), ('15', 15))
        self.assertEqual(billing.resolve_terms('custom', 'Net 15', date(2026, 11, 2)), ('Net 15', None))

    def test_type1_from_customer_default(self):
        d = self.post()
        self.assertEqual((d.terms_mode, d.terms_days, d.due_date), ('month_days', 30, date(2026, 12, 2)))
        inv = services.save_invoice(d)
        inv.refresh_from_db()
        self.assertEqual(inv.terms_display, '30')
        d = self.post(invoice_date='2027-02-10')
        self.assertEqual((d.terms_days, d.due_date), (28, date(2027, 3, 10)))

    def test_type2_number_and_text(self):
        d = self.post(terms_mode='custom', terms_text='31')
        self.assertEqual((d.terms_text, d.terms_days, d.due_date), ('31', 31, date(2026, 12, 3)))
        d = self.post(terms_mode='custom', terms_text='Due on receipt')
        self.assertEqual((d.terms_days, d.due_date), (None, None))
        d = self.post(terms_mode='custom', terms_text='Net 15', due_date='2026-11-17')
        inv = services.save_invoice(d)
        inv.refresh_from_db()
        self.assertEqual((inv.terms_display, inv.due_date), ('Net 15', date(2026, 11, 17)))
        r = self.client.get(reverse('invoice_edit', args=[inv.pk]))
        self.assertEqual((r.context['initial']['terms_mode'], r.context['initial']['terms_text'],
                          r.context['initial']['due_date']), ('custom', 'Net 15', date(2026, 11, 17)))

    def _pdf(self, inv):
        from reportlab import rl_config
        saved = rl_config.pageCompression, rl_config.useA85
        rl_config.pageCompression, rl_config.useA85 = 0, 0
        try:
            return render_invoice_pdf(build_invoice_data(inv, self.profile))
        finally:
            rl_config.pageCompression, rl_config.useA85 = saved

    def test_pdf_terms_cell(self):
        inv = services.save_invoice(self.post(terms_mode='custom', terms_text='Due on receipt'))
        pdf = self._pdf(inv)
        self.assertIn(b'(Due on receipt)', pdf)
        inv = services.save_invoice(self.post())
        self.assertIn(b'(30)', self._pdf(inv))

    def test_customer_form_and_invoice_form_offer_both_types(self):
        r = self.client.get(reverse('client_add'))
        self.assertContains(r, 'name="terms_mode"')
        self.assertContains(r, 'name="terms_text"')
        r = self.client.get(reverse('new_invoice'))
        self.assertContains(r, 'value="month_days"')
        self.assertContains(r, 'id="terms_text"')


class ActiveFlagAndComboboxTests(TestCase):
    def setUp(self):
        self.on = Client.objects.create(name='Live Co', terms_mode='custom', terms_text='31')
        self.off = Client.objects.create(name='Gone Co', active=False)
        self.p_on = ProjectTemplate.objects.create(client=self.on, name='Alice - Alpha', monthly_amount=31000)
        self.p_off = ProjectTemplate.objects.create(client=self.on, name='Bob - Beta', monthly_amount=1, active=False)

    def test_defaults_active(self):
        c = Client.objects.create(name='New')
        self.assertTrue(c.active)
        self.assertTrue(ProjectTemplate.objects.create(client=c, name='X').active)

    def test_invoice_dropdowns_hide_inactive(self):
        r = self.client.get(reverse('new_invoice'))
        html = r.content.decode()
        self.assertIn(f'<option value="{self.on.pk}" data-name="live co">Live Co</option>', html)
        self.assertNotIn('Gone Co', html)
        projs = json.loads(r.context['projects_json'])
        names = [p['name'] for p in projs[str(self.on.pk)]['projects']]
        self.assertEqual(names, ['Alice - Alpha'])

    def test_searchable_ui_smoke(self):
        html = self.client.get(reverse('new_invoice')).content.decode()
        for s in ('<select class="field" name="client" id="client" required>', 'Select customer',
                  'class="pj-combo"', 'name="line_name"'):
            self.assertIn(s, html)
        # one dropdown per row: no datalist picker on the custom-text input, one Remove button
        self.assertNotIn('datalist', html)
        self.assertNotIn('list="projlist"', html)
        self.assertEqual(html.count('class="icon-btn danger del"'), 1)
        # project name is ONE combobox per row: one text field, one toggle arrow, one list; no separate <select>
        self.assertEqual(html.count('class="pj-combo"'), 1)
        self.assertEqual(html.count('class="pj-toggle"'), 1)
        self.assertNotIn('pj-select', html)
        self.assertRegex(html, r'<input class="field pj-text" name="line_name"[^>]*role="combobox"')
        for url in (reverse('clients'), reverse('projects_hub'), reverse('projects', args=[self.on.pk])):
            r = self.client.get(url)
            self.assertContains(r, 'data-filter=')
            self.assertContains(r, 'Inactive')

    def _post(self, names, project_ids):
        d = {'client': self.on.pk, 'invoice_date': '2026-11-02', 'terms_mode': 'custom', 'terms_text': '31',
             'due_date': '', 'action': 'save', 'line_project': project_ids, 'line_name': names,
             'line_desc': ['', ''], 'line_from': ['2026-10-01', '2026-10-01'], 'line_to': ['2026-10-31', '2026-10-31'],
             'line_days': ['31', '31'], 'line_unit': ['', '500'], 'line_amount': ['', '']}
        return self.client.post(reverse('new_invoice'), d)

    def test_custom_project_text_saved_on_line(self):
        r = self._post(['alice - alpha', 'Carol - Custom Work'], ['', ''])
        self.assertEqual(r.status_code, 302)
        inv = Invoice.objects.get()
        a, b = inv.lines.order_by('id')
        self.assertEqual((a.project_id, a.amount), (self.p_on.pk, D('31000.00')))  # typed text matched saved project
        self.assertEqual((b.project_id, b.project_name, b.amount), (None, 'Carol - Custom Work', D('15500.00')))
        r = self.client.get(reverse('invoice_edit', args=[inv.pk]))
        self.assertEqual(r.context['initial']['lines'][1]['name'], 'Carol - Custom Work')

    def test_typed_inactive_name_is_custom(self):
        self._post(['Bob - Beta', 'Alice - Alpha'], ['', ''])
        a = Invoice.objects.get().lines.get(project_name='Bob - Beta')
        self.assertIsNone(a.project_id)

    def test_toggles_and_forms(self):
        self.client.post(reverse('client_toggle', args=[self.off.pk]))
        self.off.refresh_from_db(); self.assertTrue(self.off.active)
        self.client.post(reverse('project_toggle', args=[self.on.pk, self.p_on.pk]), {'next': '/projects/'})
        self.p_on.refresh_from_db(); self.assertFalse(self.p_on.active)
        self.assertContains(self.client.get(reverse('client_add')), 'name="active"')
        self.assertContains(self.client.get(reverse('project_add', args=[self.on.pk])), 'name="active"')

    def test_editing_invoice_keeps_inactive_customer_and_project(self):
        r = self._post(['Alice - Alpha', 'Zed'], [str(self.p_on.pk), ''])
        inv = Invoice.objects.get()
        Client.objects.filter(pk=self.on.pk).update(active=False)
        ProjectTemplate.objects.filter(pk=self.p_on.pk).update(active=False)
        r = self.client.get(reverse('invoice_edit', args=[inv.pk]))
        self.assertContains(r, 'data-name="live co">Live Co</option>')
        projs = json.loads(r.context['projects_json'])
        self.assertIn('Alice - Alpha', [p['name'] for p in projs[str(self.on.pk)]['projects']])


class BillingMonthTests(TestCase):
    def setUp(self):
        self.c = Client.objects.create(name='Month Co', terms_mode='custom', terms_text='31')
        self.p = ProjectTemplate.objects.create(client=self.c, name='Alice - Alpha', monthly_amount=31000)

    def _post(self, **extra):
        d = {'client': self.c.pk, 'invoice_date': '2026-11-02', 'terms_mode': 'custom', 'terms_text': '31',
             'due_date': '', 'action': 'save', 'line_project': [str(self.p.pk)], 'line_name': ['Alice - Alpha'],
             'line_desc': [''], 'line_from': ['2026-10-01'], 'line_to': ['2026-10-31'], 'line_days': ['31'],
             'line_unit': [''], 'line_amount': ['']}
        d.update(extra)
        return self.client.post(reverse('new_invoice'), d)

    def test_previous_month_helper(self):
        self.assertEqual(services.previous_month(date(2026, 1, 15)), date(2025, 12, 1))
        self.assertEqual(services.previous_month(date(2026, 11, 2)), date(2026, 10, 1))
        self.assertEqual(services.parse_month('2026-10'), date(2026, 10, 1))
        with self.assertRaises(ValueError):
            services.parse_month('Oct')

    def test_new_form_defaults_to_previous_month(self):
        from unittest import mock
        with mock.patch('django.utils.timezone.localdate', return_value=date(2026, 11, 2)):
            r = self.client.get(reverse('new_invoice'))
        self.assertEqual(r.context['initial']['billing_month'], '2026-10')
        html = r.content.decode()
        self.assertEqual((r.context['initial']['billing_year'], r.context['initial']['billing_mon']), ('2026', '10'))
        self.assertIn('<option value="2026" selected>2026</option>', html)
        self.assertIn('<option value="10" selected>October</option>', html)
        self.assertIn('function monthRange', html)  # memo from/to auto-fill script

    def test_saved_reopened_and_shown(self):
        self.assertEqual(self._post(billing_month='2026-09').status_code, 302)
        inv = Invoice.objects.get()
        self.assertEqual(inv.billing_month, date(2026, 9, 1))
        self.assertEqual(inv.billing_month_label, 'September 2026')
        r = self.client.get(reverse('invoice_edit', args=[inv.pk]))
        self.assertEqual(r.context['initial']['billing_month'], '2026-09')
        self.assertContains(r, '<option value="09" selected>September</option>')
        self.assertContains(self.client.get(reverse('invoice_detail', args=[inv.pk])), 'Invoice for September 2026')
        self.assertContains(self.client.get(reverse('invoice_list')), 'September 2026')
        self.client.post(reverse('invoice_edit', args=[inv.pk]), {**{k: v for k, v in [
            ('client', self.c.pk), ('invoice_date', '2026-11-02'), ('terms_mode', 'custom'), ('terms_text', '31'),
            ('action', 'save'), ('line_project', [str(self.p.pk)]), ('line_name', ['Alice - Alpha']),
            ('line_from', ['2026-08-01']), ('line_to', ['2026-08-31']), ('line_days', ['31'])]}, 'billing_month': '2026-08'})
        inv.refresh_from_db()
        self.assertEqual(inv.billing_month, date(2026, 8, 1))

    def test_missing_key_defaults_blank_clears_invalid_errors(self):
        self._post()
        self.assertEqual(Invoice.objects.get().billing_month, date(2026, 10, 1))
        Invoice.objects.all().delete()
        self._post(billing_month='')
        self.assertIsNone(Invoice.objects.get().billing_month)
        r = self._post(billing_month='bogus')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_preview_pdf_still_renders_and_uses_no_number(self):
        before = BusinessProfile.current().next_invoice_number
        r = self._post(billing_month='2026-10', action='preview')
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertEqual(BusinessProfile.current().next_invoice_number, before)

    def test_old_month_kept_in_options(self):
        inv = Invoice.objects.create(client=self.c, invoice_number='OLD1', billing_month=date(2019, 3, 1))
        r = self.client.get(reverse('invoice_edit', args=[inv.pk]))
        self.assertContains(r, '<option value="2019" selected>2019</option>')
        self.assertContains(r, '<option value="03" selected>March</option>')


class BillingYearMonthFieldTests(TestCase):
    def setUp(self):
        self.c = Client.objects.create(name='YM Co', terms_mode='custom', terms_text='31')

    def _post(self, **extra):
        d = {'client': self.c.pk, 'invoice_date': '2027-01-05', 'terms_mode': 'custom', 'terms_text': '31',
             'action': 'save', 'line_project': [''], 'line_name': ['Custom'], 'line_from': ['2026-12-01'],
             'line_to': ['2026-12-31'], 'line_days': ['31'], 'line_unit': ['100'], 'line_amount': ['']}
        d.update(extra)
        return self.client.post(reverse('new_invoice'), d)

    def test_january_defaults_to_december_of_previous_year(self):
        from unittest import mock
        with mock.patch('django.utils.timezone.localdate', return_value=date(2027, 1, 10)):
            r = self.client.get(reverse('new_invoice'))
        i = r.context['initial']
        self.assertEqual((i['billing_year'], i['billing_mon']), ('2026', '12'))
        self.assertContains(r, '<option value="2026" selected>2026</option>')
        self.assertContains(r, '<option value="12" selected>December</option>')
        self.assertContains(r, '<option value="2027">2027</option>')

    def test_two_fields_saved_as_first_of_month(self):
        self.assertEqual(self._post(billing_year='2026', billing_mon='12').status_code, 302)
        inv = Invoice.objects.get()
        self.assertEqual(inv.billing_month, date(2026, 12, 1))
        self.assertContains(self.client.get(reverse('invoice_detail', args=[inv.pk])), 'Invoice for December 2026')

    def test_either_blank_means_not_set(self):
        self._post(billing_year='2026', billing_mon='')
        self.assertIsNone(Invoice.objects.get().billing_month)

    def test_two_field_ui_present(self):
        html = self.client.get(reverse('new_invoice')).content.decode()
        self.assertIn('name="billing_year" id="billing_year"', html)
        self.assertIn('name="billing_mon" id="billing_mon"', html)
        self.assertIn('function ym()', html)


class InvoiceListDateFilterTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name='Date filter customer')

    def invoice(self, number, invoice_date):
        invoice_date = date.fromisoformat(invoice_date)
        return Invoice.objects.create(
            client=self.client_obj,
            invoice_number=number,
            invoice_date=invoice_date,
            due_date=invoice_date,
        )

    def get(self, **params):
        with patch('invoice_app.views.timezone.localdate', return_value=date(2026, 11, 5)):
            return self.client.get(reverse('invoice_list'), params)

    @staticmethod
    def numbers(response):
        return {invoice.invoice_number for invoice in response.context['invoices']}

    def test_selected_year_and_optional_month_filter_invoice_date(self):
        self.invoice('SEP26', '2026-09-10')
        self.invoice('OCT26', '2026-10-10')
        self.invoice('OCT25', '2025-10-10')

        year = self.get(period='year_month', year='2026')
        self.assertEqual(self.numbers(year), {'SEP26', 'OCT26'})
        month = self.get(period='year_month', year='2026', month='10')
        self.assertEqual(self.numbers(month), {'OCT26'})
        self.assertContains(month, 'October 2026')

    def test_last_three_months_includes_current_and_previous_two_calendar_months(self):
        self.invoice('AUG', '2026-08-31')
        self.invoice('SEP', '2026-09-01')
        self.invoice('NOV', '2026-11-30')
        self.invoice('DEC', '2026-12-01')

        self.assertEqual(self.numbers(self.get(period='last3')), {'SEP', 'NOV'})

    def test_previous_month_filter(self):
        self.invoice('SEP', '2026-09-30')
        self.invoice('OCT-START', '2026-10-01')
        self.invoice('OCT-END', '2026-10-31')
        self.invoice('NOV', '2026-11-01')

        self.assertEqual(self.numbers(self.get(period='previous')), {'OCT-START', 'OCT-END'})

    def test_last_six_months_includes_current_and_previous_five_calendar_months(self):
        self.invoice('MAY', '2026-05-31')
        self.invoice('JUN', '2026-06-01')
        self.invoice('NOV', '2026-11-30')
        self.invoice('DEC', '2026-12-01')

        self.assertEqual(self.numbers(self.get(period='last6')), {'JUN', 'NOV'})

    def test_financial_year_filters_april_to_march_and_offers_only_five_years(self):
        self.invoice('MAR26', '2026-03-31')
        self.invoice('APR26', '2026-04-01')
        self.invoice('MAR27', '2027-03-31')
        self.invoice('APR27', '2027-04-01')

        response = self.get(period='financial_year', financial_year='2026')
        self.assertEqual(self.numbers(response), {'APR26', 'MAR27'})
        self.assertContains(response, 'FY 2026-27')

        all_invoices = self.get()
        self.assertEqual(len(all_invoices.context['financial_year_options']), 5)
        self.assertContains(all_invoices, 'FY 2026-27')
        # The FY choice now lives in the shared period picker and is applied with the Apply button.
        self.assertContains(all_invoices, '<option value="financial" selected>Financial year</option>')
        self.assertContains(all_invoices, '<button class="button primary" type="submit">Apply</button>')

    def test_financial_year_filter_rejects_year_outside_last_five_options(self):
        self.invoice('APR20', '2020-04-01')
        response = self.get(period='financial_year', financial_year='2020')
        self.assertEqual(self.numbers(response), set())
        self.assertContains(response, 'Choose a financial year from the available options.')

    def test_invalid_month_does_not_fall_back_to_unfiltered_invoices(self):
        self.invoice('OCT', '2026-10-01')
        response = self.get(period='year_month', year='2026', month='13')
        self.assertEqual(self.numbers(response), set())
        self.assertContains(response, 'Choose a valid year and month.')


class BillToDropdownTests(TestCase):
    def test_bill_to_is_select_of_active_customers_with_filter(self):
        for i in range(30):
            Client.objects.create(name=f'Cust {i:02d}', active=i != 7)
        html = self.client.get(reverse('new_invoice')).content.decode()
        sel = re.search(r'<select class="field" name="client" id="client" required>(.*?)</select>', html, re.S).group(1)
        self.assertEqual(sel.count('<option value="'), 30)  # placeholder + 29 active
        self.assertIn('Select customer', sel)
        self.assertNotIn('Cust 07', html)
        self.assertIn('id="client_filter"', html)
        self.assertNotIn('client_search', html)

    def test_few_customers_still_get_search_and_select_posts_client(self):
        c = Client.objects.create(name='Only Co', terms_mode='custom', terms_text='31')
        p = ProjectTemplate.objects.create(client=c, name='A - B', monthly_amount=31000)
        self.assertIn('id="client_filter"', self.client.get(reverse('new_invoice')).content.decode())
        r = self.client.post(reverse('new_invoice'), {
            'client': c.pk, 'invoice_date': '2026-11-02', 'terms_mode': 'custom', 'terms_text': '31', 'action': 'save',
            'billing_month': '2026-10', 'line_project': [str(p.pk)], 'line_name': ['A - B'],
            'line_from': ['2026-10-01'], 'line_to': ['2026-10-31'], 'line_days': ['31']})
        self.assertEqual(r.status_code, 302)
        inv = Invoice.objects.get()
        self.assertEqual((inv.client, inv.lines.get().project), (c, p))
        r = self.client.get(reverse('invoice_edit', args=[inv.pk]))
        self.assertEqual(r.context['initial']['client'], c.pk)


class LastInvoiceTagTests(TestCase):
    def test_label_and_last_pdf_reference_per_customer(self):
        a = Client.objects.create(name='A Co')
        b = Client.objects.create(name='B Co', last_pdf_ref_no='PDF-B-19')
        Client.objects.create(name='C Co')
        Client.objects.filter(pk=a.pk).update(last_pdf_ref_no='PDF-A-20')
        r = self.client.get(reverse('new_invoice'))
        html = r.content.decode()
        self.assertIn('>Manual Invoice number for pdf only</label>', html)
        self.assertNotIn('PDF number override', html)
        self.assertIn('id="last_invoice"', html)
        self.assertIn('No previous PDF ref', html)
        self.assertIn('Last PDF ref:', html)
        P = json.loads(r.context['projects_json'])
        self.assertEqual(P[str(a.pk)]['last_pdf_ref_no'], 'PDF-A-20')
        self.assertEqual(P[str(b.pk)]['last_pdf_ref_no'], 'PDF-B-19')
        self.assertEqual(
            P[[k for k, v in P.items() if k not in (str(a.pk), str(b.pk))][0]]['last_pdf_ref_no'],
            '',
        )


class IconActionTests(TestCase):
    def test_customer_row_actions_are_accessible_icons(self):
        c = Client.objects.create(name='Icon Co', default_terms_days=30)
        html = self.client.get(reverse('clients')).content.decode()
        for label in ('Deactivate', 'Edit'):
            self.assertIn(f'title="{label}" aria-label="{label}"', html)
        self.assertIn('<svg class="ico ico-edit"', html)
        self.assertIn(reverse('client_edit', args=[c.pk]), html)
        self.assertNotIn('>Edit</a>', html)
        self.assertNotIn('client_delete', html)
        self.assertNotIn('aria-label="Delete"', html)
        self.assertNotIn('Delete this customer', html)
        self.assertIn("return confirm('Deactivate customer Icon Co?", html)

    def test_activate_deactivate_confirm_includes_brand(self):
        active = Client.objects.create(
            name='Active Co', brand_name='BrandX', default_terms_days=30)
        inactive = Client.objects.create(
            name='Idle Co', brand_name='Quiet', active=False, default_terms_days=30)
        html = self.client.get(reverse('clients')).content.decode()
        self.assertIn(
            "return confirm('Deactivate customer Active Co (BrandX)?", html)
        self.assertIn(
            "return confirm('Activate customer Idle Co (Quiet)?", html)
        self.assertNotIn('client_delete', html)
