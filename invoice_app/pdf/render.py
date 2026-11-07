"""Coordinate-based invoice drawing. No business data is hardcoded here: every
string comes from InvoiceData (built from BusinessProfile / Client / Invoice)."""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas

from ..billing import format_indian, format_western
from . import layout as L
from .fonts import font

DEFAULT_SIGNATURE = Path(__file__).resolve().parent.parent / 'assets' / 'signature.png'


@dataclass
class LineData:
    project_name: str
    description: str
    quantity: Decimal
    unit_price: Decimal | None  # None -> Unit Price cell left empty (billing type 3)
    amount: Decimal
    sac_code: str = ''  # printed only on GST tax invoices


@dataclass
class InvoiceData:
    number: str
    invoice_date: date
    terms_days: object  # text printed under Terms
    due_date: object  # date or None (blank)
    currency: str
    total: Decimal
    business_name: str
    business_address_lines: list
    phone: str
    email: str
    gstin: str
    bank: dict
    client_name: str
    client_address_lines: list
    lines: list
    signature_path: str | None = None
    signature_name: str = ''
    footer_text: str = ''
    colors: dict = field(default_factory=dict)
    body_rows: int = L.TABLE['body_rows']
    # --- Optional GST extras. Defaults draw nothing, so the export PDF is byte-identical. ---
    lut_note: str = ''                 # Export under LUT: one note line above Payment Details
    tax_invoice: bool = False          # Domestic GST: TaxInvoiceCanvas layout
    customer_gstin: str = ''
    customer_state: str = ''
    place_of_supply: str = ''
    tax_rows: list = field(default_factory=list)   # [(label, Decimal)] e.g. Taxable Value, CGST @ 9%
    grand_total: Decimal | None = None
    amount_words: str = ''
    service_type: str = ''
    billing_period_start: object = None
    billing_period_end: object = None
    recruitment_agreement_reference: str = ''


def _hex(value, default):
    try:
        return HexColor(value or default)
    except Exception:
        return HexColor(default)


