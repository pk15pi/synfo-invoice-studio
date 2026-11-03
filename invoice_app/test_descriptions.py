"""Project-wise invoice Memo/Description configuration (acceptance scenarios 1-16 + JS behaviour)."""
import base64
import os
import re
import zlib
from datetime import date
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse

from . import descriptions
from .forms import InvoiceDescriptionTemplateForm, ProjectTemplateForm
from .models import BusinessProfile, Client, Invoice, InvoiceDescriptionTemplate as T, InvoiceLine, ProjectTemplate
from .pdf import render_invoice_pdf
from .pdf.from_models import build_invoice_data



def _objects(data):
    return {int(m.group(1)): m.group(2) for m in re.finditer(rb'(\d+) 0 obj(.*?)endobj', data, re.S)}


def _stream(body):
    m = re.search(rb'stream\r?\n', body)
    ln = re.search(rb'/Length (\d+)', body)
    if not m or not ln:
        return b''
    head, raw = body[:m.start()], body[m.end():m.end() + int(ln.group(1))]
    if b'ASCII85Decode' in head:
        raw = base64.a85decode(raw.strip().removesuffix(b'~>'), adobe=False)
    if b'FlateDecode' in head:
        raw = zlib.decompress(raw)
    return raw


def _cmap(text):
    out = {}
    for blk in re.findall(rb'beginbfchar(.*?)endbfchar', text, re.S):
        for a, b in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', blk):
            out[int(a, 16)] = bytes.fromhex(b.decode()).decode('utf-16-be')
    for blk in re.findall(rb'beginbfrange(.*?)endbfrange', text, re.S):
        for a, z, b in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', blk):
            s = int(b, 16)
            for i, code in enumerate(range(int(a, 16), int(z, 16) + 1)):
                out[code] = chr(s + i)
    return out


def _literal(s):
    out, i = bytearray(), 0
    esc = {b'n': 10, b'r': 13, b't': 9, b'b': 8, b'f': 12, b'(': 40, b')': 41, b'\\': 92}
    while i < len(s):
        c = s[i:i + 1]
        if c == b'\\':
            n = s[i + 1:i + 2]
            if n in esc:
                out.append(esc[n]); i += 2
            elif n.isdigit():
                m = re.match(rb'[0-7]{1,3}', s[i + 1:]); out.append(int(m.group(), 8) & 255); i += 1 + len(m.group())
            else:
                i += 2
        else:
            out += c; i += 1
    return bytes(out)


def extract_text(data):
    """Tiny text extractor for reportlab PDFs (test helper; no external dependency).
    Returns one string per text-showing operator, in drawing order."""
    objs = _objects(data)
    fonts = {}
    for body in objs.values():
        for ref in re.findall(rb'/Font\s+(\d+) 0 R', body):
            for name, num in re.findall(rb'/([\w+]+)\s+(\d+) 0 R', objs.get(int(ref), b'')):
                fbody = objs.get(int(num), b'')
                tu = re.search(rb'/ToUnicode\s+(\d+) 0 R', fbody)
                fonts[name] = _cmap(_stream(objs[int(tu.group(1))])) if tu else None
    pieces = []
    for body in objs.values():
        content = _stream(body)
        if b'Tj' not in content and b'TJ' not in content:
            continue
        cur = None
        for tok in re.finditer(rb'/([\w+]+)\s+[\d.]+\s+Tf|\(((?:\\.|[^\\)])*)\)\s*Tj|<([0-9A-Fa-f]*)>\s*Tj', content, re.S):
            if tok.group(1):
                cur = fonts.get(tok.group(1)); continue
            raw = _literal(tok.group(2)) if tok.group(2) is not None else bytes.fromhex(tok.group(3).decode())
            pieces.append(''.join(cur.get(b, '') for b in raw) if cur else raw.decode('latin-1'))
    return pieces


