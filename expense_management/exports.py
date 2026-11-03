"""CSV / XLSX / PDF export of a report dict (see reports.py). The PDF is a plain ReportLab
table and is completely separate from the invoice PDF renderer."""
import csv
import io
from datetime import date
from decimal import Decimal

from django.http import HttpResponse


def _cell(v):
    if isinstance(v, date):
        return v.strftime('%d-%m-%Y')
    if isinstance(v, Decimal):
        return f'{v:.2f}'
    return '' if v is None else str(v)


def _filename(rep, f, ext):
    return f'expense-{rep["key"]}-{f["start"]:%Y%m%d}-{f["end"]:%Y%m%d}.{ext}'


def _rows(rep):
    out = list(rep['rows'])
    if rep.get('totals'):
        out.append(rep['totals'])
    return out


def to_csv(rep, f):
    resp = HttpResponse(content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="{_filename(rep, f, "csv")}"'
    resp.write('\ufeff')  # Excel on Windows opens UTF-8 correctly with a BOM
    w = csv.writer(resp)
    w.writerow([rep['title'], f['label']])
    w.writerow(rep['headers'])
    for r in _rows(rep):
        w.writerow([_cell(v) for v in r])
    if rep.get('note'):
        w.writerow([])
        w.writerow([rep['note']])
    return resp


def to_xlsx(rep, f):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = rep['key'][:31]
    ws.append([rep['title']])
    ws['A1'].font = Font(bold=True, size=13)
    ws.append([f'Period: {f["label"]}'])
    ws.append([])
    ws.append(rep['headers'])
    for c in ws[4]:
        c.font = Font(bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='0B5394')
    for r in rep['rows']:
        ws.append([v if isinstance(v, (Decimal, int, date)) else _cell(v) for v in r])
    if rep.get('totals'):
        ws.append([v if isinstance(v, (Decimal, int)) else _cell(v) for v in rep['totals']])
        for c in ws[ws.max_row]:
            c.font = Font(bold=True)
    for row in ws.iter_rows(min_row=5):
        for c in row:
            if isinstance(c.value, Decimal):
                c.number_format = '#,##0.00'
            elif isinstance(c.value, date):
                c.number_format = 'DD-MM-YYYY'
    if rep.get('note'):
        ws.append([])
        ws.append([rep['note']])
    for i, h in enumerate(rep['headers'], 1):
        ws.column_dimensions[ws.cell(row=4, column=i).column_letter].width = max(12, min(40, len(h) + 4))
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(),
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="{_filename(rep, f, "xlsx")}"'
    return resp


def to_pdf(rep, f, business_name=''):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm, title=rep['title'])
    ss = getSampleStyleSheet()
    small = ss['BodyText'].clone('small', fontSize=8, leading=10)
    story = [Paragraph(f'{business_name} &middot; {rep["title"]}' if business_name else rep['title'], ss['Title']),
             Paragraph(f'Period: {f["label"]}', ss['Normal']), Spacer(1, 6)]
    money = rep.get('money_cols', set())
    data = [[Paragraph(f'<b>{h}</b>', small) for h in rep['headers']]]
    for r in _rows(rep):
        data.append([Paragraph(_cell(v).replace('&', '&amp;').replace('<', '&lt;'), small) if i not in money
                     else _cell(v) for i, v in enumerate(r)])
    t = Table(data, repeatRows=1)
    style = [('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#cfe2f3')),
             ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#9fb3c8')),
             ('FONTSIZE', (0, 0), (-1, -1), 8), ('VALIGN', (0, 0), (-1, -1), 'TOP')]
    for c in money:
        style.append(('ALIGN', (c, 1), (c, -1), 'RIGHT'))
    if rep.get('totals'):
        style.append(('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'))
    t.setStyle(TableStyle(style))
    story.append(t)
    if rep.get('note'):
        story += [Spacer(1, 8), Paragraph(rep['note'], small)]
    story += [Spacer(1, 4), Paragraph('Amounts in INR (foreign currency converted at the rate stored on each record).',
                                      small)]
    doc.build(story)
    resp = HttpResponse(buf.getvalue(), content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{_filename(rep, f, "pdf")}"'
    return resp
