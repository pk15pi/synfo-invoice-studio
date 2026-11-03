"""Salary calculation and payroll lifecycle.

Every amount is a Decimal rounded with ROUND_HALF_UP to 2 decimals at component level.
Formulas (monthly fixed):
    earned component = monthly component x payable days / divisor days
    pay_full_month   : earned component = monthly - monthly / divisor x (LWP + unpaid leave)
Daily  : rate x payable days      Hourly : rate x approved hours      Contract : fixed rate
Then + overtime + incentives + additional earnings.
Employee deductions reduce net; employer contributions are recorded but never reduce net.
"""
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (DEDUCTION_LABELS, EARNING_LABELS, UNPRORATED_EARNINGS, ZERO, AdvanceRecovery, Employee,
                     EmployeePayroll,
                     EmployeeSalaryStructure, PayrollDeduction, PayrollEarning, PayrollPeriod, PayrollSettings,
                     SalaryComponent, StatutoryRule)
from .validators import mask

CENT = Decimal('0.01')


def q2(x):
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


def D(x):
    if x in (None, ''):
        return ZERO
    return Decimal(str(x))


@dataclass
class Line:
    code: str
    label: str
    amount: Decimal
    side: str = 'employee'


@dataclass
class Calc:
    employee: Employee
    period: PayrollPeriod
    structure: EmployeeSalaryStructure = None
    calendar_days: int = 30
    divisor_days: Decimal = ZERO
    working_days: Decimal = ZERO
    payable_days: Decimal = ZERO
    lwp_days: Decimal = ZERO
    paid_leave_days: Decimal = ZERO
    unpaid_leave_days: Decimal = ZERO
    approved_hours: Decimal = ZERO
    overtime_amount: Decimal = ZERO
    incentive_amount: Decimal = ZERO
    additional_earnings: Decimal = ZERO
    additional_deductions: Decimal = ZERO
    remarks: str = ''
    monthly_salary: Decimal = ZERO
    earnings: list = field(default_factory=list)
    deductions: list = field(default_factory=list)
    employer: list = field(default_factory=list)
    gross: Decimal = ZERO
    total_deductions: Decimal = ZERO
    employer_total: Decimal = ZERO
    rounding_adjustment: Decimal = ZERO
    net: Decimal = ZERO
    errors: list = field(default_factory=list)
    advance_recovery_override: object = None  # None = automatic
    recoveries: list = field(default_factory=list)  # [(EmployeeAdvance, amount)]


def period_divisor(period, structure=None):
    if structure is not None and structure.divisor != 'default':
        return Decimal(30) if structure.divisor == PayrollSettings.DIV_FIXED30 else Decimal(period.calendar_days)
    return period.divisor_days


def eligible_employees(period):
    """Joined on/before month end, not left before month start, has an applicable structure."""
    emps = (Employee.objects.filter(date_of_joining__lte=period.end)
            .filter(Q(date_of_leaving__isnull=True) | Q(date_of_leaving__gte=period.start))
            .select_related('department').order_by('employee_id'))
    out = []
    for e in emps:
        if EmployeeSalaryStructure.applicable(e, period.start, period.end):
            out.append(e)
    return out


def default_days(employee, period, divisor):
    """Days in service within the month, expressed against the divisor."""
    start = max(period.start, employee.date_of_joining)
    end = min(period.end, employee.date_of_leaving) if employee.date_of_leaving else period.end
    if end < start:
        return ZERO
    in_service = Decimal((end - start).days + 1)
    if in_service == period.calendar_days:
        return divisor
    if divisor == 30 and in_service > 30:
        return Decimal(30)
    return in_service