class Base(TestCase):
    def setUp(self):
        BusinessProfile.current()
        self.c = Client.objects.create(name='American Chase LLC', billing_type=Client.MONTHLY_PRORATED)
        self.support = T.objects.get(name='Monthly Support')  # seeded by migration 0009
        self.general = T.objects.get(name='General Services')

    def project(self, name, **kw):
        return ProjectTemplate.objects.create(client=self.c, name=name, monthly_amount=31000, **kw)

    def form_project(self, data, instance=None):
        base = {'name': 'Alice - Alpha', 'invoice_description_type': 'blank', 'description_template': '',
                'description': '', 'monthly_amount': '31000', 'active': 'on'}
        base.update(data)
        return ProjectTemplateForm(base, instance=instance, client=self.c)

    def post(self, projects, descs=None, month=('2026', '10'), dates=None, days=None, units=None, invoice=None):
        n = len(projects)
        dates = dates or [('2026-10-01', '2026-10-31')] * n
        d = {'client': self.c.pk, 'invoice_date': '2026-11-02', 'terms_mode': 'custom', 'terms_text': '31',
             'due_date': '', 'billing_year': month[0], 'billing_mon': month[1],
             'line_project': [p.pk for p in projects], 'line_name': [p.name for p in projects],
             'line_desc': descs or [''] * n, 'line_from': [a for a, _ in dates], 'line_to': [b for _, b in dates],
             'line_days': days or ['31'] * n, 'line_unit': units or [''] * n, 'line_amount': [''] * n,
             'action': 'save'}
        url = reverse('invoice_edit', args=[invoice.pk]) if invoice else reverse('new_invoice')
        r = self.client.post(url, d)
        self.assertEqual(r.status_code, 302, getattr(r, 'content', b'')[:500])
        return Invoice.objects.get(pk=invoice.pk) if invoice else Invoice.objects.latest('id')

    def descs(self, inv):
        return list(inv.lines.values_list('description', flat=True))

    def endpoint(self, p, frm='2026-10-01', to='2026-10-31', month='2026-10'):
        r = self.client.get(reverse('line_description'), {'project': p.pk, 'from': frm, 'to': to, 'billing_month': month})
        self.assertEqual(r.status_code, 200)
        return r.json()['description']


class SeedAndMigrationTests(Base):
    def test_seeded_templates_come_from_db(self):
        self.assertEqual(dict(T.objects.values_list('name', 'template_text')), {
            'Monthly Support': 'Support - {start_date} - {end_date}',
            'Consulting': 'Consulting Services - {start_date} - {end_date}',
            'Development': 'Development Services - {start_date} - {end_date}',
            'General Services': 'Professional Services for {month} {year}'})

    def test_default_project_keeps_legacy_output(self):
        p = self.project('Legacy')
        self.assertEqual(p.invoice_description_type, 'custom')
        self.assertEqual(self.endpoint(p, to='2026-10-23'), 'Support - October 01 - October 23, 2026')


