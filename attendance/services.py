"""Attendance calculations, leave workflow and the payroll bridge.

Payroll integration: `payroll_inputs(employee, period)` returns the dict that the payroll
engine's single input point (`employee_management.payroll.calculate(inputs=...)`) accepts,
or None when the month has no attendance data (payroll then stays manual, as before).
"""
import csv
import io
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from .models import (ZERO, AttendanceLockedError, AttendanceRecord, AttendanceSettings, Holiday, LeaveApplication,
                     LeaveBalance, LeaveType, daterange, month_bounds, now)

HALF = Decimal('0.5')
ONE = Decimal('1')


# ------------------------------------------------------------------ lock
def is_locked(employee, d):
    from employee_management.models import EmployeePayroll
    qs = EmployeePayroll.objects.filter(employee=employee, period__year=d.year, period__month=d.month)
    if not qs.filter(is_current=True, status=EmployeePayroll.FINALIZED).exists():
        return False
    return not qs.filter(status=EmployeePayroll.DRAFT, revises__isnull=False).exists()


def assert_unlocked(employee, d):
    if is_locked(employee, d):
        raise AttendanceLockedError(
            f'Attendance for {employee.employee_id} in {d:%B %Y} is locked because payroll is finalized. '
            'Start a payroll revision first, then change attendance and refresh the revision from attendance.')


def needs_reason(employee, d):
    """A day is 'finalized' for attendance once payroll for its month has been generated (any record);
    changing it then needs a reason and is kept in AttendanceCorrection history."""
    from employee_management.models import EmployeePayroll
    return EmployeePayroll.objects.filter(employee=employee, period__year=d.year, period__month=d.month).exists()


def record_correction(rec, before, after, reason, check_only=False):
    from .models import AttendanceCorrection
    if not needs_reason(rec.employee, rec.date):
        return None
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError(f'{rec.employee.employee_id} {rec.date:%d-%m-%Y}: payroll already exists for '
                              f'{rec.date:%B %Y}; give a reason for this attendance correction.')
    if check_only:
        return None
    return AttendanceCorrection.objects.create(employee=rec.employee, date=rec.date, old_value=before[:200],
                                               new_value=after[:200], reason=reason[:240])


# ------------------------------------------------------------------ calendar
def holiday_map(start, end):
    return {h.date: h.name for h in Holiday.objects.filter(date__range=(start, end), optional=False)}


def service_range(employee, start, end):
    a = max(start, employee.date_of_joining)
    b = min(end, employee.date_of_leaving) if employee.date_of_leaving else end
    return a, b


def default_status(d, holidays, settings):
    if d in holidays:
        return AttendanceRecord.HOLIDAY
    if settings.is_week_off(d):
        return AttendanceRecord.WEEK_OFF
    return None


def is_working_day(d, holidays, settings):
    return default_status(d, holidays, settings) is None


# ------------------------------------------------------------------ monthly summary
@dataclass
class MonthSummary:
    employee_id: int
    year: int
    month: int
    calendar_days: int = 0
    days_in_service: Decimal = ZERO
    working_days: Decimal = ZERO
    present: Decimal = ZERO
    half_days: int = 0
    paid_leave: Decimal = ZERO
    lwp: Decimal = ZERO
    absent: Decimal = ZERO
    holidays: Decimal = ZERO
    week_offs: Decimal = ZERO
    unmarked: int = 0
    payable_days: Decimal = ZERO
    records: int = 0
    leave_by_type: dict = field(default_factory=dict)

    @property
    def has_data(self):
        return self.records > 0

    def as_json(self):
        d = asdict(self)
        return json.dumps({k: str(v) if isinstance(v, Decimal) else v for k, v in d.items()}, sort_keys=True,
                          default=str)


