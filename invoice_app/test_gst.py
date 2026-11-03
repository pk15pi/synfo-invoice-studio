"""GST tax invoicing: calculation, GSTIN validation, form/save flow, ledger posting and PDFs."""
import hashlib
from datetime import date
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounting import postings
from accounting import services as acc
from . import gst, ledger
from .forms import ClientForm
from .models import BusinessProfile, Client, Invoice, InvoiceLine, ProjectTemplate
from .pdf import render_invoice_pdf
from .pdf.from_models import build_invoice_data


def gstin(state, pan='AAACI1234A', entity='1'):
    first14 = f'{state}{pan}{entity}Z'
    return first14 + gst.gstin_checksum(first14)


class ComputeTests(TestCase):
    def test_intra_state_splits_cgst_sgst(self):
        r = gst.compute([(D('100000'), D('18'))], '06', '06')
        self.assertEqual((r.tax_type, r.cgst, r.sgst, r.igst), ('cgst_sgst', D('9000.00'), D('9000.00'), D('0.00')))
        self.assertEqual(r.grand_total, D('118000.00'))

    def test_inter_state_igst(self):
        r = gst.compute([(D('100000'), D('18'))], '29', '06')
        self.assertEqual((r.tax_type, r.igst, r.cgst), ('igst', D('18000.00'), D('0.00')))

    def test_mixed_rates_effective_rate(self):
        r = gst.compute([(D('100000'), D('18')), (D('1001'), D('5'))], '06', '06')
        self.assertEqual((r.cgst, r.sgst), (D('9025.03'), D('9025.03')))
        self.assertEqual(r.rate, D('17.87'))
        self.assertFalse(r.uniform)

    def test_rounding_half_up_per_head(self):
        # 0.25 * 9% = 0.0225 -> 0.02 ; 0.50 * 9% = 0.045 -> 0.05 (half up, not banker's)
        self.assertEqual(gst.compute([(D('1.00'), D('18'))], '06', '06').cgst, D('0.09'))
        self.assertEqual(gst.compute([(D('0.50'), D('18'))], '06', '06').cgst, D('0.05'))
        self.assertEqual(gst.compute([(D('0.25'), D('18'))], '29', '06').igst, D('0.05'))  # 0.045 -> 0.05
        r = gst.compute([(D('333.33'), D('18'))], '06', '06')
        self.assertEqual(r.cgst + r.sgst, D('60.00'))
        self.assertEqual(r.grand_total, D('393.33'))  # no round-off to the rupee

    def test_amount_in_words_indian(self):
        self.assertEqual(gst.amount_in_words(D('118000.50')),
                         'Indian Rupees One Lakh Eighteen Thousand and Fifty Paise Only')
        self.assertIn('Two Crore', gst.amount_in_words(D('20000000')))


class GstinTests(TestCase):
    def test_business_gstin_valid_and_state(self):
        self.assertIsNone(gst.gstin_error('06AABFQ2999K1ZQ'))
        self.assertEqual(gst.state_from_gstin('06AABFQ2999K1ZQ'), '06')

    def test_invalid(self):
        for bad in ['06AABFQ2999K1ZX', '6AABFQ2999K1ZQ', '99AABFQ2999K1ZQ0', 'XXAABFQ2999K1ZQ']:
            self.assertIsNotNone(gst.gstin_error(bad), bad)
        with self.assertRaises(ValidationError):
            gst.validate_gstin('06AABFQ2999K1ZX')

    def test_client_form_autofills_state(self):
        f = ClientForm(data={'name': 'Desi Co', 'country': 'India', 'tax_treatment': 'domestic_gst',
                             'gstin': gstin('29').lower(), 'default_terms_days': 30,
                             'billing_type': Client._meta.get_field('billing_type').default,
                             'terms_mode': Client._meta.get_field('terms_mode').default})
        self.assertTrue(f.is_valid(), f.errors)
        c = f.save()
        self.assertEqual((c.gstin, c.state_code, c.state_name), (gstin('29'), '29', 'Karnataka'))

    def test_domestic_without_state_rejected(self):
        c = Client(name='X', tax_treatment=gst.DOMESTIC_GST)
        with self.assertRaises(ValidationError):
            c.full_clean()