class ScenarioTests(Base):
    # 1-3
    def test_01_03_fixed_template_uses_invoice_dates(self):
        f = self.form_project({'invoice_description_type': 'template', 'description_template': self.support.pk})
        self.assertTrue(f.is_valid(), f.errors)
        p = f.save(commit=False); p.client = self.c; p.save()
        self.assertEqual(p.description_template, self.support)
        inv = self.post([p], dates=[('2026-10-05', '2026-10-20')], days=['16'])
        self.assertEqual(self.descs(inv), ['Support - October 05 - October 20, 2026'])
        self.assertEqual(inv.lines.get().description_template, self.support)
        self.assertEqual(self.endpoint(p, '2026-10-05', '2026-10-20'), 'Support - October 05 - October 20, 2026')

    def test_03_month_year_from_billing_month_not_today(self):
        p = self.project('G', invoice_description_type='template', description_template=self.general)
        inv = self.post([p], month=('2025', '02'), dates=[('2025-02-01', '2025-02-28')], days=['28'])
        self.assertEqual(self.descs(inv), ['Professional Services for February 2025'])

    def test_03_project_and_client_placeholders(self):
        p = self.project('John Hopkins', invoice_description_type='custom',
                         description='{project_name} - Professional Services for {month} {year} ({client_name})')
        self.assertEqual(self.endpoint(p), 'John Hopkins - Professional Services for October 2026 (American Chase LLC)')

    # 4-5
    def test_04_05_custom_text(self):
        f = self.form_project({'invoice_description_type': 'custom', 'description': 'Support Services for October 2026'})
        self.assertTrue(f.is_valid(), f.errors)
        p = f.save(commit=False); p.client = self.c; p.save()
        self.assertIsNone(p.description_template)
        self.assertEqual(self.descs(self.post([p])), ['Support Services for October 2026'])

    # 6-7
    def test_06_07_blank(self):
        f = self.form_project({'invoice_description_type': 'blank', 'description': ''})
        self.assertTrue(f.is_valid(), f.errors)
        p = f.save(commit=False); p.client = self.c; p.save()
        self.assertEqual(self.endpoint(p), '')
        self.assertEqual(self.descs(self.post([p])), [''])

    # 8
    def test_08_multiple_mixed_rows(self):
        a = self.project('A', invoice_description_type='template', description_template=self.support)
        b = self.project('B', invoice_description_type='custom', description='Custom B for {month}')
        c = self.project('C', invoice_description_type='blank', description='')
        inv = self.post([a, b, c])
        self.assertEqual(self.descs(inv), ['Support - October 01 - October 31, 2026', 'Custom B for October', ''])

    # 9-10
    def test_09_10_manual_edit_preserved_with_qty_and_price_changes(self):
        a = self.project('A', invoice_description_type='template', description_template=self.support)
        inv = self.post([a], descs=['My own memo'], days=['12'], units=['900'])
        line = inv.lines.get()
        self.assertEqual((line.description, line.quantity_days), ('My own memo', 12))
        inv = self.post([a], descs=['My own memo'], days=['20'], units=['1500'], invoice=inv)
        self.assertEqual(self.descs(inv), ['My own memo'])

    # 11-12
    def test_11_12_project_edit_affects_only_new_invoices(self):
        p = self.project('A', invoice_description_type='custom', description='Old text {month}')
        old = self.post([p])
        f = self.form_project({'name': 'A', 'invoice_description_type': 'template',
                               'description_template': self.general.pk}, instance=p)
        self.assertTrue(f.is_valid(), f.errors); f.save()
        new = self.post([p], month=('2026', '11'), dates=[('2026-11-01', '2026-11-30')], days=['30'])
        self.assertEqual(self.descs(Invoice.objects.get(pk=old.pk)), ['Old text October'])
        self.assertEqual(self.descs(new), ['Professional Services for November 2026'])

    # 13
    def test_13_template_edit_or_deactivate_leaves_old_invoices(self):
        p = self.project('A', invoice_description_type='template', description_template=self.support)
        inv = self.post([p])
        self.support.template_text = 'Changed {month}'; self.support.save()
        self.assertEqual(self.descs(Invoice.objects.get(pk=inv.pk)), ['Support - October 01 - October 31, 2026'])
        self.client.post(reverse('description_template_toggle', args=[self.support.pk]))
        self.assertFalse(T.objects.get(pk=self.support.pk).is_active)
        self.assertEqual(self.descs(Invoice.objects.get(pk=inv.pk)), ['Support - October 01 - October 31, 2026'])
        pdf = self.client.get(reverse('invoice_pdf', args=[inv.pk])).content
        self.assertIn('Support - October 01 - October 31, 2026', extract_text(pdf))
        # an already-assigned inactive template stays valid on the project's edit form
        f = self.form_project({'name': 'A', 'invoice_description_type': 'template',
                               'description_template': self.support.pk}, instance=p)
        self.assertTrue(f.is_valid(), f.errors)

    # 14
    def test_14_unsupported_placeholders_rejected(self):
        for bad, msg in [('Fee {amount}', 'Unsupported placeholder "{amount}"'), ('{month:>9}', 'not allowed'),
                         ('{month.upper}', 'not allowed'), ('{year[0]}', 'not allowed'), ('Oops {month', 'Unbalanced'),
                         ('x }', 'Unbalanced'), ('{}', 'Empty placeholder'), ('{__class__}', 'Unsupported')]:
            f = InvoiceDescriptionTemplateForm({'name': 'Bad', 'template_text': bad, 'is_active': 'on'})
            self.assertFalse(f.is_valid(), bad)
            self.assertIn(msg, ' '.join(f.errors['template_text']), bad)
        f = self.form_project({'invoice_description_type': 'custom', 'description': 'Hi {consultant}'})
        self.assertFalse(f.is_valid())
        self.assertIn('Unsupported placeholder', str(f.errors['description']))
        ok = InvoiceDescriptionTemplateForm({'name': 'Braces', 'template_text': 'Lit {{x}} {month}', 'is_active': 'on'})
        self.assertTrue(ok.is_valid(), ok.errors)
        self.assertEqual(descriptions.render_text('Lit {{x}} {month}', billing_month=date(2026, 1, 1)), 'Lit {x} January')

    # 15
    def test_15_missing_dates_leave_no_placeholders(self):
        r = descriptions.render_text
        self.assertEqual(r('Support - {start_date} - {end_date}'), 'Support')
        self.assertEqual(r('Support - {start_date} - {end_date}', end_date=date(2026, 10, 31)), 'Support - October 31, 2026')
        self.assertEqual(r('Support - {start_date} - {end_date}', start_date=date(2026, 10, 1)), 'Support - October 01, 2026')
        self.assertEqual(r('Services for {month} {year}'), 'Services for')
        self.assertEqual(r('Services for {month} {year}', end_date=date(2026, 3, 9)), 'Services for March 2026')
        self.assertEqual(r('{start_date} - {end_date}, {client_name}'), '')
        self.assertEqual(r('Dec - Jan', start_date=date(2026, 12, 20)), 'Dec - Jan')
        self.assertEqual(r('X {start_date} - {end_date}', start_date=date(2026, 12, 20), end_date=date(2027, 1, 5)),
                         'X December 20, 2026 - January 05, 2027')
        p = self.project('A', invoice_description_type='template', description_template=self.support)
        inv = self.post([p], month=('', ''), dates=[('', '')])
        self.assertEqual(self.descs(inv), ['Support'])
        self.assertNotIn('{', self.endpoint(p, '', '', ''))

    # 16
    def test_16_pdf_prints_saved_line_text_only(self):
        a = self.project('A', invoice_description_type='template', description_template=self.support)
        b = self.project('B', invoice_description_type='custom', description='Custom {month} {year}')
        inv = self.post([a, b], descs=['', 'Edited by hand'])
        texts = extract_text(self.client.get(reverse('invoice_pdf', args=[inv.pk])).content)
        for d in self.descs(inv):
            self.assertIn(d, texts)
        # later project/template changes do not alter the stored PDF text
        ProjectTemplate.objects.filter(pk=a.pk).update(invoice_description_type='blank')
        T.objects.filter(pk=self.support.pk).update(template_text='Different')
        texts2 = extract_text(render_invoice_pdf(build_invoice_data(Invoice.objects.get(pk=inv.pk), BusinessProfile.current())))
        self.assertIn('Support - October 01 - October 31, 2026', texts2)
        self.assertIn('Edited by hand', texts2)
        self.assertNotIn('Different', texts2)

    def test_pdf_adapter_never_reads_project_or_template(self):
        import inspect
        from .pdf import from_models, render
        for mod in (from_models, render):
            src = inspect.getsource(mod)
            for word in ('description_template', 'invoice_description_type', 'descriptions', '.project.'):
                self.assertNotIn(word, src, f'{mod.__name__} mentions {word}')