def month_summary(employee, year, month, settings=None, holidays=None, records=None):
    settings = settings or AttendanceSettings.current()
    start, end = month_bounds(year, month)
    holidays = holiday_map(start, end) if holidays is None else holidays
    if records is None:
        records = {r.date: r for r in AttendanceRecord.objects.filter(employee=employee, date__range=(start, end))
                   .select_related('leave_type')}
    s = MonthSummary(employee_id=employee.pk, year=year, month=month, calendar_days=(end - start).days + 1)
    a, b = service_range(employee, start, end)
    by_type = defaultdict(lambda: ZERO)
    for d in daterange(a, b):
        s.days_in_service += ONE
        r = records.get(d)
        st = r.status if r else default_status(d, holidays, settings)
        if r:
            s.records += 1
        if st is None:
            s.unmarked += 1
            st = AttendanceRecord.PRESENT
        if st == AttendanceRecord.PRESENT:
            s.present += ONE
        elif st == AttendanceRecord.ABSENT:
            s.absent += ONE
        elif st == AttendanceRecord.HOLIDAY:
            s.holidays += ONE
        elif st == AttendanceRecord.WEEK_OFF:
            s.week_offs += ONE
        elif st == AttendanceRecord.LEAVE:
            lt = r.leave_type
            by_type[lt.code] += ONE
            if lt.paid:
                s.paid_leave += ONE
            else:
                s.lwp += ONE
        elif st == AttendanceRecord.HALF_DAY:
            s.half_days += 1
            s.present += HALF
            lt = r.leave_type
            if lt is None:
                s.absent += HALF
            else:
                by_type[lt.code] += HALF
                if lt.paid:
                    s.paid_leave += HALF
                else:
                    s.lwp += HALF
    s.working_days = s.days_in_service - s.holidays - s.week_offs
    s.payable_days = s.days_in_service - s.absent - s.lwp
    s.leave_by_type = {k: str(v) for k, v in sorted(by_type.items())}
    return s


def payroll_inputs(employee, period):
    """(inputs, summary) for the payroll engine, or (None, summary) when there is no attendance data.
    Absent days (incl. unpaid half days) are fed as unpaid leave, so the engine's existing
    formula (working - LWP - unpaid) and the month's divisor (actual / fixed 30) still apply."""
    s = month_summary(employee, period.year, period.month)
    if not s.has_data:
        return None, s
    return {'lwp_days': s.lwp, 'unpaid_leave_days': s.absent, 'paid_leave_days': s.paid_leave}, s


# ------------------------------------------------------------------ leave
class LeaveError(Exception):
    pass


def leave_days_by_year(app, settings=None):
    settings = settings or AttendanceSettings.current()
    holidays = holiday_map(app.start_date, app.end_date)
    a, b = service_range(app.employee, app.start_date, app.end_date)
    out = defaultdict(lambda: ZERO)
    dates = [d for d in daterange(a, b) if is_working_day(d, holidays, settings)]
    for d in dates:
        out[d.year] += HALF if app.half_day else ONE
    return dict(out), dates


def overlapping(app):
    return (LeaveApplication.objects.filter(employee=app.employee, status__in=LeaveApplication.OPEN,
                                            start_date__lte=app.end_date, end_date__gte=app.start_date)
            .exclude(pk=app.pk))


def check_application(app):
    """Raise LeaveError for overlaps / insufficient balance; returns (days_by_year, dates)."""
    if overlapping(app).exists():
        o = overlapping(app).first()
        raise LeaveError(f'Overlaps another {o.get_status_display().lower()} application '
                         f'({o.start_date:%d-%m-%Y} to {o.end_date:%d-%m-%Y}).')
    by_year, dates = leave_days_by_year(app)
    if not dates:
        raise LeaveError('The selected dates contain no working days (holidays / week offs only).')
    if app.leave_type.needs_balance:
        for y, n in by_year.items():
            bal = LeaveBalance.for_year(app.employee, app.leave_type, y)
            if bal.balance < n:
                raise LeaveError(f'Insufficient {app.leave_type.name} balance for {y}: '
                                 f'{bal.balance} available, {n} requested.')
    return by_year, dates


def save_application(app):
    by_year, _ = check_application(app)
    app.days = sum(by_year.values(), ZERO)
    app.save()
    return app


@transaction.atomic
def approve(app):
    app = LeaveApplication.objects.select_for_update().get(pk=app.pk)
    if app.status != LeaveApplication.PENDING:
        raise LeaveError('Only pending applications can be approved.')
    by_year, dates = check_application(app)
    for d in dates:
        assert_unlocked(app.employee, d)
    for d in dates:
        r = AttendanceRecord.objects.filter(employee=app.employee, date=d).first() or AttendanceRecord(
            employee=app.employee, date=d)
        r.status = AttendanceRecord.HALF_DAY if app.half_day else AttendanceRecord.LEAVE
        r.leave_type, r.application = app.leave_type, app
        r.note = f'Leave application #{app.pk}'
        r.correction_reason = f'Leave application #{app.pk} approved'
        r.save()
    if app.leave_type.needs_balance:
        for y, n in by_year.items():
            bal = LeaveBalance.for_year(app.employee, app.leave_type, y)
            bal.used += n
            bal.save()
    app.days = sum(by_year.values(), ZERO)
    app.status, app.decided_at = LeaveApplication.APPROVED, now()
    app.save()
    return app


