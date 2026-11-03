"""Stage 6: invoice format templates (master, engines, snapshot, numbering, byte-identity)."""
import hashlib
import unittest
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.http import QueryDict
from django.test import TestCase
from django.urls import reverse

from . import billing, gst, services
from .forms import ClientForm, InvoiceFormatTemplateForm
from .models import BusinessProfile, Client, Invoice, InvoiceFormatTemplate
from .pdf import render_invoice_pdf
from .pdf.formats import ENGINES, render_for_invoice
from .pdf.from_models import build_invoice_data
from .pdf.samples import KINDS, sample_invoice

try:
    import fitz  # PyMuPDF (optional, used to read PDF text and page size)
except ImportError:  # pragma: no cover
    fitz = None

needs_fitz = unittest.skipIf(fitz is None, 'PyMuPDF not installed')

# SHA-256 of the ORIGINAL renderer's output (taken before Stage 6) with rl_config.invariant=1 for
# sample_invoice(kind, 'QB020') + BusinessProfile(). Standard export (US Letter, 5 columns) and the GST
# Tax Invoice (intra / inter) and the LUT note line must stay byte-identical.
PINNED = {
    'export': '3b06365ca951482d39c5e2de35cab9c441449d6ce8ef32d43a9e544f744fdb34',
    'lut': 'f7bf6f6220b9f87abb4193189d200f2ed00f5d06eeef422d80268076533552be',
    'gst_intra': '6098b8ce4b5f0facf9b379f1edeeab24522882d91342545096a39f364d9ecd47',
    'gst_inter': 'd13e65206e251c09267650e640da2aac8f625d68278a136c667be8d4f9684826',
}
NEW_CODES = ['modern-minimal', 'corporate-classic', 'compact', 'bold-header', 'timesheet']
A4 = (595.28, 841.89)
LETTER = (612.0, 792.0)


class invariant:
    def __enter__(self):
        from reportlab import rl_config
        self.old = rl_config.invariant
        rl_config.invariant = 1

    def __exit__(self, *a):
        from reportlab import rl_config
        rl_config.invariant = self.old


def sha(b):
    return hashlib.sha256(b).hexdigest()


def pdf_text(pdf):
    doc = fitz.open(stream=pdf, filetype='pdf')
    return '\n'.join(p.get_text() for p in doc), [(round(p.rect.width, 2), round(p.rect.height, 2)) for p in doc]


class SeedTests(TestCase):
    def test_seeded_formats(self):
        codes = dict(InvoiceFormatTemplate.objects.values_list('code', 'layout_engine'))
        self.assertEqual(codes['quantbud-standard'], 'standard')
        for code in NEW_CODES:
            self.assertIn(code, codes)
        self.assertEqual(set(codes[c] for c in NEW_CODES), set(ENGINES))
        std = InvoiceFormatTemplate.objects.get(code='quantbud-standard')
        self.assertEqual((std.name, std.page_size, std.is_builtin), ('QuantBud Standard (default)', 'LETTER', True))
        for t in InvoiceFormatTemplate.objects.filter(code__in=NEW_CODES):
            self.assertEqual(t.page_size, 'A4')
            self.assertTrue(t.is_active)

    def test_model_default_page_size_is_a4(self):
        self.assertEqual(InvoiceFormatTemplate(name='x', code='x').page_size, 'A4')


class ByteIdentityTests(TestCase):
    """Null format and the Standard engine reproduce the original renderer exactly."""
    def test_pinned_hashes_and_standard_engine(self):
        std = InvoiceFormatTemplate.objects.get(code='quantbud-standard')
        with invariant():
            for kind in KINDS:
                inv, lines = sample_invoice(kind, 'QB020')
                profile = BusinessProfile()
                original = render_invoice_pdf(build_invoice_data(inv, profile, lines=lines))
                self.assertEqual(sha(original), PINNED[kind], kind)
                self.assertEqual(sha(render_for_invoice(inv, profile, lines=lines)), PINNED[kind], kind)
                self.assertEqual(sha(render_for_invoice(inv, profile, lines=lines, template=std)), PINNED[kind])
                inv.format_snapshot = std.settings_snapshot()
                self.assertEqual(sha(render_for_invoice(inv, profile, lines=lines)), PINNED[kind], kind)


