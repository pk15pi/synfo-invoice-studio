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
# ---------------------------------------------------------------------------
# CA salary TDS — Indian FY quarterly export (one row per employee)
# ---------------------------------------------------------------------------
import calendar
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Q

_ZERO = Decimal('0.00')


def _q2(value):
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def quarter_months(fy_start, quarter):
    """Return [(year, month), ...] for Indian FY quarter 1..4.

    Q1 Apr–Jun, Q2 Jul–Sep, Q3 Oct–Dec (fy_start); Q4 Jan–Mar (fy_start + 1).
    """
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f'quarter must be 1..4, got {quarter!r}')
    month_nos = {1: (4, 5, 6), 2: (7, 8, 9), 3: (10, 11, 12), 4: (1, 2, 3)}[quarter]
    return [(fy_start if month >= 4 else fy_start + 1, month) for month in month_nos]


def _month_label(year, month):
    return f'{calendar.month_abbr[month]} {year}'


def _line_sum(lines, code, employee_side_only=False):
    total = _ZERO
    for line in lines:
        if line.code != code:
            continue
        if employee_side_only and getattr(line, 'side', 'employee') != 'employee':
            continue
        total += line.amount
    return total


def salary_tds_quarter_rows(fy_start, quarter):
    """Build headers/rows for the CA TDS workbook. Reads finalized current payroll only."""
    from .models import EmployeePayroll, PayrollDeduction

    months = quarter_months(fy_start, quarter)
    period_q = Q()
    for year, month in months:
        period_q |= Q(period__year=year, period__month=month)
    slips = (
        EmployeePayroll.objects.filter(
            period_q, status=EmployeePayroll.FINALIZED, is_current=True,
        )
        .select_related('period', 'employee')
        .prefetch_related('earnings', 'deductions')
        .order_by('snap_employee_code', 'employee__employee_id', 'period__year', 'period__month')
    )

    by_employee = {}
    for slip in slips:
        key = slip.employee_id
        info = by_employee.get(key)
        if info is None:
            info = {
                'code': slip.code,
                'name': slip.snap_name,
                'pan': (slip.employee.pan_number or '').strip().upper(),
                'department': slip.snap_department,
                'designation': slip.snap_designation,
                'months': {},
                'latest': None,
            }
            by_employee[key] = info
        month_key = (slip.period.year, slip.period.month)
        info['months'][month_key] = slip
        if info['latest'] is None or month_key > info['latest']:
            info['latest'] = month_key
            info['code'] = slip.code
            info['name'] = slip.snap_name
            info['department'] = slip.snap_department
            info['designation'] = slip.snap_designation

    headers = ['Employee ID', 'Employee Name', 'PAN', 'Department', 'Designation']
    for year, month in months:
        label = _month_label(year, month)
        headers.extend([f'{label} Gross', f'{label} TDS'])
    headers.extend([
        'Gross Salary (Quarter)', 'Basic', 'HRA', 'Other Earnings',
        'Employee PF', 'Professional Tax', 'Income Tax / TDS', 'Other Deductions',
        'Net Pay (Quarter)',
    ])

    rows = []
    for info in sorted(by_employee.values(), key=lambda item: (item['code'] or '', item['name'] or '')):
        row = [info['code'], info['name'], info['pan'], info['department'], info['designation']]
        q_gross = q_basic = q_hra = q_pf = q_pt = q_tds = q_net = q_other_earn = q_other_ded = _ZERO
        for month_key in months:
            slip = info['months'].get(month_key)
            if slip is None:
                row.extend(['', ''])
                continue
            earnings = list(slip.earnings.all())
            deductions = list(slip.deductions.all())
            basic = _line_sum(earnings, 'basic')
            hra = _line_sum(earnings, 'hra')
            tds = _line_sum(deductions, 'tds', employee_side_only=True)
            pf = _line_sum(deductions, 'pf', employee_side_only=True)
            pt = _line_sum(deductions, 'professional_tax', employee_side_only=True)
            emp_ded = sum(
                (line.amount for line in deductions if line.side == PayrollDeduction.EMPLOYEE),
                _ZERO,
            )
            row.extend([float(slip.gross_earnings), float(tds)])
            q_gross += slip.gross_earnings
            q_net += slip.net_salary
            q_basic += basic
            q_hra += hra
            q_other_earn += slip.gross_earnings - basic - hra
            q_pf += pf
            q_pt += pt
            q_tds += tds
            q_other_ded += emp_ded - pf - pt - tds
        row.extend([
            float(q_gross), float(q_basic), float(q_hra), float(q_other_earn),
            float(q_pf), float(q_pt), float(q_tds), float(q_other_ded), float(q_net),
        ])
        rows.append(row)

    money_cols = set(range(6, len(headers) + 1))
    fy_tag = f'FY{fy_start}-{str(fy_start + 1)[-2:]}'
    sheet_title = f'TDS Q{quarter} {fy_tag}'
    filename = f'Salary_TDS_Q{quarter}_{fy_tag}.xlsx'
    return headers, rows, money_cols, sheet_title, filename


def salary_tds_quarter_xlsx(fy_start, quarter):
    headers, rows, money_cols, sheet_title, filename = salary_tds_quarter_rows(fy_start, quarter)
    return _book([(sheet_title, headers, rows, money_cols)]), filename