@transaction.atomic
def reject(app):
    if app.status != LeaveApplication.PENDING:
        raise LeaveError('Only pending applications can be rejected.')
    app.status, app.decided_at = LeaveApplication.REJECTED, now()
    app.save()
    return app


@transaction.atomic
def cancel(app):
    app = LeaveApplication.objects.select_for_update().get(pk=app.pk)
    if app.status not in LeaveApplication.OPEN:
        raise LeaveError('This application is already closed.')
    if app.status == LeaveApplication.APPROVED:
        days = list(app.attendance_days.all())
        for r in days:
            assert_unlocked(app.employee, r.date)
        by_year = defaultdict(lambda: ZERO)
        for r in days:
            by_year[r.date.year] += HALF if r.status == AttendanceRecord.HALF_DAY else ONE
            r.correction_reason = f'Leave application #{app.pk} cancelled'
            r.delete()
        if app.leave_type.needs_balance:
            for y, n in by_year.items():
                bal = LeaveBalance.for_year(app.employee, app.leave_type, y)
                bal.used = max(ZERO, bal.used - n)
                bal.save()
    app.status, app.decided_at = LeaveApplication.CANCELLED, now()
    app.save()
    return app


def ensure_balances(year, employees=None):
    from employee_management.models import Employee
    employees = employees if employees is not None else Employee.objects.filter(
        date_of_joining__lte=date(year, 12, 31)).filter(
        Q(date_of_leaving__isnull=True) | Q(date_of_leaving__gte=date(year, 1, 1)))
    n = 0
    for e in employees:
        for lt in LeaveType.objects.filter(active=True):
            if lt.paid:
                LeaveBalance.for_year(e, lt, year)
                n += 1
    return n


@transaction.atomic
def carry_forward(year):
    """Year-end: opening(year+1) = min(balance(year), cap) for carry-forward types, else 0.
    Idempotent: re-running recomputes the opening from the current year's balance."""
    done = 0
    for b in LeaveBalance.objects.filter(year=year).select_related('leave_type', 'employee'):
        lt = b.leave_type
        amt = ZERO
        if lt.carry_forward:
            amt = max(ZERO, b.balance)
            if lt.carry_forward_cap is not None:
                amt = min(amt, lt.carry_forward_cap)
        nb = LeaveBalance.for_year(b.employee, lt, year + 1)
        nb.opening = amt
        nb.save()
        done += 1
    return done


# ------------------------------------------------------------------ CSV
CSV_HEADERS = ['employee_id', 'date', 'status', 'leave_type', 'in_time', 'out_time', 'note']


def employees_in_month(year, month):
    from employee_management.models import Employee
    start, end = month_bounds(year, month)
    return (Employee.objects.filter(date_of_joining__lte=end)
            .filter(Q(date_of_leaving__isnull=True) | Q(date_of_leaving__gte=start)).order_by('employee_id'))


def csv_template(year, month):
    start, end = month_bounds(year, month)
    settings, holidays = AttendanceSettings.current(), holiday_map(start, end)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(CSV_HEADERS)
    for e in employees_in_month(year, month):
        recs = {r.date: r for r in e.attendance.filter(date__range=(start, end)).select_related('leave_type')}
        a, b = service_range(e, start, end)
        for d in daterange(a, b):
            r = recs.get(d)
            if r:
                w.writerow([e.employee_id, d.isoformat(), r.status, r.leave_type.code if r.leave_type else '',
                            r.in_time.strftime('%H:%M') if r.in_time else '',
                            r.out_time.strftime('%H:%M') if r.out_time else '', r.note])
            else:
                w.writerow([e.employee_id, d.isoformat(), default_status(d, holidays, settings) or '', '', '', '', ''])
    return out.getvalue().encode('utf-8-sig')


