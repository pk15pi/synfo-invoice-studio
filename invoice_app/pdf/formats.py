"""Built-in invoice format engines (Stage 6).

The 'standard' engine is NOT here: it is the original coordinate layout in render.py and is called
unchanged, so its bytes never move. Every engine below is a flow layout on a ReportLab canvas that
shares the helpers in ``BaseEngine`` and prints only the already-saved figures carried by
``InvoiceData`` (lines, total, tax rows, grand total, amount in words). Nothing is recomputed.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4, landscape, letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas

from ..billing import format_indian, format_western
from .fonts import font
from .render import wrap_words

DEFAULT_HEADERS = ['Consultant - Project Name', 'Memo/Description', 'Qty', 'Unit Price', 'Amount']
COLUMN_KEYS = ('col_project_label', 'col_description_label', 'col_qty_label', 'col_unit_price_label',
               'col_amount_label')
LOGO = Path(__file__).resolve().parent.parent / 'assets' / 'logo.png'
DATE_FMT = '%d %b %Y'


@dataclass
class FormatExtras:
    """Saved, non-amount invoice data some formats print."""
    po_number: str = ''
    vendor_code: str = ''
    billing_month: str = ''          # e.g. 'October 2026'
    line_periods: list = field(default_factory=list)   # [(from_date, to_date)] parallel to data.lines
    status: str = ''


def page_size(snap):
    size = letter if (snap.get('page_size') or 'A4') == 'LETTER' else A4
    return landscape(size) if snap.get('orientation') == 'landscape' else size


def _hex(value, default='#0B5394'):
    try:
        return HexColor(value or default)
    except Exception:
        return HexColor(default)


def tint(color, t):
    """Mix a colour toward white (t=0 -> colour, t=1 -> white)."""
    return HexColor('#%02X%02X%02X' % tuple(int(round((c + (1 - c) * t) * 255)) for c in
                                            (color.red, color.green, color.blue)))


def shade(color, t):
    return HexColor('#%02X%02X%02X' % tuple(int(round(c * (1 - t) * 255)) for c in
                                            (color.red, color.green, color.blue)))


def fmt_date(d):
    return d.strftime(DATE_FMT) if d else ''


def period_text(start, end):
    if start and end:
        if start.year == end.year:
            return f'{start.strftime("%d %b")} - {end.strftime(DATE_FMT)}'
        return f'{fmt_date(start)} - {fmt_date(end)}'
    return fmt_date(start or end)


class BaseEngine:
    margin = 42
    size = 8.4            # body text
    small = 7.0
    title_size = 24
    row_pad = 4.2
    leading = 1.28
    max_wrap = 4
    regular, bold = 'Arial', 'Arial-Bold'
    zebra = False
    grid = False
    header_fill = True
    show_line_periods = False
    col_fracs = (0.25, 0.37, 0.09, 0.13, 0.16)

    def __init__(self, c, data, extras, snap):
        self.c, self.d, self.x, self.s = c, data, extras or FormatExtras(), snap
        self.W, self.H = c._pagesize
        self.L, self.R = self.margin, self.W - self.margin
        self.accent = _hex(snap.get('accent_color'))
        self.soft = tint(self.accent, 0.9)
        self.ink = HexColor('#1F2933')
        self.muted = HexColor('#5F6B7A')
        self.rule = HexColor('#C9CED6')
        self.page_no = 1
        self.y = self.margin
        self.bottom = self.H - self.margin - 22

    # ---------- primitives (top-down y) ----------
    def Y(self, top):
        return self.H - top

    def f(self, bold=False):
        return font(self.bold if bold else self.regular)

    def text(self, s, x, baseline, size=None, bold=False, color=None, align='left', max_width=None):
        s = '' if s is None else str(s)
        size = size or self.size
        rl = self.f(bold)
        if max_width is not None and stringWidth(s, rl, size) > max_width:
            while s and stringWidth(s + '\u2026', rl, size) > max_width:
                s = s[:-1]
            s = s.rstrip() + '\u2026'
        self.c.setFont(rl, size)
        self.c.setFillColor(color or self.ink)
        y = self.Y(baseline)
        if align == 'right':
            self.c.drawRightString(x, y, s)
        elif align == 'center':
            self.c.drawCentredString(x, y, s)
        else:
            self.c.drawString(x, y, s)

    def rect(self, left, top, right, bottom, fill=None, stroke=None, width=0.6):
        if fill is not None:
            self.c.setFillColor(fill)
        if stroke is not None:
            self.c.setStrokeColor(stroke)
            self.c.setLineWidth(width)
        self.c.rect(left, self.Y(bottom), right - left, bottom - top, stroke=1 if stroke is not None else 0,
                    fill=1 if fill is not None else 0)

    def hline(self, x1, x2, y, color=None, width=0.6):
        self.c.setStrokeColor(color or self.rule)
        self.c.setLineWidth(width)
        self.c.line(x1, self.Y(y), x2, self.Y(y))

    def vline(self, x, y1, y2, color=None, width=0.6):
        self.c.setStrokeColor(color or self.rule)
        self.c.setLineWidth(width)
        self.c.line(x, self.Y(y1), x, self.Y(y2))

    def wrap(self, s, width, size=None, bold=False, max_lines=None):
        out = []
        for para in str(s or '').splitlines() or ['']:
            out += wrap_words(para, self.f(bold), size or self.size, width, max_lines or self.max_wrap) or ['']
        return out[:max_lines] if max_lines else out

    def lh(self, size=None):
        return (size or self.size) * self.leading

    def paragraph(self, s, x, top, width, size=None, bold=False, color=None, max_lines=12):
        """Draw wrapped text starting at ``top``; returns the new top."""
        size = size or self.size
        for line in self.wrap(s, width, size, bold, max_lines):
            top += self.lh(size)
            self.text(line, x, top - size * 0.25, size, bold, color, max_width=width)
        return top

    def money(self, v):
        return format_indian(v) if (self.d.currency or 'INR') == 'INR' else format_western(v)

    # ---------- content helpers ----------
    @property
    def title(self):
        return (self.s.get('title_text') or '').strip() or ('TAX INVOICE' if self.d.tax_invoice else 'INVOICE')

    @property
    def headers(self):
        headers = [(self.s.get(k) or '').strip() or DEFAULT_HEADERS[i] for i, k in enumerate(COLUMN_KEYS)]
        if self.d.service_type in ('general_manpower', 'recruitment_placement') and headers[0].lower().startswith('consultant'):
            headers[0] = 'Recruitment Service' if self.d.service_type == 'recruitment_placement' else 'Resource / Service'
        return headers

    def edges(self, left=None, right=None):
        left, right = left or self.L, right or self.R
        xs, acc = [left], left
        for frac in self.col_fracs:
            acc += (right - left) * frac
            xs.append(acc)
        xs[-1] = right
        return xs

    def meta_pairs(self):
        d = self.d
        pairs = [('Invoice No.', d.number), ('Invoice Date', fmt_date(d.invoice_date)),
                 ('Terms', str(d.terms_days if d.terms_days is not None else '')), ('Due Date', fmt_date(d.due_date))]
        if d.service_type == 'general_manpower':
            pairs.append(('Service', 'General Manpower Supply'))
            if d.billing_period_start or d.billing_period_end:
                period = period_text(d.billing_period_start, d.billing_period_end)
                pairs.append(('Billing Period', period))
        elif d.service_type == 'recruitment_placement':
            pairs.append(('Service', 'Recruitment and Candidate Placement'))
            if d.recruitment_agreement_reference:
                pairs.append(('Agreement Reference', d.recruitment_agreement_reference))
        if self.s.get('show_po_number', True) and self.x.po_number:
            pairs.append(('PO Number', self.x.po_number))
        if self.s.get('show_vendor_code', True) and self.x.vendor_code:
            pairs.append(('Vendor Code', self.x.vendor_code))
        return pairs

    def company_lines(self):
        d = self.d
        rows = [a for a in d.business_address_lines if a.strip()]
        if d.phone:
            rows.append(f'Phone: {d.phone}')
        if d.email:
            rows.append(f'Email: {d.email}')
        if d.gstin:
            rows.append(f'GSTIN: {d.gstin}')
        return rows

    def customer_rows(self):
        d = self.d
        rows = [a for a in d.client_address_lines if a.strip()]
        gst_rows = []
        if d.tax_invoice:
            gst_rows = [('GSTIN', d.customer_gstin or 'Unregistered'), ('State', d.customer_state),
                        ('Place of Supply', d.place_of_supply)]
        return rows, gst_rows

    def totals_rows(self):
        d = self.d
        if d.tax_invoice:
            return list(d.tax_rows), ('Grand Total', d.grand_total)
        return [], ('Total', d.total)

    def words(self):
        if self.d.amount_words:
            return self.d.amount_words
        if self.s.get('show_amount_in_words', True) and (self.d.currency or 'INR') == 'INR':
            from ..gst import amount_in_words
            return amount_in_words(self.d.total)
        return ''

    def logo(self, x, top, size, color=None):
        """Logo image if invoice_app/assets/logo.png exists, else an initials monogram. Returns width used."""
        if not self.s.get('show_logo', True):
            return 0
        if LOGO.is_file():
            try:
                img = ImageReader(str(LOGO))
                iw, ih = img.getSize()
                w = size * iw / ih
                self.c.drawImage(img, x, self.Y(top + size), w, size, mask='auto')
                return w + 8
            except Exception:
                pass
        initials = ''.join(w[0] for w in (self.d.business_name or 'Q').split()[:2]).upper()
        self.rect(x, top, x + size, top + size, fill=color or self.accent)
        self.text(initials, x + size / 2, top + size * 0.64, size * 0.42, True, white, 'center')
        return size + 8

    # ---------- pagination ----------
    def page_footer(self):
        y = self.H - self.margin + 6
        self.hline(self.L, self.R, y - 12, self.rule, 0.4)
        if self.d.footer_text:
            self.text(self.d.footer_text, (self.L + self.R) / 2, y, self.small, color=self.muted, align='center')
        self.text(f'Page {self.page_no}', self.R, y, self.small, color=self.muted, align='right')
        self.text(f'{self.title} #{self.d.number}', self.L, y, self.small, color=self.muted,
                  max_width=(self.R - self.L) / 3)

    def new_page(self):
        self.page_footer()
        self.c.showPage()
        self.page_no += 1
        self.y = self.margin
        self.text(f'{self.d.business_name}  \u00b7  {self.title} #{self.d.number} (continued)', self.L, self.y + 8,
                  self.small, True, self.muted)
        self.y += 20

    def ensure(self, h):
        if self.y + h > self.bottom:
            self.new_page()
            return True
        return False

    # ---------- table ----------
    def row_cells(self, i, ln):
        e = self.edges()
        pad = self.row_pad
        w0, w1 = e[1] - e[0] - 2 * pad, e[2] - e[1] - 2 * pad
        c0 = [(t, self.size, False, None) for t in self.wrap(ln.project_name, w0)]
        c1 = [(t, self.size, False, None) for t in self.wrap(ln.description, w1)]
        if self.show_line_periods and i < len(self.x.line_periods):
            per = period_text(*self.x.line_periods[i])
            if per:
                c1.append((f'Period: {per}', self.small, True, self.accent))
        if self.d.tax_invoice and ln.sac_code:
            c1.append((f'SAC: {ln.sac_code}', self.small, False, self.muted))
        nums = [f'{Decimal(ln.quantity):.2f}', '' if ln.unit_price is None else format_western(ln.unit_price),
                format_western(ln.amount)]
        return [c0, c1] + [[(n, self.size, False, None)] for n in nums]

    def cell_height(self, cells):
        return max(sum(self.lh(sz) for _, sz, _, _ in col) for col in cells) + 2 * self.row_pad

    def table_header(self):
        e = self.edges()
        h = self.lh() + 2 * self.row_pad + 1
        top = self.y
        if self.header_fill:
            self.rect(self.L, top, self.R, top + h, fill=self.header_bg())
        color = white if self.header_fill else self.accent
        for j, head in enumerate(self.headers):
            base = top + h - self.row_pad - 1.8
            if j >= 2:
                self.text(head, e[j + 1] - self.row_pad, base, self.size, True, color, 'right',
                          max_width=e[j + 1] - e[j] - 2 * self.row_pad)
            else:
                self.text(head, e[j] + self.row_pad, base, self.size, True, color,
                          max_width=e[j + 1] - e[j] - 2 * self.row_pad)
        if self.grid:
            self.rect(self.L, top, self.R, top + h, stroke=self.grid_color(), width=0.7)
        if not self.header_fill:
            self.hline(self.L, self.R, top + h, self.accent, 1.0)
        self.y = top + h

    def header_bg(self):
        return self.accent

    def grid_color(self):
        return shade(self.accent, 0.2)

    def draw_row(self, i, cells, h):
        e = self.edges()
        top = self.y
        if self.zebra and i % 2 == 1:
            self.rect(self.L, top, self.R, top + h, fill=self.soft)
        for j, col in enumerate(cells):
            t = top + self.row_pad
            for txt, sz, b, colr in col:
                t += self.lh(sz)
                if j >= 2:
                    self.text(txt, e[j + 1] - self.row_pad, t - sz * 0.28, sz, b, colr, 'right',
                              max_width=e[j + 1] - e[j] - 2 * self.row_pad)
                else:
                    self.text(txt, e[j] + self.row_pad, t - sz * 0.28, sz, b, colr,
                              max_width=e[j + 1] - e[j] - 2 * self.row_pad)
        if self.grid:
            self.rect(self.L, top, self.R, top + h, stroke=self.grid_color(), width=0.5)
            for x in e[1:-1]:
                self.vline(x, top, top + h, self.grid_color(), 0.5)
        else:
            self.hline(self.L, self.R, top + h, self.rule, 0.4)
        self.y = top + h

    def table(self):
        self.table_header()
        for i, ln in enumerate(self.d.lines):
            cells = self.row_cells(i, ln)
            h = self.cell_height(cells)
            if self.ensure(h):
                self.table_header()
            self.draw_row(i, cells, h)

    # ---------- totals and closing blocks ----------
    def totals_height(self):
        rows, _ = self.totals_rows()
        return len(rows) * self.lh() + self.lh(self.size + 4) + 26

    def totals(self, left, top, right):
        """Right-hand totals; returns the bottom y."""
        rows, (glabel, gvalue) = self.totals_rows()
        y = top
        for label, amount in rows:
            y += self.lh() + 1
            bold = label == 'Total Tax'
            self.text(label, left, y, self.size, bold, self.muted if not bold else None, max_width=right - left - 70)
            self.text(self.money(amount), right, y, self.size, bold, align='right')
        y += 6
        if rows:
            self.hline(left, right, y, self.rule, 0.6)
        return self.grand_total(left, y, right, glabel, gvalue)

    def grand_total(self, left, top, right, label, value):
        h = self.lh(self.size + 3) + 8
        self.rect(left - 6, top + 2, right + 4, top + 2 + h, fill=self.accent)
        self.text(f'{label} ({self.d.currency})', left, top + h - 1, self.size + 1, True, white)
        self.text(self.money(value), right, top + h - 1, self.size + 3, True, white, 'right')
        return top + h + 6

    def words_block(self, x, top, width):
        w = self.words()
        if not w:
            return top
        top = self.paragraph('Amount in words', x, top, width, self.small, True, self.muted, 1)
        return self.paragraph(w, x, top, width, self.size, False, None, 3) + 4

    def bank_block(self, x, top, width):
        if not self.s.get('show_bank_details', True):
            return top
        b = self.d.bank or {}
        rows = [('Bank', b.get('bank_name')), ('Account Holder', b.get('account_holder')),
                ('Account No.', b.get('account_number')), ('IFSC', b.get('ifsc')), ('Country', b.get('bank_country'))]
        rows = [(k, v) for k, v in rows if v]
        if not rows:
            return top
        top = self.paragraph('Payment Details', x, top, width, self.size, True, self.accent, 1) + 1
        for k, v in rows:
            top += self.lh(self.small) + 0.6
            self.text(f'{k}:', x, top, self.small, color=self.muted)
            self.text(v, x + 64, top, self.small, max_width=width - 64)
        return top + 6

    def closing(self):
        """Totals (right) + words and bank details (left), then notes, declaration, LUT note, signature."""
        tw = min(230, (self.R - self.L) * 0.45)
        need = max(self.totals_height(), 90 if self.s.get('show_bank_details', True) else 40)
        self.ensure(need + 10)
        top = self.y + 10
        bottom_r = self.totals(self.R - tw + 6, top, self.R - 4)
        lw = self.R - self.L - tw - 20
        bottom_l = self.words_block(self.L, top, lw)
        bottom_l = self.bank_block(self.L, bottom_l + 2, lw)
        self.y = max(bottom_r, bottom_l) + 6
        self.notes_blocks()
        self.signature()

    def notes_blocks(self):
        width = self.R - self.L
        if self.d.lut_note:
            self.ensure(self.lh() + 8)
            self.y = self.paragraph(self.d.lut_note, self.L, self.y, width, self.size, True, self.ink, 2) + 4
        for label, key in (('Notes / Terms', 'notes_text'), ('Declaration', 'declaration_text')):
            text = (self.s.get(key) or '').strip()
            if text:
                lines = self.wrap(text, width, self.small, max_lines=10)
                self.ensure(self.lh(self.small) * (len(lines) + 1) + 6)
                self.y = self.paragraph(label, self.L, self.y, width, self.small, True, self.muted, 1)
                self.y = self.paragraph(text, self.L, self.y, width, self.small, False, self.ink, 10) + 4

    def signature(self):
        if not self.s.get('show_signature', True):
            return
        self.ensure(74)
        right = self.R
        left = right - 170
        top = self.y + 6
        self.text(f'For {self.d.business_name}', right, top + 8, self.small, True, align='right', max_width=170)
        path = self.d.signature_path
        if path and Path(path).is_file():
            try:
                img = ImageReader(str(path))
                iw, ih = img.getSize()
                bw, bh = 120, 36
                sc = min(bw / iw, bh / ih)
                w, h = iw * sc, ih * sc
                self.c.drawImage(img, right - w, self.Y(top + 14 + h), w, h, mask='auto')
            except Exception:
                pass
        self.hline(left, right, top + 54, self.ink, 0.5)
        self.text(self.d.signature_name or 'Authorised Signatory', right, top + 63, self.small, align='right',
                  max_width=170)
        self.text('Authorised Signatory', right, top + 71, self.small - 0.5, color=self.muted, align='right')
        self.y = top + 76

    # ---------- page structure (engines override header / parties) ----------
    def render(self):
        self.header()
        self.parties()
        self.y += 10
        self.table()
        self.closing()
        self.page_footer()
        self.c.showPage()

    def party(self, x, top, width, label, name, rows, gst_rows=(), label_color=None):
        top = self.paragraph(label, x, top, width, self.small, True, label_color or self.accent, 1)
        top = self.paragraph(name, x, top, width, self.size + 1, True, None, 2)
        for r in rows:
            top = self.paragraph(r, x, top, width, self.size, False, None, 2)
        for k, v in gst_rows:
            top += self.lh()
            lw = stringWidth(f'{k}: ', self.f(True), self.size)
            self.text(f'{k}: ', x, top - 2, self.size, True, self.muted)
            self.text(v, x + lw, top - 2, self.size, max_width=width - lw)
        return top

    def meta_grid(self, x, top, width, cols=3, boxed=False):
        pairs = self.meta_pairs()
        cw = width / cols
        rh = self.lh(self.small) + self.lh() + 8
        for i, (k, v) in enumerate(pairs):
            cx = x + (i % cols) * cw
            cy = top + (i // cols) * rh
            if boxed:
                self.rect(cx, cy, cx + cw, cy + rh, stroke=self.grid_color(), width=0.5)
            self.text(k.upper(), cx + 5, cy + self.lh(self.small) + 1, self.small - 0.4, True, self.muted,
                      max_width=cw - 10)
            self.text(v, cx + 5, cy + rh - 5, self.size, True, max_width=cw - 10)
        return top + ((len(pairs) + cols - 1) // cols) * rh


class ModernMinimal(BaseEngine):
    """Clean white page, a thin accent rule, open table, big accent total."""
    header_fill = False

    def header(self):
        top = self.margin
        lw = self.logo(self.L, top, 34)
        self.text(self.d.business_name, self.L + lw, top + 14, 14, True, max_width=300)
        y = top + 16
        for r in self.company_lines():
            y += self.lh(self.small)
            self.text(r, self.L + lw, y, self.small, color=self.muted, max_width=300)
        self.text(self.title, self.R, top + 22, self.title_size, False, self.accent, 'right')
        self.text(f'# {self.d.number}', self.R, top + 38, self.size + 2, True, align='right')
        self.y = max(y, top + 44) + 10
        self.hline(self.L, self.R, self.y, self.accent, 1.2)
        self.y += 6

    def parties(self):
        half = (self.R - self.L) / 2
        rows, gst_rows = self.customer_rows()
        b1 = self.party(self.L, self.y, half - 20, 'BILL TO', self.d.client_name, rows, gst_rows)
        b2 = self.meta_grid(self.L + half, self.y + 4, half, cols=2)
        self.y = max(b1, b2) + 4

    def grand_total(self, left, top, right, label, value):
        top += 6
        self.text(f'{label.upper()} ({self.d.currency})', left, top + 10, self.small, True, self.muted)
        self.text(self.money(value), right, top + 30, 20, True, self.accent, 'right')
        self.hline(left, right, top + 38, self.accent, 1.2)
        return top + 44

    def totals_height(self):
        return super().totals_height() + 22


class CorporateClassic(BaseEngine):
    """Boxed header, bill-to / ship-to grids, fully bordered table and totals box."""
    grid = True
    regular, bold = 'Trebuchet', 'Trebuchet-Bold'

    def header(self):
        top = self.margin
        h = 78
        self.rect(self.L, top, self.R, top + h, stroke=self.grid_color(), width=1.0)
        tw = 170
        self.rect(self.R - tw, top, self.R, top + 30, fill=self.accent)
        self.text(self.title, self.R - tw / 2, top + 20, 15, True, white, 'center')
        self.text(f'No. {self.d.number}', self.R - tw / 2, top + 46, self.size + 2, True, align='center')
        self.text(f'Date {fmt_date(self.d.invoice_date)}', self.R - tw / 2, top + 60, self.size, color=self.muted,
                  align='center')
        self.vline(self.R - tw, top, top + h, self.grid_color(), 1.0)
        lw = self.logo(self.L + 8, top + 8, 30)
        self.text(self.d.business_name, self.L + 8 + lw, top + 20, 13, True, max_width=self.R - tw - self.L - lw - 20)
        y = top + 22
        for r in self.company_lines()[:6]:
            y += self.lh(self.small)
            self.text(r, self.L + 8 + lw, y, self.small, color=self.muted, max_width=self.R - tw - self.L - lw - 20)
        self.y = max(top + h, y + 6) + 8

    def parties(self):
        half = (self.R - self.L) / 2
        rows, gst_rows = self.customer_rows()
        top = self.y
        bar = self.lh(self.small) + 5
        for i, label in enumerate(('BILL TO', 'SHIP TO / SERVICE RECIPIENT')):
            x = self.L + i * half
            self.rect(x, top, x + half, top + bar, fill=self.soft)
            self.text(label, x + 6, top + bar - 3.5, self.small, True, shade(self.accent, 0.1))
        b1 = self.party(self.L + 6, top + bar, half - 12, '', self.d.client_name, rows, gst_rows)
        ship = list(rows)
        if self.d.tax_invoice and self.d.place_of_supply:
            ship.append(f'Place of Supply: {self.d.place_of_supply}')
        b2 = self.party(self.L + half + 6, top + bar, half - 12, '', self.d.client_name, ship)
        bottom = max(b1, b2) + 6
        self.rect(self.L, top, self.L + half, bottom, stroke=self.grid_color(), width=0.7)
        self.rect(self.L + half, top, self.R, bottom, stroke=self.grid_color(), width=0.7)
        self.y = self.meta_grid(self.L, bottom + 8, self.R - self.L, cols=len(self.meta_pairs()) if
                                len(self.meta_pairs()) <= 6 else 4, boxed=True)

    def totals(self, left, top, right):
        bottom = super().totals(left, top, right)
        self.rect(left - 6, top, right + 4, bottom - 6, stroke=self.grid_color(), width=0.7)
        return bottom

    def grand_total(self, left, top, right, label, value):
        h = self.lh(self.size + 2) + 8
        self.rect(left - 6, top + 2, right + 4, top + 2 + h, fill=self.accent)
        self.text(f'{label} ({self.d.currency})', left, top + h - 1, self.size + 1, True, white)
        self.text(self.money(value), right, top + h - 1, self.size + 2, True, white, 'right')
        return top + h + 8


class CompactOnePage(BaseEngine):
    """Dense layout with small type so many lines fit on one page."""
    margin = 28
    size = 7.0
    small = 6.0
    title_size = 15
    row_pad = 2.2
    leading = 1.2
    max_wrap = 2
    zebra = True
    col_fracs = (0.26, 0.40, 0.08, 0.12, 0.14)

    def header(self):
        top = self.margin
        lw = self.logo(self.L, top, 22)
        self.text(self.d.business_name, self.L + lw, top + 10, 11, True, max_width=320)
        self.text('  |  '.join(self.company_lines()), self.L + lw, top + 20, self.small, color=self.muted,
                  max_width=self.R - self.L - lw - 150)
        self.text(self.title, self.R, top + 11, self.title_size, True, self.accent, 'right')
        self.text(f'#{self.d.number}', self.R, top + 21, self.size + 1, True, align='right')
        self.y = top + 28
        self.rect(self.L, self.y, self.R, self.y + 1.4, fill=self.accent)
        self.y += 4

    def parties(self):
        w = self.R - self.L
        rows, gst_rows = self.customer_rows()
        b1 = self.party(self.L, self.y, w * 0.5 - 10, 'BILL TO', self.d.client_name, rows, gst_rows)
        y = self.y + 2
        x = self.L + w * 0.5
        for k, v in self.meta_pairs():
            y += self.lh() + 0.5
            self.text(f'{k}:', x, y, self.size, True, self.muted)
            self.text(v, x + 70, y, self.size, max_width=w * 0.5 - 70)
        self.y = max(b1, y) + 2

    def header_bg(self):
        return shade(self.accent, 0.15)


class BoldHeader(BaseEngine):
    """Full-width coloured banner with a white summary box."""
    zebra = True
    regular, bold = 'Trebuchet', 'Trebuchet-Bold'

    def header(self):
        bh = 118
        self.rect(0, 0, self.W, bh, fill=self.accent)
        top = self.margin - 10
        lw = self.logo(self.L, top, 30, shade(self.accent, 0.35))
        self.text(self.d.business_name, self.L + lw, top + 13, 14, True, white, max_width=300)
        y = top + 16
        for r in self.company_lines()[:6]:
            y += self.lh(self.small)
            self.text(r, self.L + lw, y, self.small, color=tint(self.accent, 0.75), max_width=300)
        self.text(self.title, self.L, bh - 12, 26, True, white)
        # summary box
        bw, bx = 190, self.R - 190
        btop, bbot = top, bh + 26
        self.rect(bx, btop, self.R, bbot, fill=white, stroke=shade(self.accent, 0.1), width=0.8)
        _, (glabel, gvalue) = self.totals_rows()
        self.text('AMOUNT DUE' if not self.d.tax_invoice else 'GRAND TOTAL', bx + 12, btop + 16, self.small, True,
                  self.muted)
        self.text(f'{self.d.currency} {self.money(gvalue)}', bx + 12, btop + 36, 17, True, self.accent,
                  max_width=bw - 24)
        rows = [('Invoice No.', self.d.number), ('Invoice Date', fmt_date(self.d.invoice_date)),
                ('Due Date', fmt_date(self.d.due_date))]
        yy = btop + 44
        for k, v in rows:
            yy += self.lh() + 2
            self.text(k, bx + 12, yy, self.size, color=self.muted)
            self.text(v, self.R - 12, yy, self.size, True, align='right', max_width=bw - 90)
        self.y = bbot + 14

    def parties(self):
        w = self.R - self.L
        rows, gst_rows = self.customer_rows()
        b1 = self.party(self.L, self.y, w * 0.5 - 10, 'BILLED TO', self.d.client_name, rows, gst_rows)
        pairs = [p for p in self.meta_pairs() if p[0] not in ('Invoice No.', 'Invoice Date', 'Due Date')]
        y = self.y
        x = self.L + w * 0.55
        for k, v in pairs:
            y += self.lh() + 3
            self.text(k, x, y, self.size, True, self.muted)
            self.text(v, self.R, y, self.size, True, align='right', max_width=w * 0.45 - 80)
        self.y = max(b1, y) + 4

    def header_bg(self):
        return shade(self.accent, 0.3)


class TimesheetService(BaseEngine):
    """Service-period focused: a prominent period panel and a per-line period under each description."""
    show_line_periods = True
    col_fracs = (0.24, 0.40, 0.09, 0.12, 0.15)

    def overall_period(self):
        starts = [a for a, _ in self.x.line_periods if a]
        ends = [b for _, b in self.x.line_periods if b]
        return period_text(min(starts) if starts else None, max(ends) if ends else None)

    def header(self):
        top = self.margin
        self.rect(self.L, top, self.L + 4, top + 50, fill=self.accent)
        lw = self.logo(self.L + 12, top, 30)
        self.text(self.d.business_name, self.L + 12 + lw, top + 12, 14, True, max_width=300)
        y = top + 14
        for r in self.company_lines()[:6]:
            y += self.lh(self.small)
            self.text(r, self.L + 12 + lw, y, self.small, color=self.muted, max_width=300)
        self.text(self.title, self.R, top + 18, 20, True, self.accent, 'right')
        self.text(f'# {self.d.number}', self.R, top + 33, self.size + 2, True, align='right')
        self.text(f'Dated {fmt_date(self.d.invoice_date)}', self.R, top + 45, self.size, color=self.muted,
                  align='right')
        self.y = max(y, top + 50) + 12
        # service period panel
        ph = 44
        self.rect(self.L, self.y, self.R, self.y + ph, fill=self.soft)
        cols = [('SERVICE PERIOD', self.overall_period() or '-'), ('BILLING MONTH', self.x.billing_month or '-'),
                ('TOTAL QTY (DAYS)', f'{sum((Decimal(l.quantity) for l in self.d.lines), Decimal("0")):.2f}'),
                ('DUE DATE', fmt_date(self.d.due_date) or '-')]
        cw = (self.R - self.L) / len(cols)
        for i, (k, v) in enumerate(cols):
            x = self.L + 10 + i * cw
            self.text(k, x, self.y + 15, self.small, True, shade(self.accent, 0.1), max_width=cw - 14)
            self.text(v, x, self.y + 33, 11, True, max_width=cw - 14)
        self.y += ph + 10

    def parties(self):
        half = (self.R - self.L) / 2
        rows, gst_rows = self.customer_rows()
        b1 = self.party(self.L, self.y, half - 20, 'BILL TO', self.d.client_name, rows, gst_rows)
        pairs = [p for p in self.meta_pairs() if p[0] not in ('Due Date',)]
        b2 = self.meta_grid(self.L + half, self.y + 4, half, cols=2)
        self.y = max(b1, b2) + 4


ENGINES = {
    'modern_minimal': ModernMinimal,
    'corporate_classic': CorporateClassic,
    'compact': CompactOnePage,
    'bold_header': BoldHeader,
    'timesheet': TimesheetService,
}


def render_format_pdf(snap, data, extras=None) -> bytes:
    """Render ``data`` (InvoiceData) with a non-standard engine described by ``snap`` (format snapshot)."""
    engine = ENGINES[snap['layout_engine']]
    buf = BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=page_size(snap))
    c.setTitle(f'{"Tax Invoice" if data.tax_invoice else "Invoice"} {data.number}')
    c.setAuthor(data.business_name)
    engine(c, data, extras, snap).render()
    c.save()
    return buf.getvalue()


def format_extras(invoice, lines, snap=None):
    snap = snap or {}
    month = getattr(invoice, 'billing_month', None)
    return FormatExtras(
        po_number=(getattr(invoice, 'po_number', '') or '').strip(),
        vendor_code=((snap['vendor_code'] if 'vendor_code' in snap else
                      getattr(invoice.client, 'vendor_code', '')) or '').strip(),
        billing_month=month.strftime('%B %Y') if month else '',
        line_periods=[(getattr(l, 'from_date', None), getattr(l, 'to_date', None)) for l in lines],
        status=getattr(invoice, 'status', '') or '',
    )


def snapshot_for(template, client=None):
    """Frozen copy of a template's settings (+ the customer's vendor code at that moment)."""
    if template is None:
        return None
    snap = template.settings_snapshot()
    snap['vendor_code'] = (getattr(client, 'vendor_code', '') or '').strip() if client is not None else ''
    return snap


def render_for_invoice(invoice, profile, lines=None, number=None, template=None):
    """Single entry point for invoice PDFs.

    ``template`` given  -> ad-hoc render with that template's *current* settings.
    otherwise           -> the invoice's saved ``format_snapshot``.
    No format / the 'standard' engine -> the original renderer, byte-identical to pre-format output.
    """
    from .from_models import build_invoice_data
    from .render import render_invoice_pdf
    if lines is None:
        lines = list(invoice.lines.all()) if invoice.pk else []
    data = build_invoice_data(invoice, profile, lines=lines, number=number)
    snap = snapshot_for(template, invoice.client) if template is not None else getattr(invoice, 'format_snapshot',
                                                                                         None)
    if not snap or snap.get('layout_engine') not in ENGINES:
        return render_invoice_pdf(data)
    return render_format_pdf(snap, data, format_extras(invoice, lines, snap))