@needs_fitz
class EngineRenderTests(TestCase):
    def test_each_engine_each_kind(self):
        profile = BusinessProfile()
        for code in NEW_CODES:
            t = InvoiceFormatTemplate.objects.get(code=code)
            for kind in KINDS:
                with self.subTest(code=code, kind=kind):
                    inv, lines = sample_invoice(kind, 'QB777')
                    text, sizes = pdf_text(render_for_invoice(inv, profile, lines=lines, template=t))
                    flat = ' '.join(text.split())
                    self.assertIn('QB777', flat)
                    for amt in ('74,193.00', '40,000.00'):
                        self.assertIn(amt, flat)
                    self.assertIn('Acme Analytics Inc.' if kind in ('export', 'lut') else 'Northwind', flat)
                    self.assertIn('23.00', flat)
                    self.assertIn('3,225.80', flat)
                    self.assertIn('02 Nov 2026', flat)
                    self.assertIn('03 Dec 2026', flat)
                    self.assertEqual(sizes[0], A4)
                    if kind in ('export', 'lut'):
                        self.assertIn('1,14,193.00', flat)
                        self.assertNotIn('TAX INVOICE', flat)
                    else:
                        self.assertIn('TAX INVOICE', flat)
                        self.assertIn('1,34,747.74', flat)   # grand total (saved)
                        self.assertIn('Taxable Value', flat)
                        self.assertIn('SAC: 998314', flat)
                        self.assertIn(inv.client.gstin, flat)
                        self.assertIn('Place of Supply', flat)
                        self.assertIn('Rupees', flat)
                        if kind == 'gst_intra':
                            self.assertIn('10,277.37', flat)
                            self.assertIn('CGST @ 9%', flat)
                            self.assertIn('SGST @ 9%', flat)
                        else:
                            self.assertIn('20,554.74', flat)
                            self.assertIn('IGST @ 18%', flat)
                    if kind == 'lut':
                        self.assertIn('LUT', flat)
                        self.assertIn('AD060326000001', flat)
                    else:
                        self.assertNotIn('without payment of IGST', flat)

    def test_letter_and_landscape_respected(self):
        t = InvoiceFormatTemplate.objects.get(code='modern-minimal')
        inv, lines = sample_invoice('export')
        t.page_size = 'LETTER'
        self.assertEqual(pdf_text(render_for_invoice(inv, BusinessProfile(), lines=lines, template=t))[1][0], LETTER)
        t.orientation = 'landscape'
        self.assertEqual(pdf_text(render_for_invoice(inv, BusinessProfile(), lines=lines, template=t))[1][0],
                         (LETTER[1], LETTER[0]))
        t.page_size = 'A4'
        self.assertEqual(pdf_text(render_for_invoice(inv, BusinessProfile(), lines=lines, template=t))[1][0],
                         (A4[1], A4[0]))

    def test_label_and_title_overrides_and_extras(self):
        t = InvoiceFormatTemplate.objects.get(code='corporate-classic')
        t.title_text, t.col_project_label, t.col_amount_label = 'BILL', 'Resource', 'Line Total'
        inv, lines = sample_invoice('export')
        flat = ' '.join(pdf_text(render_for_invoice(inv, BusinessProfile(), lines=lines, template=t))[0].split())
        for s in ('BILL', 'Resource', 'Line Total', 'Memo/Description', 'PO-4500012345', 'V-20931'):
            self.assertIn(s, flat)
        t.show_po_number = t.show_vendor_code = False
        flat = ' '.join(pdf_text(render_for_invoice(inv, BusinessProfile(), lines=lines, template=t))[0].split())
        self.assertNotIn('PO-4500012345', flat)
        self.assertNotIn('V-20931', flat)

    def test_timesheet_prints_periods(self):
        t = InvoiceFormatTemplate.objects.get(code='timesheet')
        inv, lines = sample_invoice('export')
        flat = ' '.join(pdf_text(render_for_invoice(inv, BusinessProfile(), lines=lines, template=t))[0].split())
        self.assertIn('Period: 01 Oct - 31 Oct 2026', flat)
        self.assertIn('October 2026', flat)

    def test_many_lines_paginate_with_totals(self):
        t = InvoiceFormatTemplate.objects.get(code='bold-header')
        inv, lines = sample_invoice('gst_inter')
        lines = lines * 30
        text, sizes = pdf_text(render_for_invoice(inv, BusinessProfile(), lines=lines, template=t))
        self.assertGreater(len(sizes), 1)
        self.assertIn('Page 2', text)
        self.assertIn('1,34,747.74', ' '.join(text.split()))


