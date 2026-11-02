import re
import shutil
import tempfile
from datetime import date
from decimal import Decimal as D
from io import BytesIO, StringIO
from pathlib import Path

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
        n = BusinessProfile.current().next_invoice_number
        self.client.post(reverse('invoice_detail', args=[inv.pk]), {'pdf_number_override': 'QB020'})
        inv.refresh_from_db()
        self.assertEqual(inv.display_number, 'QB020')
        self.assertNotEqual(inv.invoice_number, 'QB020')
        self.assertEqual(BusinessProfile.current().next_invoice_number, n)
        r = self.client.get(reverse('invoice_pdf', args=[inv.pk]))
        self.assertIn('QB020.pdf', r['Content-Disposition'])

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
        r = self.client.post(reverse('client_add'), {'name': 'Acme', 'address': 'x', 'country': 'India',
                                                     'contact_person': 'A', 'phone': '1', 'email': 'a@b.co',
                                                     'tax_id': 'T', 'default_terms_days': '15', 'notes': ''})
        c = Client.objects.get(name='Acme')
        self.assertRedirects(r, reverse('client_detail', args=[c.pk]))
        self.assertEqual((c.country, c.default_terms_days), ('India', 15))

    def test_legacy_manual_pdf_url(self):
        self.client.post(reverse('new_invoice'), self.post_data())
        inv = Invoice.objects.get()
        r = self.client.get(reverse('invoice_pdf_manual', args=[inv.pk, 'QB999']) + '?download=1')
        self.assertIn('attachment', r['Content-Disposition'])
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

    def test_pdf_override_number_used(self):
        inv, lines = _sample_invoice(pdf_number_override='X-1')
        self.assertEqual(build_invoice_data(inv, BusinessProfile(), lines=lines).number, 'X-1')

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