def _date(s):
    for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError(f'bad date "{s}"')


def _time(s):
    s = (s or '').strip()
    if not s:
        return None
    for fmt in ('%H:%M', '%H:%M:%S', '%I:%M %p'):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            pass
    raise ValueError(f'bad time "{s}"')


def import_csv(data, year=None, month=None, reason=''):
    """All-or-nothing import. Returns (saved_count, errors). Blank status rows are skipped.
    Leave rows set attendance only; balances move only through leave applications."""
    from employee_management.models import Employee
    if isinstance(data, bytes):
        data = data.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(data))
    missing = [h for h in ('employee_id', 'date', 'status') if h not in (reader.fieldnames or [])]
    if missing:
        return 0, [f'Missing column(s): {", ".join(missing)}']
    emps = {e.employee_id.upper(): e for e in Employee.objects.all()}
    types = {t.code.upper(): t for t in LeaveType.objects.all()}
    valid = dict(AttendanceRecord.STATUSES)
    rows, errors = [], []
    for i, row in enumerate(reader, start=2):
        st = (row.get('status') or '').strip().upper()
        if not st:
            continue
        try:
            e = emps.get((row.get('employee_id') or '').strip().upper())
            if e is None:
                raise ValueError(f'unknown employee "{row.get("employee_id")}"')
            d = _date(row.get('date') or '')
            if year and month and (d.year, d.month) != (int(year), int(month)):
                raise ValueError(f'date {d} is outside the selected month')
            if st not in valid:
                raise ValueError(f'bad status "{st}" (use {", ".join(valid)})')
            lt = None
            code = (row.get('leave_type') or '').strip().upper()
            if code:
                lt = types.get(code)
                if lt is None:
                    raise ValueError(f'unknown leave type "{code}"')
            if st == 'L' and lt is None:
                raise ValueError('leave rows need a leave_type')
            if d < e.date_of_joining or (e.date_of_leaving and d > e.date_of_leaving):
                raise ValueError('date is outside the employee\'s service')
            if is_locked(e, d):
                raise ValueError('month is locked (payroll finalized)')
            if not (reason or '').strip() and needs_reason(e, d):
                raise ValueError(f'payroll already exists for {d:%B %Y}; give a correction reason')
            rows.append((e, d, st, lt, _time(row.get('in_time')), _time(row.get('out_time')),
                         (row.get('note') or '').strip()[:160]))
        except ValueError as ex:
            errors.append(f'Row {i}: {ex}')
    if errors:
        return 0, errors
    with transaction.atomic():
        for e, d, st, lt, tin, tout, note in rows:
            r = AttendanceRecord.objects.filter(employee=e, date=d).first() or AttendanceRecord(employee=e, date=d)
            r.status, r.leave_type, r.in_time, r.out_time, r.note = st, lt, tin, tout, note
            r.correction_reason = reason
            r.save()
    return len(rows), []


# ------------------------------------------------------------------ register
SUMMARY_HEADERS = ['Working Days', 'Present', 'Half Days', 'Paid Leave', 'LWP', 'Absent', 'Holidays', 'Week Offs',
                   'Unmarked', 'Payable Days']


def summary_cells(s):
    return [s.working_days, s.present, s.half_days, s.paid_leave, s.lwp, s.absent, s.holidays, s.week_offs,
            s.unmarked, s.payable_days]


def register(year, month):
    start, end = month_bounds(year, month)
    settings, holidays = AttendanceSettings.current(), holiday_map(start, end)
    days = list(daterange(start, end))
    headers = ['Employee ID', 'Name'] + [f'{d.day}' for d in days] + SUMMARY_HEADERS
    rows = []
    for e in employees_in_month(year, month):
        recs = {r.date: r for r in e.attendance.filter(date__range=(start, end)).select_related('leave_type')}
        a, b = service_range(e, start, end)
        cells = []
        for d in days:
            if d < a or d > b:
                cells.append('-')
            elif d in recs:
                r = recs[d]
                cells.append(r.status + (f':{r.leave_type.code}' if r.leave_type else ''))
            else:
                cells.append((default_status(d, holidays, settings) or 'P') + '*')
        s = month_summary(e, year, month, settings, holidays, recs)
        rows.append([e.employee_id, e.full_name] + cells + summary_cells(s))
    return headers, rows