class FlowTests(TestCase):
    def setUp(self):
        self.profile = BusinessProfile.current()
        self.modern = InvoiceFormatTemplate.objects.get(code='modern-minimal')
        self.compact = InvoiceFormatTemplate.objects.get(code='compact')
        self.c = Client.objects.create(name='Fmt Client', address='1 St', country='USA', invoice_format=self.modern,
                                       vendor_code='VC-1')

    def post(self, **extra):
        d = {'client': self.c.pk, 'invoice_date': '2026-11-02', 'terms_mode': 'custom', 'terms_text': '31',
             'line_project': [''], 'line_name': ['Custom'], 'line_desc': ['Work'], 'line_from': ['2026-10-01'],
             'line_to': ['2026-10-31'], 'line_days': ['31'], 'line_unit': ['100'], 'line_amount': ['']}
        d.update(extra)
        q = QueryDict(mutable=True)
        for k, v in d.items():
            q.setlist(k, v) if isinstance(v, list) else q.__setitem__(k, str(v))
        return q

    def save(self, **extra):
        data = services.parse_invoice_post(self.post(**extra), self.c, self.profile)
        self.assertEqual(data.errors, [])
        return services.save_invoice(data)

    def test_defaults_from_client_and_override(self):
        a = self.save(po_number='PO-9')
        self.assertEqual((a.format_template, a.po_number), (self.modern, 'PO-9'))
        self.assertEqual(a.format_snapshot['layout_engine'], 'modern_minimal')
        self.assertEqual(a.format_snapshot['vendor_code'], 'VC-1')
        b = self.save(format_template=str(self.compact.pk))
        self.assertEqual(b.format_template, self.compact)
        self.c.invoice_format = None
        self.c.save()
        c = self.save()
        self.assertIsNone(c.format_template)
        self.assertIsNone(c.format_snapshot)

    def test_inactive_override_rejected(self):
        self.compact.is_active = False
        self.compact.save()
        data = services.parse_invoice_post(self.post(format_template=str(self.compact.pk)), self.c, self.profile)
        self.assertTrue(any('format' in e for e in data.errors))

    def test_numbering_consecutive_across_formats(self):
        start = self.profile.next_invoice_number
        nums = []
        for fmt in ('', str(self.compact.pk), '', str(self.modern.pk)):
            nums.append(self.save(format_template=fmt).invoice_number)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.next_invoice_number, start + 4)
        self.assertEqual(len(set(nums)), 4)
        self.assertEqual(nums, sorted(nums, key=lambda s: int(''.join(ch for ch in s if ch.isdigit()) or 0)))
        self.assertEqual(Invoice.objects.count(), 4)

    def test_amounts_identical_across_formats(self):
        a = self.save(format_template=str(self.modern.pk))
        b = self.save(format_template=str(self.compact.pk))
        self.c.invoice_format = None
        self.c.save()
        c = self.save()
        for f in ('total', 'taxable_value', 'total_tax', 'grand_total'):
            self.assertEqual(getattr(a, f), getattr(b, f))
            self.assertEqual(getattr(a, f), getattr(c, f))

    def test_snapshot_immutable_after_template_edit(self):
        inv = self.save()
        from . import ledger
        ledger.finalize_invoice(inv)
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.FINALIZED)
        self.modern.accent_color, self.modern.title_text, self.modern.page_size = '#FF0000', 'CHANGED', 'LETTER'
        self.modern.save()
        inv.refresh_from_db()
        self.assertEqual(inv.format_snapshot['accent_color'], '#0F766E')
        self.assertEqual(inv.format_snapshot['title_text'], '')
        # Re-saving through the form flow keeps the snapshot (and the PO) of a finalized invoice.
        data = services.parse_invoice_post(self.post(format_template=str(self.compact.pk), po_number='NEW'),
                                           self.c, self.profile)
        services.save_invoice(data, inv)
        inv.refresh_from_db()
        self.assertEqual((inv.format_template, inv.format_snapshot['title_text'], inv.po_number),
                         (self.modern, '', ''))
        # Direct tampering is refused by the lifecycle guard.
        inv.format_snapshot = self.modern.settings_snapshot()
        with self.assertRaises(ValidationError):
            inv.save()

    @needs_fitz
    def test_pdf_uses_snapshot_and_adhoc_format(self):
        inv = self.save()
        self.modern.title_text = 'EDITED TITLE'
        self.modern.save()
        r = self.client.get(reverse('invoice_pdf', args=[inv.pk]))
        text, sizes = pdf_text(r.content)
        self.assertNotIn('EDITED TITLE', text)
        self.assertEqual(sizes[0], A4)
        r = self.client.get(reverse('invoice_pdf', args=[inv.pk]) + f'?format={self.compact.pk}&download=1')
        self.assertIn(f'{inv.display_number}-compact.pdf', r['Content-Disposition'])
        self.assertIn(inv.display_number, pdf_text(r.content)[0])
        std = InvoiceFormatTemplate.objects.get(code='quantbud-standard')
        r = self.client.get(reverse('invoice_pdf', args=[inv.pk]) + f'?format={std.pk}')
        self.assertEqual(pdf_text(r.content)[1][0], LETTER)
        self.modern.refresh_from_db()
        self.assertEqual(Invoice.objects.get(pk=inv.pk).format_template, self.modern)  # ad-hoc saved nothing

    def test_null_format_invoice_pdf_is_original_bytes(self):
        self.c.invoice_format = None
        self.c.save()
        inv = self.save()
        with invariant():
            original = render_invoice_pdf(build_invoice_data(inv, BusinessProfile.current()))
            r = self.client.get(reverse('invoice_pdf', args=[inv.pk]))
        self.assertEqual(sha(r.content), sha(original))

    def test_detail_and_form_pages(self):
        inv = self.save(po_number='PO-77')
        r = self.client.get(reverse('invoice_detail', args=[inv.pk]))
        self.assertContains(r, 'Download in another format')
        self.assertContains(r, 'PO-77')
        r = self.client.get(reverse('invoice_edit', args=[inv.pk]))
        self.assertContains(r, 'name="format_template"')
        self.assertContains(r, 'value="PO-77"')
        r = self.client.get(reverse('client_detail', args=[self.c.pk]))
        self.assertContains(r, 'VC-1')
        self.assertContains(r, 'Modern Minimal')

    def test_edit_preview_uses_chosen_format_and_consumes_no_number(self):
        start = BusinessProfile.current().next_invoice_number
        d = {k: (v if isinstance(v, list) else str(v)) for k, v in self.post(action='preview').lists()}
        d = {k: (v if len(v) > 1 else v[0]) for k, v in d.items()}
        r = self.client.post(reverse('new_invoice'), d)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(BusinessProfile.current().next_invoice_number, start)
        if fitz:
            self.assertEqual(pdf_text(r.content)[1][0], A4)