class ValidationAndProtectionTests(Base):
    def test_form_validation_per_type(self):
        f = self.form_project({'invoice_description_type': 'template'})
        self.assertFalse(f.is_valid()); self.assertIn('description_template', f.errors)
        f = self.form_project({'invoice_description_type': 'custom', 'description': '  '})
        self.assertFalse(f.is_valid()); self.assertIn('description', f.errors)
        f = self.form_project({'invoice_description_type': 'blank'})
        self.assertTrue(f.is_valid(), f.errors)
        f = self.form_project({'invoice_description_type': 'custom', 'description': 'x',
                               'description_template': self.support.pk})
        self.assertTrue(f.is_valid(), f.errors)
        self.assertIsNone(f.save(commit=False).description_template)  # only template mode keeps the FK

    def test_inactive_template_not_selectable_for_new_selection(self):
        self.support.is_active = False; self.support.save()
        f = self.form_project({'invoice_description_type': 'template', 'description_template': self.support.pk})
        self.assertFalse(f.is_valid()); self.assertIn('description_template', f.errors)
        self.assertNotIn(self.support, list(f.fields['description_template'].queryset))
        p = self.project('Other', invoice_description_type='template', description_template=self.general)
        f = self.form_project({'name': 'Other', 'invoice_description_type': 'template',
                               'description_template': self.support.pk}, instance=p)
        self.assertFalse(f.is_valid())

    def test_delete_blocked_when_assigned_or_used(self):
        free = T.objects.create(name='Free', template_text='x')
        self.client.post(reverse('description_template_delete', args=[free.pk]))
        self.assertFalse(T.objects.filter(pk=free.pk).exists())
        p = self.project('A', invoice_description_type='template', description_template=self.support)
        with self.assertRaises(ProtectedError):
            T.objects.get(pk=self.support.pk).delete()
        self.client.post(reverse('description_template_delete', args=[self.support.pk]))
        self.assertTrue(T.objects.filter(pk=self.support.pk).exists())
        inv = self.post([p])
        ProjectTemplate.objects.filter(pk=p.pk).update(invoice_description_type='blank', description_template=None)
        with self.assertRaises(ProtectedError):  # still recorded on the saved line
            T.objects.get(pk=self.support.pk).delete()
        self.assertEqual(InvoiceLine.objects.get(invoice=inv).description_template_id, self.support.pk)

    def test_master_pages(self):
        r = self.client.get(reverse('description_templates'))
        self.assertContains(r, 'Monthly Support'); self.assertContains(r, 'General Services')
        r = self.client.post(reverse('description_template_add'), {'name': 'Audit', 'template_text': 'Audit {year}', 'is_active': 'on'})
        self.assertRedirects(r, reverse('description_templates'))
        t = T.objects.get(name='Audit')
        self.client.post(reverse('description_template_edit', args=[t.pk]), {'name': 'Audit', 'template_text': 'Audit FY {year}'})
        t.refresh_from_db(); self.assertEqual((t.template_text, t.is_active), ('Audit FY {year}', False))
        r = self.client.post(reverse('description_template_add'), {'name': 'Audit', 'template_text': 'dup'})
        self.assertContains(r, 'already exists')
        r = self.client.get(reverse('project_add', args=[self.c.pk]))
        self.assertContains(r, 'id_invoice_description_type'); self.assertContains(r, reverse('description_templates'))
        self.assertContains(self.client.get(reverse('projects', args=[self.c.pk])), reverse('description_templates'))


