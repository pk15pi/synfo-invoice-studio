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
    unit_price: Decimal
    amount: Decimal


@dataclass
class InvoiceData:
    number: str
    invoice_date: date
    terms_days: int
    due_date: date
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
        self.text(self.d.due_date.strftime(fmt), cfg['due_date']['value_x'], cfg['value_baseline'], vf, vs)

    def _edges(self):
        t = L.TABLE
        return [t['left']] + t['separators'] + [t['right']]

    def table_header(self):
        t = L.TABLE
        self.rect(t['left'], t['header_top'], t['right'], t['header_bottom'], self.col['primary'])
        edges = self._edges()
        f, s = t['header_font']
        for i, head in enumerate(t['headers']):
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
            vals = [f'{Decimal(ln.quantity):.2f}', format_western(ln.unit_price), format_western(ln.amount)]
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
        self.footer()


def render_invoice_pdf(data: InvoiceData) -> bytes:
    """Render one or more US Letter pages. Up to ``body_rows`` lines fit on one page;
    extra lines continue on further pages with the identical layout. The total,
    total strip, payment details and signature appear on the last page only."""
    buf = BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(L.PAGE_W, L.PAGE_H))
    c.setTitle(f'Invoice {data.number}')
    c.setAuthor(data.business_name)
    per_page = max(1, data.body_rows)
    lines = list(data.lines)
    chunks = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]
    ic = InvoiceCanvas(c, data)
    for idx, chunk in enumerate(chunks):
        ic.page(idx + 1, chunk, idx == len(chunks) - 1)
        c.showPage()
    c.save()
    return buf.getvalue()