class MasterTests(TestCase):
    def form(self, **kw):
        d = {'name': 'My Format', 'code': 'my-format', 'layout_engine': 'modern_minimal', 'page_size': 'A4',
             'orientation': 'portrait', 'accent_color': '#123abc', 'is_active': 'on'}
        d.update(kw)
        return d

    def test_form_validation(self):
        f = InvoiceFormatTemplateForm(self.form())
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data['accent_color'], '#123ABC')
        self.assertIn('accent_color', InvoiceFormatTemplateForm(self.form(accent_color='blue')).errors)
        self.assertIn('code', InvoiceFormatTemplateForm(self.form(code='not a slug!')).errors)
        self.assertIn('name', InvoiceFormatTemplateForm(self.form(name='Modern Minimal')).errors)
        self.assertIn('code', InvoiceFormatTemplateForm(self.form(code='compact')).errors)
        self.assertIn('page_size', InvoiceFormatTemplateForm(self.form(layout_engine='standard')).errors)
        self.assertTrue(InvoiceFormatTemplateForm(self.form(layout_engine='standard', page_size='LETTER')).is_valid())
        self.assertIn('layout_engine', InvoiceFormatTemplateForm(self.form(layout_engine='nope')).errors)

    def test_master_crud_and_toggle(self):
        r = self.client.get(reverse('format_templates'))
        for name in ('QuantBud Standard (default)', 'Modern Minimal', 'Corporate Classic', 'Compact One-Page',
                     'Bold Header', 'Timesheet / Service Detail'):
            self.assertContains(r, name)
        r = self.client.post(reverse('format_template_add'), self.form(action='save'))
        self.assertRedirects(r, reverse('format_templates'))
        t = InvoiceFormatTemplate.objects.get(code='my-format')
        self.client.post(reverse('format_template_edit', args=[t.pk]), self.form(name='Renamed', action='save'))
        t.refresh_from_db()
        self.assertEqual(t.name, 'Renamed')
        self.client.post(reverse('format_template_toggle', args=[t.pk]))
        t.refresh_from_db()
        self.assertFalse(t.is_active)
        self.assertEqual(self.client.get(reverse('format_templates') + '?status=inactive').context['templates'].count(), 1)
        self.client.post(reverse('format_template_delete', args=[t.pk]))
        self.assertFalse(InvoiceFormatTemplate.objects.filter(pk=t.pk).exists())

    def test_delete_protected(self):
        t = InvoiceFormatTemplate.objects.get(code='compact')
        c = Client.objects.create(name='P', invoice_format=t)
        with self.assertRaises(ProtectedError):
            t.delete()
        r = self.client.post(reverse('format_template_delete', args=[t.pk]), follow=True)
        self.assertContains(r, 'cannot be deleted')
        self.assertTrue(InvoiceFormatTemplate.objects.filter(pk=t.pk).exists())
        c.invoice_format = None
        c.save()
        inv = Invoice.objects.create(client=c, invoice_number='ZZ1', invoice_date='2026-11-02', total=D('1.00'),
                                     format_template=t)
        self.assertEqual(inv.format_snapshot['code'], 'compact')   # filled on save
        with self.assertRaises(ProtectedError):
            t.delete()

    def test_preview_consumes_no_number_and_saves_nothing(self):
        start = BusinessProfile.current().next_invoice_number
        n = InvoiceFormatTemplate.objects.count()
        t = InvoiceFormatTemplate.objects.get(code='bold-header')
        for kind in KINDS:
            r = self.client.get(reverse('format_template_preview', args=[t.pk]) + f'?kind={kind}')
            self.assertEqual(r['Content-Type'], 'application/pdf')
        r = self.client.post(reverse('format_template_add'), self.form(action='preview', kind='gst_intra'))
        self.assertEqual(r['Content-Type'], 'application/pdf')
        r = self.client.post(reverse('format_template_edit', args=[t.pk]),
                             self.form(name=t.name, code=t.code, title_text='PREVIEW ONLY', action='preview'))
        self.assertEqual(r['Content-Type'], 'application/pdf')
        t.refresh_from_db()
        self.assertEqual(t.title_text, '')
        self.assertEqual(InvoiceFormatTemplate.objects.count(), n)
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(BusinessProfile.current().next_invoice_number, start)
        if fitz:
            flat = ' '.join(pdf_text(r.content)[0].split())
            self.assertIn('PREVIEW ONLY', flat)
            self.assertIn('SAMPLE-001', flat)

    def test_client_form_fields(self):
        t = InvoiceFormatTemplate.objects.get(code='timesheet')
        f = ClientForm({'name': 'New C', 'billing_type': Client._meta.get_field('billing_type').default,
                        'terms_mode': 'custom', 'vendor_code': 'V-1', 'invoice_format': t.pk, 'active': 'on'})
        self.assertTrue(f.is_valid(), f.errors)
        c = f.save()
        self.assertEqual((c.vendor_code, c.invoice_format), ('V-1', t))
        t.is_active = False
        t.save()
        self.assertFalse(ClientForm({'name': 'New D', 'billing_type': c.billing_type, 'terms_mode': 'custom',
                                     'invoice_format': t.pk}).is_valid())
        self.assertTrue(ClientForm({'name': c.name, 'billing_type': c.billing_type, 'terms_mode': 'custom',
                                    'invoice_format': t.pk}, instance=c).is_valid())
