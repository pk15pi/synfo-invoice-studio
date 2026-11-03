"""Excel (openpyxl) and PDF (ReportLab) exports. Both render a core.Report, so every report exports the same figures
shown on screen. The PDF layout is a dedicated report template (NOT the invoice template)."""
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
import copy
import re

from django.utils import timezone

from . import services as S
from .core import NT, text

INR_FMT = '#,##0.00;[Red]-#,##0.00'


def _company():
    try:
        return S.profile().name
    except Exception:  # pragma: no cover
        return 'QuantBud'


# ------------------------------------------------------------------------------------------- masking
_AADHAAR = re.compile(r'(?<![\w])\d{4}[ -]?\d{4}[ -]?\d{4}(?![\w])')
_PAN = re.compile(r'(?<![A-Za-z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Za-z0-9])')
_BANK = re.compile(r'(?<![\w])\d{9,18}(?![\w])')


def mask_text(s):
    """Mask Aadhaar (12 digits, optionally spaced), PAN and bank account numbers (9-18 digits) - last 4 kept."""
    if not isinstance(s, str) or not s:
        return s
    from employee_management.validators import mask
    for rx in (_AADHAAR, _PAN, _BANK):
        s = rx.sub(lambda m: mask(m.group(0)), s)
    return s


def masked(rep):
    """Copy of a report with every text cell / filter / note masked (applied by every export)."""
    if getattr(rep, '_masked', False):
        return rep
    r = copy.deepcopy(rep)
    r.title, r.statement = mask_text(r.title), mask_text(r.statement)
    r.filters = [(k, mask_text(str(v))) for k, v in r.filters]
    r.notes = [mask_text(n) for n in r.notes]
    r.limitations = [mask_text(n) for n in r.limitations]
    for c in r.cards:
        c['label'], c['note'] = mask_text(c.get('label')), mask_text(c.get('note'))
        if isinstance(c.get('value'), str):
            c['value'] = mask_text(c['value'])
    for t in r.tables:
        t.title, t.note = mask_text(t.title), mask_text(t.note)
        t.rows = [[mask_text(v) for v in row] for row in t.rows]
        if t.total:
            t.total = [mask_text(v) for v in t.total]
    r._masked = True
    return r


def report_csv(rep):
    import csv
    from io import StringIO
    rep = masked(rep)
    buf = StringIO()
    w = csv.writer(buf)
    for k, v in meta_lines(rep):
        w.writerow([k, v])
    if rep.statement:
        w.writerow([rep.statement])

    def cell(v, kind):
        if isinstance(v, Decimal):
            return f'{v:.2f}'
        if isinstance(v, (date, datetime)):
            return v.strftime('%d-%m-%Y')
        return '' if v is None else str(v)
    for t in rep.tables:
        w.writerow([])
        w.writerow([t.title])
        w.writerow([c.label for c in t.cols])
        for row in t.rows:
            w.writerow([cell(v, c.kind) for v, c in zip(row, t.cols)])
        if t.total:
            w.writerow([cell(v, c.kind) for v, c in zip(t.total, t.cols)])
    for n in rep.notes + rep.limitations:
        w.writerow([n])
    return ('\ufeff' + buf.getvalue()).encode('utf-8')


def meta_lines(rep):
    lines = [('Report', rep.title), ('Period', f'{rep.period.label} ({rep.period.range_text})'), ('Date basis', rep.basis)]
    lines += [(k, str(v)) for k, v in rep.filters]
    lines.append(('Generated', timezone.localtime().strftime('%d %b %Y %H:%M')))
    return lines


