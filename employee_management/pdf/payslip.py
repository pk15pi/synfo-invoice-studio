"""Payslip PDF (ReportLab). Independent of the invoice renderer; reuses only the
Business Profile (name, address, contact, GSTIN, signature)."""
import calendar
import io
from dataclasses import dataclass, field
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from ..numwords import rupees_in_words
from ..validators import mask

PRIMARY = colors.HexColor('#1f3a5f')
LIGHT = colors.HexColor('#eef2f7')
BORDER = colors.HexColor('#b8c4d4')


@dataclass
class PayslipData:
    company_name: str
    company_address: list
    company_contact: str
    gstin: str
    month_label: str
    employee_code: str
    name: str
    department: str
    designation: str
    date_of_joining: object
    pan_masked: str
    bank_name: str
    account_masked: str
    calendar_days: Decimal
    divisor_days: Decimal
    working_days: Decimal
    payable_days: Decimal
    lwp_days: Decimal
    paid_leave_days: Decimal
    unpaid_leave_days: Decimal
    earnings: list = field(default_factory=list)          # [(label, amount)]
    deductions: list = field(default_factory=list)
    employer: list = field(default_factory=list)
    gross: Decimal = Decimal('0')
    total_deductions: Decimal = Decimal('0')
    rounding_adjustment: Decimal = Decimal('0')
    net: Decimal = Decimal('0')
    payment_date: object = None
    status: str = ''
    revision: int = 1
    signature_path: str = None
    signatory: str = 'Authorised Signatory'
    footer: str = ''


def money(v):
    v = Decimal(v).quantize(Decimal('0.01'))
    s = f'{abs(v):.2f}'
    whole, frac = s.split('.')
    if len(whole) > 3:  # Indian grouping 12,34,567.00
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ','.join(groups) + ',' + tail
    return ('-' if v < 0 else '') + whole + '.' + frac


def _signature(settings, profile):
    if not settings.use_business_signature and settings.signature_image:
        try:
            return settings.signature_image.path
        except Exception:
            pass
    from invoice_app.pdf.from_models import signature_path
    return signature_path(profile)


def build_payslip_data(source, settings, profile, period=None, payment_date=None):
    """`source` is either an EmployeePayroll (saved, uses frozen snapshot) or a payroll.Calc."""
    from ..models import EmployeePayroll
    contact = ' | '.join(x for x in (profile.phone and f'Phone: {profile.phone}',
                                     profile.email and f'Email: {profile.email}') if x)
    common = dict(company_name=profile.name, company_address=(profile.address or '').splitlines(),
                  company_contact=contact, gstin=profile.gstin, signature_path=_signature(settings, profile),
                  signatory=settings.signatory_name or profile.signature_text or 'Authorised Signatory',
                  footer=settings.payslip_footer)
    if isinstance(source, EmployeePayroll):
        p = source
        ded = list(p.deductions.all())
        return PayslipData(
            month_label=p.period.label, employee_code=p.snap_employee_code, name=p.snap_name,
            department=p.snap_department, designation=p.snap_designation, date_of_joining=p.snap_date_of_joining,
            pan_masked=p.snap_pan_masked, bank_name=p.snap_bank_name, account_masked=mask(p.snap_account_number),
            calendar_days=p.calendar_days, divisor_days=p.divisor_days, working_days=p.working_days,
            payable_days=p.payable_days, lwp_days=p.lwp_days, paid_leave_days=p.paid_leave_days,
            unpaid_leave_days=p.unpaid_leave_days,
            earnings=[(x.label, x.amount) for x in p.earnings.all()],
            deductions=[(x.label, x.amount) for x in ded if x.side == 'employee'],
            employer=[(x.label, x.amount) for x in ded if x.side == 'employer'],
            gross=p.gross_earnings, total_deductions=p.total_deductions,
            rounding_adjustment=p.rounding_adjustment, net=p.net_salary,
            payment_date=p.period.payment_date, status=p.get_status_display(), revision=p.revision, **common)
    c = source
    e = c.employee
    bank = e.current_bank
    return PayslipData(
        month_label=c.period.label, employee_code=e.employee_id, name=e.full_name, department=e.department.name,
        designation=e.designation, date_of_joining=e.date_of_joining, pan_masked=mask(e.pan_number),
        bank_name=bank.bank_name if bank else '', account_masked=bank.account_masked if bank else '',
        calendar_days=c.calendar_days, divisor_days=c.divisor_days, working_days=c.working_days,
        payable_days=c.payable_days, lwp_days=c.lwp_days, paid_leave_days=c.paid_leave_days,
        unpaid_leave_days=c.unpaid_leave_days,
        earnings=[(x.label, x.amount) for x in c.earnings], deductions=[(x.label, x.amount) for x in c.deductions],
        employer=[(x.label, x.amount) for x in c.employer], gross=c.gross, total_deductions=c.total_deductions,
        rounding_adjustment=c.rounding_adjustment, net=c.net, payment_date=payment_date or c.period.payment_date,
        status='Preview (not saved)', **common)