class InvoiceCanvas:
    def __init__(self, c, data):
        self.c = c
        self.d = data
        cols = dict(L.DEFAULT_COLORS)
        cols.update({k: v for k, v in (data.colors or {}).items() if v})
        self.col = {k: _hex(v, L.DEFAULT_COLORS.get(k, '#000000')) for k, v in cols.items()}
        self.clipped = []

    # ---- primitives (top-down coordinates) ----
    def y(self, top):
        return L.PAGE_H - top

    def rect(self, left, top, right, bottom, color):
        self.c.setFillColor(color)
        self.c.rect(left, self.y(bottom), right - left, bottom - top, stroke=0, fill=1)

    def text(self, s, x, baseline, fname, size, color=None, align='left', max_width=None):
        s = '' if s is None else str(s)
        rl = font(fname)
        if max_width is not None:
            s = self._fit(s, rl, size, max_width)
        self.c.setFont(rl, size)
        self.c.setFillColor(color or self.col['ink'])
        if align == 'right':
            self.c.drawRightString(x, self.y(baseline), s)
        elif align == 'center':
            self.c.drawCentredString(x, self.y(baseline), s)
        else:
            self.c.drawString(x, self.y(baseline), s)

    def _fit(self, s, rl, size, max_width):
        # Never shrink type; if a value is too long for its cell, end with an ellipsis
        # (the web form warns about this before the PDF is produced).
        if stringWidth(s, rl, size) <= max_width:
            return s
        self.clipped.append(s)
        while s and stringWidth(s + '\u2026', rl, size) > max_width:
            s = s[:-1]
        return s.rstrip() + '\u2026'

    # ---- sections ----
    def running_header(self, page):
        h = L.RUN_HEADER
        self.text(h['text'], h['x'], h['baseline'], h['font'], h['size'])
        p = L.PAGE_NUMBER
        self.text(p['fmt'].format(page=page), p['x'], p['baseline'], p['font'], p['size'])

    def border(self):
        b = L.BORDER
        w = b['width']
        col = self.col['border']
        self.rect(b['left'], b['top'], b['right'], b['top'] + w, col)          # top
        self.rect(b['left'], b['bottom'] - w, b['right'], b['bottom'], col)    # bottom
        self.rect(b['left'], b['top'], b['left'] + w, b['bottom'], col)        # left
        self.rect(b['right'] - w, b['top'], b['right'], b['bottom'], col)      # right

    def company(self):
        cfg = L.COMPANY
        n = cfg['name']
        self.text(self.d.business_name, n['x'], n['baseline'], n['font'], n['size'], max_width=430)
        addr = [a for a in self.d.business_address_lines if a.strip()]
        slots = cfg['lines']
        extra = 0
        for i, line in enumerate(addr):
            if i < len(slots):
                s = slots[i]
                self.text(line, cfg['x'], s['baseline'], s['font'], s['size'], max_width=430)
            else:  # more address lines than the sample: push contact rows down
                extra += cfg['extra_line_step']
                s = slots[-1]
                self.text(line, cfg['x'], s['baseline'] + extra, s['font'], s['size'], max_width=430)
        for key, value in (('contact', self.d.phone), ('email', self.d.email), ('gstin', self.d.gstin)):
            if not value:
                continue
            s = cfg[key]
            self.text(s['fmt'].format(phone=value, email=value, gstin=value), s['x'], s['baseline'] + extra,
                      s['font'], s['size'], max_width=430)

    def badge(self):
        b = L.BADGE
        self.rect(b['left'], b['top'], b['right'], b['bottom'], self.col['primary'])
        t = b['title']
        self.text(t['text'], t['x'], t['baseline'], t['font'], t['size'], self.col['on_primary'])
        n = b['number']
        self.text(n['fmt'].format(number=self.d.number), n['x'], n['baseline'], n['font'], n['size'],
                  self.col['on_primary'], max_width=b['right'] - n['x'] - 1)

    def customer(self):
        b = L.BILL_TO
        lab = b['label']
        self.text(lab['text'], lab['x'], lab['baseline'], lab['font'], lab['size'], self.col['label'])
        nm = b['name']
        self.text(self.d.client_name, nm['x'], nm['baseline'], nm['font'], nm['size'], max_width=360)
        lines = [a for a in self.d.client_address_lines if a.strip()][:b['max_lines']]
        for i, line in enumerate(lines):
            last = i == len(lines) - 1
            fname, size = b['last_line_font'] if last else b['line_font']
            base = b['first_line_baseline'] + i * b['line_step'] + (b['last_line_extra'] if last and i else 0)
            self.text(line, b['x'], base, fname, size, max_width=360)

    def dates(self):
        cfg = L.DATES
        lf, ls = cfg['label_font']
        vf, vs = cfg['value_font']
        fmt = cfg['date_format']
        lc = self.col['label']
        self.text('Invoice Date', cfg['invoice_date']['label_x'], cfg['label_baseline'], lf, ls, lc)
        self.text(self.d.invoice_date.strftime(fmt), cfg['invoice_date']['value_x'], cfg['value_baseline'], vf, vs)
        self.text('Terms', cfg['terms']['label_x'], cfg['label_baseline'], lf, ls, lc)
        self.text(str(self.d.terms_days), cfg['terms']['value_center'], cfg['value_baseline'], vf, vs, align='center')
        self.text('Due Date', cfg['due_date']['label_x'], cfg['label_baseline'], lf, ls, lc)
        self.text((self.d.due_date.strftime(fmt) if self.d.due_date else ''), cfg['due_date']['value_x'], cfg['value_baseline'], vf, vs)

    def _edges(self):
        t = L.TABLE
        return [t['left']] + t['separators'] + [t['right']]

    def table_header(self):
        t = L.TABLE
        self.rect(t['left'], t['header_top'], t['right'], t['header_bottom'], self.col['primary'])
        edges = self._edges()
        f, s = t['header_font']
        for i, head in enumerate(t['headers']):
            if i == 0 and self.d.service_type in ('general_manpower', 'recruitment_placement') and head.lower().startswith('consultant'):
                head = 'Recruitment Service' if self.d.service_type == 'recruitment_placement' else 'Resource / Service'
            mode, x = t['header_align'][i]
            if mode == 'center':
                self.text(head, (edges[i] + edges[i + 1]) / 2, t['header_baseline'], f, s, self.col['on_primary'], 'center')
            else:
                self.text(head, x, t['header_baseline'], f, s, self.col['on_primary'])

    def _row_top(self, i):
        t = L.TABLE
        return t['body_top'] + i * t['row_height']

    def table_rows(self, lines, total=None):
        t = L.TABLE
        n = self.d.body_rows
        rh = t['row_height']
        for i in range(n):
            shaded = (i % 2 == 0) == t['first_row_shaded']
            top = self._row_top(i)
            self.rect(t['left'], top, t['right'], top + rh, self.col['alt_row'] if shaded else self.col['white_row'])
        # total row band
        tt = self._row_top(n)
        self.rect(t['left'], tt, t['right'], tt + t['total_row_height'],
                  self.col['alt_row'] if t['total_row_shaded'] else self.col['white_row'])
        edges = self._edges()
        pad = t['text_pad_left']
        bf, bs = t['body_font']
        nf, ns = t['number_font']
        for i, ln in enumerate(lines[:n]):
            base = self._row_top(i) + t['baseline_offset']
            self.text(ln.project_name, edges[0] + pad, base, bf, bs, max_width=edges[1] - edges[0] - 2 * pad)
            self.text(ln.description, edges[1] + pad + 0.36, base, bf, bs, max_width=edges[2] - edges[1] - 2 * pad)
            vals = [f'{Decimal(ln.quantity):.2f}', '' if ln.unit_price is None else format_western(ln.unit_price), format_western(ln.amount)]
            for j, v in enumerate(vals):
                self.text(v, t['num_right'][j], base, nf, ns, align='right',
                          max_width=edges[3 + j] - edges[2 + j] - 2)
        if total is not None:
            tf, ts = t['total_font']
            self.text(format_western(total), t['num_right'][2], tt + 6.60, tf, ts, align='right')

    def table_separators(self):
        t = L.TABLE
        w = t['rule_width']
        rule = self.col['rule']
        n = self.d.body_rows
        top = t['top']
        total_top = self._row_top(n)
        bottom = total_top + t['total_row_height']
        hw = w / 2
        # horizontal rules: top of header, top & bottom of the total row
        for yy in (top, total_top, bottom):
            self.rect(t['left'], yy - hw, t['right'], yy + hw, rule)
        for x in t['separators']:
            self.rect(x - hw, top + 0.24, x + hw, bottom + 0.24, rule)

    def total_strip(self):
        s = L.TOTAL_STRIP
        self.rect(s['left'], s['top'], s['right'], s['bottom'], self.col['primary'])
        hw = L.TABLE['rule_width'] / 2
        self.rect(s['separator_x'] - hw, s['top'], s['separator_x'] + hw, s['bottom'], self.col['rule'])
        lab = s['label']
        self.text(lab['text'], lab['x'], lab['baseline'], lab['font'], lab['size'], self.col['on_primary'])
        a = s['amount']
        self.text(format_indian(self.d.total), a['right'], a['baseline'], a['font'], a['size'],
                  self.col['on_primary'], 'right', max_width=s['right'] - s['separator_x'] - 2)
        cur = s['currency']
        self.text(self.d.currency, cur['right'], cur['baseline'], cur['font'], cur['size'], align='right')

    def payment_details(self):
        p = L.PAYMENT
        t = p['title']
        self.text(t['text'], t['x'], t['baseline'], t['font'], t['size'])
        f, s = p['font']
        for baseline, fmt in p['rows']:
            self.text(fmt.format(**self.d.bank), p['x'], baseline, f, s, max_width=300)

    def signature(self):
        sg = L.SIGNATURE
        left, top, right, bottom = sg['image_box']
        path = self.d.signature_path
        if path and Path(path).is_file():
            try:
                img = ImageReader(str(path))
                iw, ih = img.getSize()
                bw, bh = right - left, bottom - top
                scale = min(bw / iw, bh / ih)
                w, h = iw * scale, ih * scale
                x = left + (bw - w) / 2
                y_top = bottom - h
                self.c.drawImage(img, x, self.y(y_top + h), w, h, mask='auto')
            except Exception:
                pass
        n = sg['name']
        if self.d.signature_name:
            self.text(self.d.signature_name, n['center'], n['baseline'], n['font'], n['size'], align='center')

    def footer(self):
        f = L.FOOTER
        if self.d.footer_text:
            self.text(self.d.footer_text, f['center'], f['baseline'], f['font'], f['size'], align='center')

    def lut_line(self):
        cfg = L.LUT_LINE
        self.text(self.d.lut_note, cfg['x'], cfg['baseline'], cfg['font'], cfg['size'], max_width=cfg['max_width'])

    def page(self, page_no, lines, last):
        self.running_header(page_no)
        self.border()
        self.company()
        self.badge()
        self.customer()
        self.dates()
        self.table_rows(lines, self.d.total if last else None)
        self.table_header()
        self.table_separators()
        if last:
            self.total_strip()
            self.payment_details()
            self.signature()
            if self.d.lut_note:
                self.lut_line()
        self.footer()