# ------------------------------------------------------------------------------------------- Excel
def _xl_styles():
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    thin = Side(style='thin', color='C9CED6')
    return dict(
        title=Font(bold=True, size=14, color='1F2A44'), bold=Font(bold=True), head_font=Font(bold=True, color='FFFFFF'),
        head_fill=PatternFill('solid', fgColor='1F2A44'), sub_fill=PatternFill('solid', fgColor='EEF1F6'),
        tot_fill=PatternFill('solid', fgColor='DDE3EC'), border=Border(left=thin, right=thin, top=thin, bottom=thin),
        wrap=Alignment(wrap_text=True, vertical='top'), right=Alignment(horizontal='right', vertical='top'),
        center=Alignment(horizontal='center', vertical='center', wrap_text=True), muted=Font(italic=True, color='666666'))


def _xl_value(v, kind):
    if isinstance(v, tuple):
        v = v[0]
    if v is None:
        return NT if kind in ('money', 'pct') else ''
    if kind == 'money' and isinstance(v, (int, Decimal)):
        return float(v)
    if kind == 'int' and isinstance(v, int):
        return v
    if kind == 'pct' and isinstance(v, Decimal):
        return float(v) / 100
    if isinstance(v, (date, datetime)):
        return v
    return text(v, kind)


def write_sheet(ws, rep, st=None):
    from openpyxl.utils import get_column_letter
    st = st or _xl_styles()
    rep = masked(rep)
    ws.sheet_view.showGridLines = False
    ws['A1'] = _company()
    ws['A1'].font = st['title']
    row = 2
    for k, v in meta_lines(rep):
        ws.cell(row, 1, k).font = st['bold']
        ws.cell(row, 2, v)
        row += 1
    if rep.statement:
        ws.cell(row, 1, rep.statement).font = st['muted']
        row += 1
    widths = {}
    if rep.cards:
        row += 1
        ws.cell(row, 1, 'Summary').font = st['bold']
        row += 1
        for c in rep.cards:
            ws.cell(row, 1, c['label'])
            val = c['value']
            cell = ws.cell(row, 2, NT if val is None else (float(val) if c['kind'] == 'money' and not isinstance(val, str)
                                                           else (f'{val}%' if c['kind'] == 'pct' and not isinstance(val, str)
                                                                 else val)))
            if c['kind'] == 'money' and val is not None and not isinstance(val, str):
                cell.number_format = INR_FMT
            cell.alignment = st['right']
            if c.get('prev') is not None:
                ws.cell(row, 3, float(c['prev'])).number_format = INR_FMT
                ws.cell(row, 4, c['change'])
            if c['note']:
                ws.cell(row, 5, c['note']).font = st['muted']
            row += 1
        widths[1] = 34
    freeze = None
    for t in rep.tables:
        row += 1
        ws.cell(row, 1, t.title).font = st['bold']
        row += 1
        for j, col in enumerate(t.cols, 1):
            c = ws.cell(row, j, col.label)
            c.font, c.fill, c.alignment, c.border = st['head_font'], st['head_fill'], st['center'], st['border']
            widths[j] = max(widths.get(j, 8), min(len(col.label) + 2, 30))
        if freeze is None:
            freeze = f'A{row + 1}'
            if len(rep.tables) == 1:
                ws.print_title_rows = f'{row}:{row}'
        row += 1
        body = list(zip(t.rows, t.styles))
        if t.total:
            body.append((t.total, 'total'))
        for r_, style in body:
            for j, col in enumerate(t.cols, 1):
                v = r_[j - 1] if j - 1 < len(r_) else ''
                c = ws.cell(row, j, _xl_value(v, col.kind))
                c.border = st['border']
                if col.kind == 'money' and isinstance(c.value, float):
                    c.number_format = INR_FMT
                    c.alignment = st['right']
                elif col.kind == 'pct' and isinstance(c.value, float):
                    c.number_format = '0.00%'
                    c.alignment = st['right']
                elif col.kind == 'date' and isinstance(c.value, date):
                    c.number_format = 'DD-MMM-YYYY'
                elif col.kind in ('int', 'money', 'pct'):
                    c.alignment = st['right']
                else:
                    c.alignment = st['wrap']
                if style in ('subtotal', 'group'):
                    c.font, c.fill = st['bold'], st['sub_fill']
                elif style == 'total':
                    c.font, c.fill = st['bold'], st['tot_fill']
                widths[j] = max(widths.get(j, 8), min(len(text(v, col.kind)) + 2, 45))
            row += 1
        if t.note:
            ws.cell(row, 1, t.note).font = st['muted']
            row += 1
    for title, items in (('Notes', rep.notes), ('Limitations (not tracked)', rep.limitations)):
        if items:
            row += 1
            ws.cell(row, 1, title).font = st['bold']
            for n in items:
                row += 1
                ws.cell(row, 1, n).font = st['muted']
    for j, w in widths.items():
        ws.column_dimensions[get_column_letter(j)].width = w
    ws.column_dimensions['A'].width = max(ws.column_dimensions['A'].width or 0, 22)
    if freeze:
        ws.freeze_panes = freeze
    ws.page_setup.orientation = 'landscape' if rep.landscape else 'portrait'
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.oddFooter.center.text = 'Page &P of &N'
    ws.oddHeader.center.text = f'{rep.title} - {rep.period.label}'
    return ws