PW = None
try:
    from playwright.sync_api import sync_playwright as PW  # noqa: N811
except ImportError:  # pragma: no cover
    pass


@skipUnless(PW is not None, 'playwright not installed')
class InvoiceFormJsTests(StaticLiveServerTestCase):
    """Real browser against a temp live server + the test database (never db.sqlite3)."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
        super().setUpClass()
        cls.pw = PW().start()
        try:
            cls.browser = cls.pw.chromium.launch()
        except Exception as exc:  # pragma: no cover
            cls.pw.stop(); super().tearDownClass()
            raise __import__('unittest').SkipTest(f'chromium unavailable: {exc}')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop()
        super().tearDownClass()

    def setUp(self):
        BusinessProfile.current()
        self.c = Client.objects.create(name='Acme Corp')
        self.tpl = T.objects.create(name='Support T', template_text='Support - {start_date} - {end_date}')
        self.a = ProjectTemplate.objects.create(client=self.c, name='Alice - Alpha', monthly_amount=31000,
                                                invoice_description_type='template', description_template=self.tpl)
        self.b = ProjectTemplate.objects.create(client=self.c, name='Bob - Beta', monthly_amount=31000,
                                                invoice_description_type='custom', description='Custom Bob for {month} {year}')
        self.d = ProjectTemplate.objects.create(client=self.c, name='Cara - Gamma', monthly_amount=31000,
                                                invoice_description_type='blank', description='')
        self.page = self.browser.new_page(viewport={'width': 1500, 'height': 1100})
        self.shots = os.environ.get('QB_SCREENSHOT_DIR')

    def tearDown(self):
        self.page.close()

    def desc(self, i):
        return self.page.locator('#rows tbody tr').nth(i).locator('[name=line_desc]')

    def wait_desc(self, i, value):
        self.page.wait_for_function(
            '([i, v]) => document.querySelectorAll("#rows tbody tr")[i].querySelector("[name=line_desc]").value === v',
            arg=[i, value], timeout=5000)

    def test_project_form_toggle(self):
        pg = self.page
        pg.goto(self.live_server_url + reverse('project_edit', args=[self.c.pk, self.a.pk]))
        self.assertTrue(pg.is_visible('#id_description_template')); self.assertFalse(pg.is_visible('#id_description'))
        if self.shots:
            pg.screenshot(path=os.path.join(self.shots, 'project_form_template.png'), full_page=True)
        pg.select_option('#id_invoice_description_type', 'custom')
        self.assertFalse(pg.is_visible('#id_description_template')); self.assertTrue(pg.is_visible('#id_description'))
        if self.shots:
            pg.screenshot(path=os.path.join(self.shots, 'project_form_custom.png'), full_page=True)
        pg.select_option('#id_invoice_description_type', 'blank')
        self.assertFalse(pg.is_visible('#id_description_template')); self.assertFalse(pg.is_visible('#id_description'))

    def test_invoice_rows_dirty_flag(self):
        pg = self.page
        pg.goto(self.live_server_url + reverse('new_invoice'))
        pg.select_option('#billing_year', '2026'); pg.select_option('#billing_mon', '10')
        pg.select_option('#client', str(self.c.pk))  # auto-adds one row per active project
        self.wait_desc(0, 'Support - October 01 - October 31, 2026')
        self.wait_desc(1, 'Custom Bob for October 2026')
        self.wait_desc(2, '')
        # manual edit on row 0, then qty / unit price changes never touch it
        self.desc(0).fill('Hand written memo')
        row0 = pg.locator('#rows tbody tr').nth(0)
        row0.locator('[name=line_days]').fill('10'); row0.locator('[name=line_unit]').fill('1234')
        # line date change re-renders only untouched rows
        row1 = pg.locator('#rows tbody tr').nth(1)
        row1.locator('[name=line_from]').fill('2026-10-05')
        row1.locator('[name=line_to]').fill('2026-10-20')
        # billing month change re-renders untouched rows (row 1 keeps its custom dates, month text changes)
        pg.select_option('#billing_mon', '11')
        self.wait_desc(1, 'Custom Bob for November 2026')
        self.wait_desc(0, 'Hand written memo')
        pg.wait_for_timeout(400)
        self.assertEqual(self.desc(0).input_value(), 'Hand written memo')
        self.assertEqual(self.desc(2).input_value(), '')
        if self.shots:
            pg.screenshot(path=os.path.join(self.shots, 'invoice_form_three_rows.png'), full_page=True)
        # re-picking the project restores its default (documented behaviour)
        name0 = row0.locator('[name=line_name]')
        name0.click(); name0.fill('')
        pg.locator('#rows tbody tr').nth(0).locator('.pj-list li', has_text='Alice - Alpha').click()
        self.wait_desc(0, 'Support - November 01 - November 30, 2026')
        # submit with a manual edit: saved verbatim
        self.desc(2).fill('Typed for blank project')
        pg.click('button[name=action][value=save]')
        pg.wait_for_url(re.compile(r'/invoice/\d+/$'))
        inv = Invoice.objects.latest('id')
        self.assertEqual(list(inv.lines.values_list('description', flat=True)),
                         ['Support - November 01 - November 30, 2026', 'Custom Bob for November 2026',
                          'Typed for blank project'])
        # editing that invoice keeps saved text even after the template changes
        T.objects.filter(pk=self.tpl.pk).update(template_text='Changed')
        pg.goto(self.live_server_url + reverse('invoice_edit', args=[inv.pk]))
        pg.wait_for_timeout(500)
        self.assertEqual(self.desc(0).input_value(), 'Support - November 01 - November 30, 2026')