def calculate(employee, period, inputs=None, settings=None):
    settings = settings or PayrollSettings.current()
    inputs = inputs or {}
    st = EmployeeSalaryStructure.applicable(employee, period.start, period.end)
    c = Calc(employee=employee, period=period, structure=st, calendar_days=period.calendar_days)
    if st is None:
        c.errors.append('No salary structure is effective for this month.')
        return c
    divisor = period_divisor(period, st)
    c.divisor_days = divisor
    for f in ('lwp_days', 'paid_leave_days', 'unpaid_leave_days', 'approved_hours', 'overtime_amount',
              'incentive_amount', 'additional_earnings', 'additional_deductions'):
        setattr(c, f, D(inputs.get(f)))
    c.remarks = inputs.get('remarks', '') or ''
    in_service = default_days(employee, period, divisor)
    c.working_days = D(inputs['working_days']) if inputs.get('working_days') not in (None, '') else in_service
    if inputs.get('payable_days') not in (None, ''):
        c.payable_days = D(inputs['payable_days'])
    else:
        c.payable_days = max(ZERO, c.working_days - c.lwp_days - c.unpaid_leave_days)
    for f in ('payable_days', 'lwp_days', 'unpaid_leave_days', 'paid_leave_days', 'approved_hours'):
        if getattr(c, f) < 0:
            c.errors.append(f'{f.replace("_", " ").capitalize()} cannot be negative.')
    if c.payable_days > divisor and st.structure_type == st.MONTHLY:
        c.errors.append(f'Payable days ({c.payable_days}) cannot exceed the divisor ({divisor}).')
    if c.errors:
        return c

    comps = list(st.components.all())
    earn = [x for x in comps if x.kind == SalaryComponent.EARNING]
    ded = [x for x in comps if x.kind == SalaryComponent.DEDUCTION]
    c.monthly_salary = q2(sum((x.amount for x in earn), ZERO))
    full_service = in_service == divisor

    if st.structure_type == st.MONTHLY:
        absent = c.lwp_days + c.unpaid_leave_days
        for x in earn:
            if x.code in UNPRORATED_EARNINGS:
                amt = q2(x.amount)
            elif st.pay_full_month and full_service and inputs.get('payable_days') in (None, ''):
                amt = q2(x.amount - x.amount / divisor * absent)
            else:
                amt = q2(x.amount * c.payable_days / divisor) if divisor else ZERO
            c.earnings.append(Line(x.code, EARNING_LABELS.get(x.code, x.code), max(amt, ZERO)))
    else:
        if st.structure_type == st.DAILY:
            base = q2(st.rate * c.payable_days)
            label = f'Daily wage ({c.payable_days} days x {st.rate})'
        elif st.structure_type == st.HOURLY:
            base = q2(st.rate * c.approved_hours)
            label = f'Hourly wage ({c.approved_hours} h x {st.rate})'
        else:
            base = q2(st.rate)
            label = 'Contract payment'
        c.monthly_salary = q2(st.rate)
        c.earnings.append(Line('basic', label, base))
        for x in earn:  # fixed extras on non-monthly structures are paid as-is
            c.earnings.append(Line(x.code, EARNING_LABELS.get(x.code, x.code), q2(x.amount)))

    for code, amt in (('overtime', c.overtime_amount), ('incentive', c.incentive_amount),
                      ('other_earning', c.additional_earnings)):
        if amt:
            label = 'Additional earnings' if code == 'other_earning' else EARNING_LABELS[code]
            c.earnings.append(Line(code + '_input', label, q2(amt)))
    c.gross = q2(sum((x.amount for x in c.earnings), ZERO))
    basic = sum((x.amount for x in c.earnings if x.code == 'basic'), ZERO)

    statutory_codes = set()
    if st.use_statutory_rules:
        on = period.end
        for r in StatutoryRule.objects.filter(active=True, effective_from__lte=on).filter(
                Q(effective_to__isnull=True) | Q(effective_to__gte=on)):
            if r.applies_if_gross_upto is not None and c.gross > r.applies_if_gross_upto:
                continue
            if r.applies_if_gross_from is not None and c.gross < r.applies_if_gross_from:
                continue
            if r.calculation == r.FIXED:
                amt = q2(r.rate)
            else:
                b = basic if r.base == 'basic' else c.gross
                if r.base_ceiling is not None:
                    b = min(b, r.base_ceiling)
                amt = q2(b * r.rate / Decimal(100))
            line = Line(r.code, r.name, amt, r.side)
            (c.deductions if r.side == r.EMPLOYEE else c.employer).append(line)
            if r.side == r.EMPLOYEE:
                statutory_codes.add(r.code)
    for x in ded:
        if x.code in statutory_codes:
            continue  # rule already computed it
        if st.use_statutory_rules and x.code in ('pf', 'esi', 'professional_tax', 'tds'):
            continue
        if x.amount:
            c.deductions.append(Line(x.code, DEDUCTION_LABELS.get(x.code, x.code), q2(x.amount)))
    if c.additional_deductions:
        c.deductions.append(Line('other_deduction_input', 'Additional deductions', q2(c.additional_deductions)))
    # Stage 6: advance / loan recovery (only when non-zero, so payslips without advances are unchanged)
    from .advances import plan_recovery
    c.advance_recovery_override = inputs.get('advance_recovery_override')
    available = c.gross - q2(sum((x.amount for x in c.deductions), ZERO))
    c.recoveries, rec_errors = plan_recovery(employee, period, available, c.advance_recovery_override)
    c.errors.extend(rec_errors)
    rec_total = q2(sum((a for _, a in c.recoveries), ZERO))
    if rec_total:
        c.deductions.append(Line('advance_recovery', 'Advance recovery', rec_total))
    c.total_deductions = q2(sum((x.amount for x in c.deductions), ZERO))
    c.employer_total = q2(sum((x.amount for x in c.employer), ZERO))
    net = c.gross - c.total_deductions
    if settings.rounding == PayrollSettings.ROUND_RUPEE:
        rounded = net.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        c.rounding_adjustment = q2(rounded - net)
        net = rounded
    c.net = q2(net)
    if c.net < 0:
        c.errors.append('Deductions exceed earnings: net salary would be negative.')
    return c