class FlowBase(TestCase):
    def setUp(self):
        self.profile = BusinessProfile.current()
        self.assertEqual(gst.business_state(self.profile), '06')
        self.c = Client.objects.create(name='Haryana Buyer', address='Sector 44\nGurugram\nIndia - 122003',
                                       country='India', tax_treatment=gst.DOMESTIC_GST, gstin=gstin('06'))
        self.p1 = ProjectTemplate.objects.create(client=self.c, name='Platform', monthly_amount=100000)
        self.p2 = ProjectTemplate.objects.create(client=self.c, name='Hosting', monthly_amount=1001,
                                                 sac_code='998315', gst_rate=D('5'))

    def post(self, **extra):
        d = {'client': self.c.pk, 'invoice_date': '2026-11-03', 'terms_days': '30', 'due_date': '',
             'line_project': [self.p1.pk, self.p2.pk], 'line_name': ['', ''], 'line_desc': ['', ''],
             'line_from': ['2026-10-01', '2026-10-01'], 'line_to': ['2026-10-31', '2026-10-31'],
             'line_days': ['31', '31'], 'line_unit': ['', ''], 'line_amount': ['100000', '1001'], 'action': 'save'}
        d.update(extra)
        return d

    def new_invoice(self, **extra):
        self.client.post(reverse('new_invoice'), self.post(**extra))
        return Invoice.objects.latest('pk')


class FlowTests(FlowBase):
    def test_intra_state_invoice_snapshots(self):
        inv = self.new_invoice()
        self.assertEqual((inv.tax_treatment, inv.place_of_supply, inv.tax_type), ('domestic_gst', '06', 'cgst_sgst'))
        self.assertEqual((inv.taxable_value, inv.cgst_amount, inv.sgst_amount), (D('101001.00'), D('9025.03'), D('9025.03')))
        self.assertEqual(inv.grand_total, D('119051.06'))
        self.assertEqual(inv.currency, 'INR')
        self.assertEqual([(l.sac_code, l.gst_rate) for l in inv.lines.order_by('position')],
                         [('998314', D('18.00')), ('998315', D('5.00'))])

    def test_place_of_supply_override_gives_igst(self):
        inv = self.new_invoice(place_of_supply='29')
        self.assertEqual((inv.tax_type, inv.igst_amount, inv.cgst_amount), ('igst', D('18050.05'), D('0.00')))

    def test_export_and_lut_have_no_tax(self):
        for t in (gst.EXPORT, gst.EXPORT_LUT):
            inv = self.new_invoice(tax_treatment=t)
            self.assertEqual((inv.tax_treatment, inv.tax_type, inv.total_tax), (t, 'none', D('0.00')))
            self.assertEqual(inv.grand_total, D('101001.00'))
            self.assertEqual(inv.place_of_supply, '')

    def test_export_client_default(self):
        self.c.tax_treatment, self.c.gstin = gst.EXPORT, ''
        self.c.save()
        self.assertEqual(self.new_invoice().tax_type, 'none')

    def test_numbering_unchanged(self):
        start = BusinessProfile.current().next_invoice_number
        inv = self.new_invoice()
        self.assertEqual(inv.invoice_number, self.profile.format_number(start))

    def test_draft_recomputes_finalized_never(self):
        inv = self.new_invoice()
        self.p1.gst_rate = D('12')
        self.p1.save()
        self.client.post(reverse('invoice_edit', args=[inv.pk]), self.post())
        inv.refresh_from_db()
        self.assertEqual(inv.cgst_amount, D('6025.03'))  # draft follows the project's new rate
        ledger.finalize_invoice(inv)
        self.p1.gst_rate = D('28')
        self.p1.save()
        before = Invoice.objects.filter(pk=inv.pk).values('cgst_amount', 'sgst_amount', 'grand_total', 'tax_rate').get()
        self.client.post(reverse('invoice_edit', args=[inv.pk]), self.post(place_of_supply='29'))
        inv.refresh_from_db()
        self.assertEqual(Invoice.objects.filter(pk=inv.pk).values('cgst_amount', 'sgst_amount', 'grand_total', 'tax_rate').get(), before)
        self.assertEqual(inv.place_of_supply, '06')
        self.assertEqual(inv.lines.order_by('position')[0].gst_rate, D('12.00'))

    def test_form_shows_tax_section(self):
        r = self.client.get(reverse('new_invoice'))
        self.assertContains(r, 'id="gst_section"')
        self.assertContains(r, 'name="tax_treatment"')


class LedgerGstTests(FlowBase):
    def test_finalize_posts_gst(self):
        inv = self.new_invoice()
        ledger.finalize_invoice(inv)
        party = ('customer', str(self.c.pk))
        self.assertEqual(postings.party_balance(acc.mapped_account('invoice.ar'), *party), D('119051.06'))
        self.assertEqual(acc.balance(acc.mapped_account('invoice.revenue')), D('101001.00'))
        self.assertEqual(acc.balance(acc.mapped_account('gst.output.cgst')), D('9025.03'))
        self.assertEqual(acc.balance(acc.mapped_account('gst.output.sgst')), D('9025.03'))

    def test_finalize_posts_igst(self):
        inv = self.new_invoice(place_of_supply='29')
        ledger.finalize_invoice(inv)
        self.assertEqual(acc.balance(acc.mapped_account('gst.output.igst')), D('18050.05'))