def wrap_words(text, rl_font, size, width, max_lines):
    words, lines, cur = (text or '').split(), [], ''
    for w in words:
        trial = f'{cur} {w}'.strip()
        if stringWidth(trial, rl_font, size) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [' '.join(lines[max_lines - 1:])]
    return lines


class TaxInvoiceCanvas(InvoiceCanvas):
    """GST 'Tax Invoice' (Domestic GST). Reuses every export-layout section, styles and the 5-column
    table; adds the customer GSTIN block, a SAC row under each line, a tax summary under the table and the
    amount in words, moving payment details / signature / border / footer down to make room."""
    T = L.TAX_INVOICE

    def border(self):
        b = L.BORDER
        w, col = b['width'], self.col['border']
        bottom = b['bottom'] + self.T['border_dy']
        self.rect(b['left'], b['top'], b['right'], b['top'] + w, col)
        self.rect(b['left'], bottom - w, b['right'], bottom, col)
        self.rect(b['left'], b['top'], b['left'] + w, bottom, col)
        self.rect(b['right'] - w, b['top'], b['right'], bottom, col)

    def badge(self):
        b = L.BADGE
        t = b['title']
        width = stringWidth(self.T['title'], font(t['font']), t['size'])
        left = min(b['left'], b['right'] - width - 2 * self.T['badge_pad'])
        self.rect(left, b['top'], b['right'], b['bottom'], self.col['primary'])
        self.text(self.T['title'], left + self.T['badge_pad'], t['baseline'], t['font'], t['size'], self.col['on_primary'])
        n = b['number']
        nx = left + self.T['number_inset']
        self.text(n['fmt'].format(number=self.d.number), nx, n['baseline'], n['font'], n['size'],
                  self.col['on_primary'], max_width=b['right'] - nx - 1)

    def customer(self):
        b = L.BILL_TO
        mw = self.T['customer_name_max_width']
        lab = b['label']
        self.text(lab['text'], lab['x'], lab['baseline'], lab['font'], lab['size'], self.col['label'])
        nm = b['name']
        self.text(self.d.client_name, nm['x'], nm['baseline'], nm['font'], nm['size'], max_width=mw)
        lines = [a for a in self.d.client_address_lines if a.strip()][:b['max_lines']]
        for i, line in enumerate(lines):
            last = i == len(lines) - 1
            fname, size = b['last_line_font'] if last else b['line_font']
            base = b['first_line_baseline'] + i * b['line_step'] + (b['last_line_extra'] if last and i else 0)
            self.text(line, b['x'], base, fname, size, max_width=mw)
        cb = self.T['customer_block']
        rows = [('Customer GSTIN: ', self.d.customer_gstin or 'Unregistered'),
                ('State: ', self.d.customer_state), ('Place of Supply: ', self.d.place_of_supply)]
        for (label, value), baseline in zip(rows, cb['baselines']):
            lw = stringWidth(label, font(cb['label_font']), cb['size'])
            self.text(label, cb['x'], baseline, cb['label_font'], cb['size'], self.col['label'])
            self.text(value, cb['x'] + lw, baseline, cb['font'], cb['size'], max_width=cb['max_width'] - lw)

    def table_rows(self, lines, total=None):
        # Draw the bands/total exactly like the export layout, then the lines two rows apiece.
        super().table_rows([], total)
        t = L.TABLE
        edges = self._edges()
        pad = t['text_pad_left']
        bf, bs = t['body_font']
        nf, ns = t['number_font']
        sf, ss = self.T['sac_font']
        per = self.T['rows_per_line']
        for k, ln in enumerate(lines[:self.d.body_rows // per]):
            base = self._row_top(k * per) + t['baseline_offset']
            self.text(ln.project_name, edges[0] + pad, base, bf, bs, max_width=edges[1] - edges[0] - 2 * pad)
            self.text(ln.description, edges[1] + pad + 0.36, base, bf, bs, max_width=edges[2] - edges[1] - 2 * pad)
            vals = [f'{Decimal(ln.quantity):.2f}', '' if ln.unit_price is None else format_western(ln.unit_price),
                    format_western(ln.amount)]
            for j, v in enumerate(vals):
                self.text(v, t['num_right'][j], base, nf, ns, align='right', max_width=edges[3 + j] - edges[2 + j] - 2)
            if ln.sac_code:
                self.text(self.T['sac_fmt'].format(sac=ln.sac_code), edges[1] + pad + 0.36,
                          self._row_top(k * per + 1) + t['baseline_offset'], sf, ss,
                          max_width=edges[2] - edges[1] - 2 * pad)

    def total_strip(self):
        s = self.T['summary']
        f, fs = s['font']
        bf, bs = s['bold_font']
        baseline = s['first_baseline']
        rows = list(self.d.tax_rows)
        for i, (label, amount) in enumerate(rows):
            bold = i == len(rows) - 1  # Total Tax
            self.text(label, s['label_x'], baseline, bf if bold else f, bs if bold else fs, max_width=66)
            self.text(format_indian(amount), s['amount_right'], baseline, bf if bold else f, bs if bold else fs,
                      align='right', max_width=56)
            baseline += s['step']
        g = self.T['grand']
        top = baseline - s['step'] + g['gap']
        bottom = top + g['height']
        self.rect(g['left'], top, g['right'], bottom, self.col['primary'])
        hw = L.TABLE['rule_width'] / 2
        self.rect(g['separator_x'] - hw, top, g['separator_x'] + hw, bottom, self.col['rule'])
        lf, ls = g['label_font']
        self.text(g['label'], s['label_x'] + 0.12, bottom - 1.42, lf, ls, self.col['on_primary'], max_width=66)
        af, asz = g['amount_font']
        self.text(format_indian(self.d.grand_total), s['amount_right'], bottom - 1.63, af, asz, self.col['on_primary'],
                  'right', max_width=g['right'] - g['separator_x'] - 2)
        cf, cs = g['currency_font']
        self.text(self.d.currency, g['separator_x'] - 2.0, bottom - 1.63, cf, cs, self.col['on_primary'], 'right')
        self._summary_bottom = bottom

    def amount_in_words(self):
        w = self.T['words']
        text = w['label'] + (self.d.amount_words or '')
        for i, line in enumerate(wrap_words(text, font(w['font']), w['size'], w['max_width'], w['max_lines'])):
            self.text(line, w['x'], w['first_baseline'] + i * w['step'], w['font'], w['size'], max_width=w['max_width'])

    def payment_details(self):
        p = L.PAYMENT
        dy = self.T['payment_dy']
        t = p['title']
        self.text(t['text'], t['x'], t['baseline'] + dy, t['font'], t['size'])
        f, s = p['font']
        for baseline, fmt in p['rows']:
            self.text(fmt.format(**self.d.bank), p['x'], baseline + dy, f, s, max_width=300)

    def signature(self):
        sg = L.SIGNATURE
        dy = self.T['signature_dy']
        left, top, right, bottom = sg['image_box']
        top, bottom = top + dy, bottom + dy
        path = self.d.signature_path
        if path and Path(path).is_file():
            try:
                img = ImageReader(str(path))
                iw, ih = img.getSize()
                bw, bh = right - left, bottom - top
                scale = min(bw / iw, bh / ih)
                w, h = iw * scale, ih * scale
                x = left + (bw - w) / 2
                self.c.drawImage(img, x, self.y(bottom), w, h, mask='auto')
            except Exception:
                pass
        n = sg['name']
        if self.d.signature_name:
            self.text(self.d.signature_name, n['center'], n['baseline'] + dy, n['font'], n['size'], align='center')

    def footer(self):
        f = L.FOOTER
        if self.d.footer_text:
            self.text(self.d.footer_text, f['center'], f['baseline'] + self.T['footer_dy'], f['font'], f['size'],
                      align='center')

    def page(self, page_no, lines, last):
        super().page(page_no, lines, last)  # sections above are overridden; same order as the export layout
        if last:
            self.amount_in_words()


def render_invoice_pdf(data: InvoiceData) -> bytes:
    """Render one or more US Letter pages. Up to ``body_rows`` lines fit on one page;
    extra lines continue on further pages with the identical layout. The total,
    total strip, payment details and signature appear on the last page only."""
    buf = BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(L.PAGE_W, L.PAGE_H))
    c.setTitle(f'{"Tax Invoice" if data.tax_invoice else "Invoice"} {data.number}')
    c.setAuthor(data.business_name)
    per_page = max(1, data.body_rows // L.TAX_INVOICE['rows_per_line'] if data.tax_invoice else data.body_rows)
    lines = list(data.lines)
    chunks = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]
    ic = (TaxInvoiceCanvas if data.tax_invoice else InvoiceCanvas)(c, data)
    for idx, chunk in enumerate(chunks):
        ic.page(idx + 1, chunk, idx == len(chunks) - 1)
        c.showPage()
    c.save()
    return buf.getvalue()