def _snapshot(p, c):
    e = c.employee
    bank = e.current_bank
    p.structure = c.structure
    p.bank_account = bank
    p.snap_employee_code = e.employee_id
    p.snap_name = e.full_name
    p.snap_department = e.department.name
    p.snap_designation = e.designation
    p.snap_date_of_joining = e.date_of_joining
    p.snap_pan_masked = mask(e.pan_number)
    p.snap_bank_name = bank.bank_name if bank else ''
    p.snap_account_number = bank.account_number if bank else ''
    p.snap_ifsc = bank.ifsc_code if bank else ''
    p.snap_structure_type = c.structure.structure_type
    p.snap_rate = c.structure.rate


def write_draft(c, payroll=None, revises=None, reason='', source=None, snapshot=None):
    """Create or update a DRAFT EmployeePayroll from a Calc. `source`/`snapshot` record where the inputs came from."""
    if c.errors:
        raise ValueError('; '.join(c.errors))
    with transaction.atomic():
        p = payroll or EmployeePayroll(period=c.period, employee=c.employee)
        if p.pk and p.is_finalized:
            raise ValueError('Finalized payroll is read-only.')
        if revises is not None:
            p.revises = revises
            p.revision = (EmployeePayroll.objects.filter(period=c.period, employee=c.employee)
                          .order_by('-revision').values_list('revision', flat=True).first() or 0) + 1
            p.is_current = False  # becomes current only when finalized
            p.revision_reason = reason
        for f in ('calendar_days', 'divisor_days', 'working_days', 'payable_days', 'lwp_days', 'paid_leave_days',
                  'unpaid_leave_days', 'approved_hours', 'overtime_amount', 'incentive_amount',
                  'additional_earnings', 'additional_deductions', 'remarks', 'monthly_salary',
                  'rounding_adjustment'):
            setattr(p, f, getattr(c, f))
        p.gross_earnings, p.total_deductions = c.gross, c.total_deductions
        p.employer_contributions, p.net_salary = c.employer_total, c.net
        p.advance_recovery_override = c.advance_recovery_override
        _snapshot(p, c)
        if source is not None:
            p.input_source, p.attendance_snapshot = source, snapshot or ''
        p.save()
        p.earnings.all().delete()
        p.deductions.all().delete()
        for i, l in enumerate(c.earnings):
            PayrollEarning.objects.create(payroll=p, code=l.code, label=l.label, amount=l.amount, order=i)
        for i, l in enumerate(c.deductions + c.employer):
            PayrollDeduction.objects.create(payroll=p, code=l.code, label=l.label, amount=l.amount, order=i,
                                            side=l.side)
        p.advance_recoveries.all().delete()
        for adv, amt in c.recoveries:
            AdvanceRecovery.objects.create(advance=adv, payroll=p, amount=amt)
        return p


ATTENDANCE_FIELDS = ('lwp_days', 'unpaid_leave_days', 'paid_leave_days')


def attendance_inputs(employee, period):
    """Payroll inputs from the attendance module, or (None, summary) when the month has no attendance data."""
    try:
        from attendance.services import payroll_inputs
    except ImportError:  # attendance app not installed
        return None, None
    return payroll_inputs(employee, period)


def source_for(p, inputs):
    """'attendance' if the inputs still match the attendance snapshot, else 'override' (manual if none)."""
    import json
    if not p.attendance_snapshot:
        return EmployeePayroll.SOURCE_MANUAL
    snap = json.loads(p.attendance_snapshot)
    want = {'lwp_days': snap['lwp'], 'unpaid_leave_days': snap['absent'], 'paid_leave_days': snap['paid_leave']}
    same = all(D(inputs.get(k) or 0) == D(v) for k, v in want.items()) and \
        not inputs.get('working_days') and not inputs.get('payable_days')
    return EmployeePayroll.SOURCE_ATTENDANCE if same else EmployeePayroll.SOURCE_OVERRIDE