def _days(v):
    v = Decimal(v)
    return f'{v.normalize():f}' if v == v.to_integral() else f'{v:.2f}'


def render_payslip(d: PayslipData) -> bytes:
    buf = io.BytesIO()
    W, H = A4
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f'Payslip {d.employee_code} {d.month_label}')
    c.setAuthor(d.company_name)
    L, R = 18 * mm, W - 18 * mm
    y = H - 20 * mm
    # header
    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 16)
    c.drawString(L, y, d.company_name)
    c.setFont('Helvetica-Bold', 13)
    c.drawRightString(R, y, 'PAYSLIP')
    c.setFillColor(colors.black)
    c.setFont('Helvetica', 9)
    yy = y - 13
    for line in d.company_address:
        c.drawString(L, yy, line)
        yy -= 11
    if d.company_contact:
        c.drawString(L, yy, d.company_contact)
        yy -= 11
    if d.gstin:
        c.drawString(L, yy, f'GSTIN: {d.gstin}')
        yy -= 11
    c.setFont('Helvetica', 10)
    c.drawRightString(R, y - 14, f'For the month of {d.month_label}')
    if d.revision and d.revision > 1:
        c.drawRightString(R, y - 26, f'Revision {d.revision}')
    y = yy - 6
    c.setStrokeColor(PRIMARY)
    c.setLineWidth(1.2)
    c.line(L, y, R, y)
    y -= 8

    # employee details grid
    rows = [('Employee ID', d.employee_code, 'Department', d.department),
            ('Employee Name', d.name, 'Designation', d.designation),
            ('Date of Joining', d.date_of_joining.strftime('%d-%m-%Y') if d.date_of_joining else '-',
             'PAN', d.pan_masked or '-'),
            ('Bank', d.bank_name or '-', 'Account No.', d.account_masked or '-'),
            ('LWP Days', _days(d.lwp_days), 'Paid / Unpaid Leave',
             f'{_days(d.paid_leave_days)} / {_days(d.unpaid_leave_days)}')]
    rh = 15
    colw = (R - L) / 4
    c.setLineWidth(0.5)
    c.setStrokeColor(BORDER)
    for i, row in enumerate(rows):
        top = y - i * rh
        if i % 2 == 0:
            c.setFillColor(LIGHT)
            c.rect(L, top - rh, R - L, rh, stroke=0, fill=1)
        c.setFillColor(colors.black)
        for j, txt in enumerate(row):
            c.setFont('Helvetica-Bold' if j % 2 == 0 else 'Helvetica', 9)
            c.drawString(L + j * colw + 4, top - rh + 4.5, str(txt)[:38])
    c.rect(L, y - len(rows) * rh, R - L, len(rows) * rh, stroke=1, fill=0)
    y -= len(rows) * rh + 14

    # earnings | deductions tables side by side
    half = (R - L - 10) / 2
    xs = [L, L + half + 10]
    titles = [('Earnings', d.earnings, 'Gross Earnings', d.gross),
              ('Deductions', d.deductions, 'Total Deductions', d.total_deductions)]
    n = max(len(d.earnings), len(d.deductions), 1)
    for (title, items, tot_label, tot), x in zip(titles, xs):
        c.setFillColor(PRIMARY)
        c.rect(x, y - rh, half, rh, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 9.5)
        c.drawString(x + 4, y - rh + 4.5, title)
        c.drawRightString(x + half - 4, y - rh + 4.5, 'Amount (INR)')
        c.setFillColor(colors.black)
        c.setFont('Helvetica', 9)
        for i in range(n):
            top = y - rh * (i + 1)
            if i % 2 == 1:
                c.setFillColor(LIGHT)
                c.rect(x, top - rh, half, rh, stroke=0, fill=1)
                c.setFillColor(colors.black)
            if i < len(items):
                c.drawString(x + 4, top - rh + 4.5, str(items[i][0])[:42])
                c.drawRightString(x + half - 4, top - rh + 4.5, money(items[i][1]))
        top = y - rh * (n + 1)
        c.setFont('Helvetica-Bold', 9.5)
        c.line(x, top, x + half, top)
        c.drawString(x + 4, top - rh + 4.5, tot_label)
        c.drawRightString(x + half - 4, top - rh + 4.5, money(tot))
        c.setStrokeColor(BORDER)
        c.rect(x, top - rh, half, rh * (n + 2), stroke=1, fill=0)
    y -= rh * (n + 2) + 14

    if d.employer:
        c.setFont('Helvetica-Bold', 9)
        c.drawString(L, y, 'Employer contributions (not deducted from net salary):')
        c.setFont('Helvetica', 9)
        y -= 12
        for label, amt in d.employer:
            c.drawString(L + 8, y, label)
            c.drawRightString(L + half, y, money(amt))
            y -= 11
        y -= 6

    # net pay box
    box_h = 40 if d.rounding_adjustment else 32
    c.setFillColor(LIGHT)
    c.setStrokeColor(PRIMARY)
    c.rect(L, y - box_h, R - L, box_h, stroke=1, fill=1)
    c.setFillColor(PRIMARY)
    c.setFont('Helvetica-Bold', 12)
    c.drawString(L + 6, y - 14, 'NET SALARY')
    c.drawRightString(R - 6, y - 14, f'INR {money(d.net)}')
    c.setFillColor(colors.black)
    c.setFont('Helvetica-Oblique', 9)
    c.drawString(L + 6, y - 26, rupees_in_words(d.net))
    if d.rounding_adjustment:
        c.setFont('Helvetica', 8)
        c.drawString(L + 6, y - 36, f'Includes rounding adjustment of INR {money(d.rounding_adjustment)}')
    y -= box_h + 14
    c.setFont('Helvetica', 9)
    c.drawString(L, y, f'Status: {d.status}')

    # signature
    sy = max(y - 70, 40 * mm)
    if d.signature_path:
        try:
            c.drawImage(d.signature_path, R - 55 * mm, sy + 4, width=45 * mm, height=16 * mm,
                        preserveAspectRatio=True, mask='auto', anchor='c')
        except Exception:
            pass
    c.setStrokeColor(colors.black)
    c.line(R - 60 * mm, sy, R, sy)
    c.setFont('Helvetica', 9)
    c.drawCentredString(R - 30 * mm, sy - 11, d.signatory)
    c.drawCentredString(R - 30 * mm, sy - 22, f'For {d.company_name}')
    c.setFont('Helvetica-Oblique', 8)
    c.setFillColor(colors.HexColor('#555555'))
    if d.footer:
        c.drawCentredString(W / 2, 14 * mm, d.footer)
    c.drawCentredString(W / 2, 10 * mm, 'Confidential - for the named employee only.')
    c.showPage()
    c.save()
    return buf.getvalue()


def payslip_filename(code, month_label):
    month, year = month_label.split(' ')
    safe = ''.join(ch for ch in code if ch.isalnum() or ch in '-_') or 'EMP'
    return f'{safe}_{month}_{year}_Payslip.pdf'


def payslip_for(payroll):
    from invoice_app.models import BusinessProfile
    from ..models import PayrollSettings
    data = build_payslip_data(payroll, PayrollSettings.current(), BusinessProfile.current())
    return render_payslip(data), payslip_filename(data.employee_code, data.month_label)