class PdfTests(FlowBase):
    def text(self, inv):
        import fitz  # PyMuPDF optional; skip when absent
        pdf = render_invoice_pdf(build_invoice_data(inv, self.profile))
        return pdf, ''.join(p.get_text() for p in fitz.open(stream=pdf, filetype='pdf'))

    def setUp(self):
        super().setUp()
        try:
            import fitz  # noqa: F401
        except ImportError:
            self.skipTest('PyMuPDF not installed')

    def test_tax_invoice_text(self):
        _, t = self.text(self.new_invoice())
        for s in ['TAX INVOICE', 'Customer GSTIN: ' + gstin('06'), 'Place of Supply: Haryana (06)', 'SAC: 998314',
                  'SAC: 998315', 'Taxable Value', 'CGST', 'SGST', '9,025.03', 'Grand Total', '1,19,051.06',
                  'Indian Rupees One Lakh Nineteen Thousand Fifty One and Six Paise Only', self.profile.gstin]:
            self.assertIn(s, t.replace('\n', ' ') if ' ' in s else t, s)
        self.assertNotIn('IGST', t)

    def test_igst_text(self):
        _, t = self.text(self.new_invoice(place_of_supply='29'))
        self.assertIn('IGST', t)
        self.assertIn('Place of Supply: Karnataka (29)', t)

    def test_lut_one_extra_line(self):
        self.c.lut_number = 'AD060326000001'
        self.c.save()
        exp = self.new_invoice(tax_treatment=gst.EXPORT)
        lut = self.new_invoice(tax_treatment=gst.EXPORT_LUT)
        lut.invoice_number = exp.invoice_number
        _, te = self.text(exp)
        _, tl = self.text(lut)
        extra = [l for l in tl.splitlines() if l not in te.splitlines()]
        self.assertEqual(extra, [gst.LUT_NOTE + ' (LUT No. AD060326000001)'])
        self.assertNotIn('TAX INVOICE', tl)


class ExportPdfIdentityTests(TestCase):
    """The export render path must be byte-identical with the GST code present (invariant PDF output)."""
    def test_export_bytes_unchanged_by_gst_fields(self):
        from reportlab import rl_config
        old = rl_config.invariant
        rl_config.invariant = 1
        try:
            profile = BusinessProfile()
            c = Client(name='US Client', address='1 Main St\nPortland\nUSA - 97210', country='USA')
            lines = [InvoiceLine(project_name='P', description='Support', quantity_days=D('23'),
                                 unit_price=D('3225.80'), amount=D('74193.00'), sac_code='998314', gst_rate=D('18'))]
            inv = Invoice(client=c, invoice_number='QB020', invoice_date=date(2026, 11, 2), terms_days=31,
                          due_date=date(2026, 12, 5), total=D('74193.00'))
            data = build_invoice_data(inv, profile, lines=lines)
            self.assertFalse(data.tax_invoice)
            self.assertEqual(data.lut_note, '')
            a = render_invoice_pdf(data)
            # same data without any GST extras (pre-GST construction)
            data.lines[0].sac_code = ''
            b = render_invoice_pdf(data)
            self.assertEqual(hashlib.sha256(a).hexdigest(), hashlib.sha256(b).hexdigest())
        finally:
            rl_config.invariant = old

    def test_gst_layout_bytes_pinned(self):
        """GST Tax Invoice (intra and inter), the LUT note line and Standard export stay byte-identical to the
        pre-format renderer, both directly and through the format dispatcher (null format / Standard engine)."""
        from reportlab import rl_config
        from .models import InvoiceFormatTemplate
        from .pdf.formats import render_for_invoice
        from .pdf.samples import sample_invoice
        from .test_formats import PINNED
        std = InvoiceFormatTemplate.objects.get(code='quantbud-standard')
        old = rl_config.invariant
        rl_config.invariant = 1
        try:
            for kind in ('gst_intra', 'gst_inter', 'lut', 'export'):
                inv, lines = sample_invoice(kind, 'QB020')
                data = build_invoice_data(inv, BusinessProfile(), lines=lines)
                self.assertEqual(data.tax_invoice, kind.startswith('gst'))
                self.assertEqual(hashlib.sha256(render_invoice_pdf(data)).hexdigest(), PINNED[kind], kind)
                for tpl in (None, std):
                    pdf = render_for_invoice(inv, BusinessProfile(), lines=lines, template=tpl)
                    self.assertEqual(hashlib.sha256(pdf).hexdigest(), PINNED[kind], kind)
        finally:
            rl_config.invariant = old