def refresh_from_attendance(p, settings=None):
    """Re-pull LWP / leave from attendance into a draft (other inputs kept). Returns (payroll|None, calc|None)."""
    if p.is_finalized:
        raise ValueError('Finalized payroll is read-only.')
    att, summary = attendance_inputs(p.employee, p.period)
    if att is None:
        return None, None
    inputs = {f: getattr(p, f) for f in EmployeePayroll.INPUT_FIELDS}
    inputs.update(att, working_days=None, payable_days=None)
    c = calculate(p.employee, p.period, inputs, settings)
    if c.errors:
        return None, c
    return write_draft(c, payroll=p, source=EmployeePayroll.SOURCE_ATTENDANCE, snapshot=summary.as_json()), c


def generate(period, inputs_by_employee=None, settings=None):
    """Create draft payroll for every eligible employee that has no current record yet.
    Returns (created, skipped:list[(employee, reason)])."""
    inputs_by_employee = inputs_by_employee or {}
    created, skipped = [], []
    for e in eligible_employees(period):
        if EmployeePayroll.objects.filter(period=period, employee=e, is_current=True).exists():
            skipped.append((e, 'already has payroll for this month'))
            continue
        inputs, source, snap = inputs_by_employee.get(e.pk), EmployeePayroll.SOURCE_MANUAL, ''
        if inputs is None:
            att, summary = attendance_inputs(e, period)
            if att is not None:
                inputs, source, snap = att, EmployeePayroll.SOURCE_ATTENDANCE, summary.as_json()
        c = calculate(e, period, inputs, settings)
        if c.errors:
            skipped.append((e, '; '.join(c.errors)))
            continue
        created.append(write_draft(c, source=source, snapshot=snap))
    return created, skipped


def recalculate(p, inputs, settings=None):
    c = calculate(p.employee, p.period, inputs, settings)
    if c.errors:
        return None, c
    return write_draft(c, payroll=p), c


def finalize(payrolls):
    """Atomically freeze drafts. A finalized revision replaces the current record."""
    now = timezone.now()
    done = []
    with transaction.atomic():
        for p in payrolls:
            p = EmployeePayroll.objects.select_for_update().get(pk=p.pk)
            if p.is_finalized:
                continue
            if p.revises_id:
                EmployeePayroll.objects.filter(period=p.period, employee=p.employee, is_current=True).exclude(
                    pk=p.pk).update(is_current=False)
                p.is_current = True
            p.status, p.finalized_at = EmployeePayroll.FINALIZED, now
            p.save()
            done.append(p)
        period = payrolls[0].period if payrolls else None
        if period and not period.payrolls.filter(status=EmployeePayroll.DRAFT).exists():
            period.status, period.finalized_at = PayrollPeriod.FINALIZED, now
            period.save()
        if period and done:
            # Stage 4: one ledger entry per month (idempotent; a revision reverses and reposts it).
            from accounting.postings import post_payroll
            post_payroll(period)
            from .advances import refresh_status
            refresh_status(employees=[p.employee_id for p in done])
    return done


def start_revision(original, reason):
    if not original.is_finalized:
        raise ValueError('Only finalized payroll needs a revision; edit the draft directly.')
    if EmployeePayroll.objects.filter(revises=original, status=EmployeePayroll.DRAFT).exists():
        return EmployeePayroll.objects.get(revises=original, status=EmployeePayroll.DRAFT)
    inputs = {f: getattr(original, f) for f in EmployeePayroll.INPUT_FIELDS}
    c = calculate(original.employee, original.period, inputs)
    if c.errors:
        raise ValueError('; '.join(c.errors))
    with transaction.atomic():
        period = original.period
        if period.status == PayrollPeriod.FINALIZED:
            period.status = PayrollPeriod.DRAFT
            period.save()
        return write_draft(c, revises=original, reason=reason, source=original.input_source,
                           snapshot=original.attendance_snapshot)


def get_or_create_period(year, month, settings=None):
    settings = settings or PayrollSettings.current()
    return PayrollPeriod.objects.get_or_create(year=year, month=month,
                                               defaults={'divisor': settings.default_divisor})[0]


def expected_payment_date(period):
    return period.payment_date or (period.end + timedelta(days=1))