def report_xlsx(rep):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = re.sub(r'[\\/*?:\[\]]', '-', rep.title)[:31]
    write_sheet(ws, rep)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


FY_SHEETS = [('Executive Summary', 'pnl'), ('Revenue Details', 'invoice_register'), ('Payroll Details', 'payroll_register'),
             ('Employee Claims', 'employee_claims'), ('Company Expenses', 'company_expenses'),
             ('Monthly Financial Summary', 'monthly_perf'), ('Outstanding Amounts', 'outstanding')]


def fy_workbook(fy_year):
    """Complete FY workbook with the 7 required sheets."""
    from openpyxl import Workbook
    from . import reports
    from .dashboard import executive
    params = {'period': 'fy', 'fy': str(fy_year)}
    wb = Workbook()
    wb.remove(wb.active)
    st = _xl_styles()
    for name, key in FY_SHEETS:
        rep = reports.build(key, params)
        if key == 'pnl':
            ex = executive(rep.period)
            rep.title = 'Executive Summary'
            rep.cards = ex['cards']
        write_sheet(wb.create_sheet(name), rep, st)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------------------------------- PDF
def report_pdf(rep):
    rep = masked(rep)
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table as RLTable, TableStyle
    from xml.sax.saxutils import escape

    size = landscape(A4) if rep.landscape or any(len(t.cols) > 7 for t in rep.tables) else A4
    buf = BytesIO()
    company = _company()
    ss = getSampleStyleSheet()
    maxcols = max([len(t.cols) for t in rep.tables] or [4])
    fs = 8 if maxcols <= 8 else 7 if maxcols <= 12 else 5.6
    cell = ParagraphStyle('c', parent=ss['Normal'], fontSize=fs, leading=fs + 2)
    cell_r = ParagraphStyle('cr', parent=cell, alignment=TA_RIGHT)
    head = ParagraphStyle('h', parent=cell, fontName='Helvetica-Bold', textColor=colors.white)
    head_r = ParagraphStyle('hr', parent=head, alignment=TA_RIGHT)
    bold = ParagraphStyle('b', parent=cell, fontName='Helvetica-Bold')
    bold_r = ParagraphStyle('br', parent=bold, alignment=TA_RIGHT)
    small = ParagraphStyle('s', parent=ss['Normal'], fontSize=8, leading=10, textColor=colors.HexColor('#555555'))
    h1 = ParagraphStyle('h1', parent=ss['Heading1'], fontSize=15, spaceAfter=2)
    h3 = ParagraphStyle('h3', parent=ss['Heading3'], fontSize=10, spaceBefore=8, spaceAfter=3)

    class Numbered(pdfcanvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            n = len(self._pages)
            for st_ in self._pages:
                self.__dict__.update(st_)
                w, h = size
                self.setFont('Helvetica', 7.5)
                self.setFillColor(colors.HexColor('#555555'))
                self.drawString(12 * mm, h - 8 * mm, f'{company} | {rep.title} | {rep.period.label}')
                self.drawRightString(w - 12 * mm, 7 * mm, f'Page {self._pageNumber} of {n}')
                self.drawString(12 * mm, 7 * mm, 'Management report - figures in INR')
                super().showPage()
            super().save()

    doc = SimpleDocTemplate(buf, pagesize=size, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=13 * mm,
                            bottomMargin=13 * mm, title=rep.title, author=company)
    width = size[0] - 24 * mm
    story = [Paragraph(escape(company), small), Paragraph(escape(rep.title), h1)]
    for k, v in meta_lines(rep):
        story.append(Paragraph(f'<b>{escape(k)}:</b> {escape(v)}', small))
    if rep.statement:
        story += [Spacer(1, 4), Paragraph(f'<i>{escape(rep.statement)}</i>', small)]
    if rep.cards:
        data, row = [], []
        for c in rep.cards:
            v = c['value']
            val = NT if v is None else (text(v, 'money') if c['kind'] == 'money' and not isinstance(v, str)
                                        else f'{v}%' if c['kind'] == 'pct' and not isinstance(v, str) else str(v))
            extra = f"<br/><font size=6>vs {text(c['prev'], 'money')} ({c['change']})</font>" if c.get('prev') is not None else ''
            note = f"<br/><font size=6>{escape(c['note'])}</font>" if c['note'] else ''
            row.append(Paragraph(f"<font size=6.5>{escape(c['label'])}</font><br/><b>{escape(val)}</b>{extra}{note}", cell))
            if len(row) == 4:
                data.append(row)
                row = []
        if row:
            data.append(row + [''] * (4 - len(row)))
        ct = RLTable(data, colWidths=[width / 4] * 4)
        ct.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), 0.4, colors.HexColor('#C9CED6')),
                                ('INNERGRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#C9CED6')),
                                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F6F8FB')),
                                ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
        story += [Spacer(1, 6), ct]
    for t in rep.tables:
        story.append(Paragraph(escape(t.title), h3))
        num = [c.kind in ('money', 'int', 'pct') for c in t.cols]
        data = [[Paragraph(escape(c.label), head_r if num[j] else head) for j, c in enumerate(t.cols)]]
        body = list(zip(t.rows, t.styles)) + ([(t.total, 'total')] if t.total else [])
        lens = [max(len(c.label) * 0.7, 6) for c in t.cols]
        for r_, style in body:
            strong = style in ('subtotal', 'total', 'group')
            line = []
            for j, c in enumerate(t.cols):
                s = text(r_[j] if j < len(r_) else '', c.kind)
                lens[j] = max(lens[j], min(len(s), 40))
                stl = (bold_r if num[j] else bold) if strong else (cell_r if num[j] else cell)
                line.append(Paragraph(escape(s), stl))
            data.append(line)
        tot = sum(lens)
        widths = [width * l_ / tot for l_ in lens]
        rt = RLTable(data, colWidths=widths, repeatRows=1)
        cmds = [('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F2A44')),
                ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#C9CED6')), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                ('LEFTPADDING', (0, 0), (-1, -1), 3), ('RIGHTPADDING', (0, 0), (-1, -1), 3)]
        for i, (_, style) in enumerate(body, 1):
            if style in ('subtotal', 'group'):
                cmds.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#EEF1F6')))
            elif style == 'total':
                cmds.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#DDE3EC')))
            elif i % 2 == 0:
                cmds.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#FAFBFD')))
        rt.setStyle(TableStyle(cmds))
        story.append(rt)
        if t.note:
            story.append(Paragraph(escape(t.note), small))
    for title, items in (('Notes', rep.notes), ('Limitations (not tracked)', rep.limitations)):
        if items:
            story.append(KeepTogether([Paragraph(title, h3)] + [Paragraph('- ' + escape(n), small) for n in items]))
    doc.build(story, canvasmaker=Numbered)
    return buf.getvalue()
