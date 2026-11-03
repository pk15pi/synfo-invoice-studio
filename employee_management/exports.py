"""Excel / CSV exports. Bank transfer files contain full account numbers: they are
CONFIDENTIAL, generated on demand, never written to disk by the app."""
import csv
import io

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

HEAD = Font(bold=True, color='FFFFFF')
FILL = PatternFill('solid', fgColor='1F3A5F')


def _sheet(ws, headers, rows, money_cols=()):
    ws.append(headers)
    for cell in ws[1]:
        cell.font, cell.fill = HEAD, FILL
    for r in rows:
        ws.append(r)
    for i, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(i)].width = max(12, min(40, len(str(h)) + 4))
        if i in money_cols:
            for row in ws.iter_rows(min_row=2, min_col=i, max_col=i):
                for cell in row:
                    cell.number_format = '#,##0.00'
    ws.freeze_panes = 'A2'


def _book(sheets):
    wb = Workbook()
    wb.remove(wb.active)
    for title, headers, rows, money in sheets:
        _sheet(wb.create_sheet(title[:31]), headers, rows, money)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def salary_register_rows(payrolls):
    labels_e, labels_d = [], []
    for p in payrolls:
        for x in p.earnings.all():
            if x.label not in labels_e:
                labels_e.append(x.label)
        for x in p.deductions.all():
            key = x.label + (' (employer)' if x.side == 'employer' else '')
            if key not in labels_d:
                labels_d.append(key)
    headers = (['Employee ID', 'Name', 'Department', 'Designation', 'Status', 'Revision', 'Divisor',
                'Payable Days', 'LWP'] + labels_e + ['Gross'] + labels_d +
               ['Total Deductions', 'Employer Contributions', 'Rounding', 'Net Salary'])
    rows = []
    for p in payrolls:
        e = {x.label: x.amount for x in p.earnings.all()}
        d = {x.label + (' (employer)' if x.side == 'employer' else ''): x.amount for x in p.deductions.all()}
        rows.append([p.code, p.snap_name, p.snap_department, p.snap_designation, p.get_status_display(),
                     p.revision, float(p.divisor_days), float(p.payable_days), float(p.lwp_days)] +
                    [float(e.get(l, 0)) for l in labels_e] + [float(p.gross_earnings)] +
                    [float(d.get(l, 0)) for l in labels_d] +
                    [float(p.total_deductions), float(p.employer_contributions), float(p.rounding_adjustment),
                     float(p.net_salary)])
    first_money = 10
    money = set(range(first_money, len(headers) + 1))
    return headers, rows, money


def salary_register_xlsx(period, payrolls):
    h, rows, money = salary_register_rows(payrolls)
    return _book([(f'Register {period.label}', h, rows, money)])


def salary_register_csv(period, payrolls):
    h, rows, _ = salary_register_rows(payrolls)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(h)
    for r in rows:
        w.writerow([f'{c:.2f}' if isinstance(c, float) else c for c in r])
    return out.getvalue().encode('utf-8-sig')


BANK_HEADERS = ['Employee ID', 'Employee Name', 'Bank Name', 'Account Number', 'IFSC Code', 'Net Salary',
                'Payment Date', 'Remarks']


def bank_rows(period, payrolls, payment_date, full=False):
    """Account numbers are masked to the last 4 digits unless ``full`` is explicitly requested."""
    from .validators import mask
    pd = payment_date.strftime('%d-%m-%Y') if payment_date else ''
    return [[p.code, p.snap_name, p.snap_bank_name, p.snap_account_number if full else mask(p.snap_account_number), p.snap_ifsc, f'{p.net_salary:.2f}', pd,
             f'Salary {period.label}'] for p in payrolls]


def bank_csv(period, payrolls, payment_date, full=False):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(BANK_HEADERS)
    for r in bank_rows(period, payrolls, payment_date, full):
        w.writerow(r)
    return out.getvalue().encode('utf-8-sig')


def bank_xlsx(period, payrolls, payment_date, full=False):
    rows = bank_rows(period, payrolls, payment_date, full)
    for r in rows:
        r[5] = float(r[5])
    data = _book([('Bank Transfer', BANK_HEADERS, rows, {6})])
    return data


def generic_xlsx(title, headers, rows, money=()):
    return _book([(title, headers, rows, set(money))])
