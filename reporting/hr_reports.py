"""Stage 6 reports: employee advances / loans (Payroll) and department leave (Employees)."""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum

from employee_management.models import AdvanceRecovery, Department, Employee, EmployeeAdvance, EmployeePayroll

from . import periods as P
from . import services as S
from .core import Col, Report, Table

Z = S.ZERO
M, NUM, T = 'money', 'num', 'text'
HALF, ONE = Decimal('0.5'), Decimal('1')
ADV_BASIS = ('As at the period end: principal + interest - recoveries on finalized payslips (payroll month ended) '
             '- repayments - write-offs')


def _i(params, k):
    v = str(params.get(k) or '').strip()
    return int(v) if v.isdigit() else None


def _dept_filter(p, r):
    d = Department.objects.filter(pk=_i(p, 'department')).first() if _i(p, 'department') else None
    if d:
        r.filters.append(('Department', d.name))
    return d


def outstanding_as_at(a, d):
    if a.status == a.CANCELLED or a.date > d:
        return Z
    rec = sum((x.amount for x in a.recoveries.filter(payroll__status=EmployeePayroll.FINALIZED,
                                                     payroll__is_current=True).select_related('payroll__period')
               if x.payroll.period.end <= d), Z)
    rep = a.repayments.filter(cancelled=False, date__lte=d).aggregate(s=Sum('amount'))['s'] or Z
    wo = a.written_off_amount if a.written_off_on and a.written_off_on <= d else Z
    return S.q2(a.total - rec - rep - wo)


def _advances(p, r):
    qs = EmployeeAdvance.objects.exclude(status=EmployeeAdvance.CANCELLED).select_related('employee__department')
    if _i(p, 'employee'):
        qs = qs.filter(employee_id=_i(p, 'employee'))
        e = Employee.objects.filter(pk=_i(p, 'employee')).first()
        if e:
            r.filters.append(('Employee', f'{e.employee_id} {e.full_name}'))
    d = _dept_filter(p, r)
    if d:
        qs = qs.filter(employee__department=d)
    return qs.order_by('employee__employee_id', 'date', 'id')


def advances_outstanding(p, period):
    r = Report('advances_outstanding', 'Employee Advances & Loans Outstanding', 'Payroll', ADV_BASIS, period,
               landscape=True)
    asat = min(period.end, P.today())
    t = Table(f'Advances outstanding as at {asat:%d-%m-%Y}', [
        Col('Emp ID'), Col('Name'), Col('Department'), Col('#'), Col('Type'), Col('Date', 'date'),
        Col('Principal', M), Col('Interest', M), Col('Instalment', M), Col('Status'), Col('Outstanding', M)])
    by_emp = defaultdict(lambda: [None, 0, Z])
    for a in _advances(p, r).filter(date__lte=asat):
        o = outstanding_as_at(a, asat)
        if o <= 0:
            continue
        e = a.employee
        t.add([e.employee_id, e.full_name, e.department.name, str(a.pk), a.get_kind_display(), a.date, a.principal,
               a.interest_amount, S.q2(a.instalment), a.get_status_display(), o])
        x = by_emp[e.pk]
        x[0], x[1], x[2] = e, x[1] + 1, x[2] + o
    t.sum_total()
    s = Table('By employee', [Col('Emp ID'), Col('Name'), Col('Department'), Col('Open advances', 'int'),
                              Col('Outstanding', M)])
    for e, n, o in sorted(by_emp.values(), key=lambda x: x[0].employee_id):
        s.add([e.employee_id, e.full_name, e.department.name, n, S.q2(o)])
    s.sum_total()
    r.card('Total outstanding', S.q2(sum((x[2] for x in by_emp.values()), Z)))
    r.card('Employees with advances', len(by_emp), 'int')
    r.tables += [s, t]
    r.notes.append('Ledger: account mapped as payroll.advance (Employee Advances), party-tagged per employee.')
    return r


def advance_schedule(p, period):
    from employee_management.advances import projected_schedule
    r = Report('advance_schedule', 'Advance / Loan Recovery Schedule', 'Payroll',
               'Recoveries on finalized payslips in the period; projection from the next unrecovered month', period,
               landscape=True)
    advs = list(_advances(p, r))
    done = Table('Recovered through payroll in the period', [
        Col('Month'), Col('Emp ID'), Col('Name'), Col('#'), Col('Type'), Col('Recovered', M)])
    recs = (AdvanceRecovery.objects.filter(advance__in=advs, payroll__status=EmployeePayroll.FINALIZED,
                                           payroll__is_current=True)
            .select_related('advance__employee', 'payroll__period')
            .order_by('payroll__period__year', 'payroll__period__month', 'advance__employee__employee_id'))
    for x in recs:
        if not (period.start <= x.payroll.period.start <= period.end):
            continue
        a = x.advance
        done.add([x.payroll.period.label, a.employee.employee_id, a.employee.full_name, str(a.pk), a.get_kind_display(),
                  S.q2(x.amount)])
    done.sum_total()
    proj = Table('Projected schedule (active advances)', [
        Col('Month'), Col('Emp ID'), Col('Name'), Col('#'), Col('Instalment', M), Col('Balance after', M)])
    rows = []
    for a in advs:
        if a.status != a.ACTIVE:
            continue
        for s in projected_schedule(a):
            rows.append((s['date'], a.employee.employee_id, a.pk, [s['date'].strftime('%b %Y'), a.employee.employee_id,
                                                                   a.employee.full_name, str(a.pk), s['amount'],
                                                                   s['balance']]))
    for *_, row in sorted(rows, key=lambda x: x[:3]):
        proj.add(row)
    proj.note = 'Projection assumes the full instalment each month (actual recovery is capped at net pay).'
    r.tables += [done, proj]
    return r


def leave_dept(p, period):
    from attendance.models import AttendanceRecord, LeaveBalance, LeaveType
    r = Report('leave_dept', 'Department-wise Leave Report', 'Employees',
               'Attendance date in the period (Leave = 1 day, Half day with leave type = 0.5 day)', period,
               landscape=True)
    d = _dept_filter(p, r)
    types = list(LeaveType.objects.order_by('code'))
    recs = AttendanceRecord.objects.filter(date__range=(period.start, period.end), leave_type__isnull=False,
                                           status__in=[AttendanceRecord.LEAVE, AttendanceRecord.HALF_DAY])
    if d:
        recs = recs.filter(employee__department=d)
    agg = defaultdict(lambda: defaultdict(lambda: Z))
    emps = defaultdict(set)
    for dep, lt, st, e in recs.values_list('employee__department__name', 'leave_type_id', 'status', 'employee_id'):
        agg[dep][lt] += ONE if st == AttendanceRecord.LEAVE else HALF
        emps[dep].add(e)
    t = Table('Leave taken by department and type (days)', [Col('Department'), Col('Employees on leave', 'int')] +
              [Col(f'{x.code} ({"paid" if x.paid else "unpaid"})', NUM) for x in types] + [Col('Total', NUM)])
    for dep in sorted(agg):
        vals = [agg[dep][x.pk] for x in types]
        t.add([dep, len(emps[dep])] + vals + [sum(vals, Z)])
    if t.rows:
        t.total = ['Total', len(set().union(*emps.values()))] + [sum((row[i] for row in t.rows), Z)
                                                                for i in range(2, len(t.cols))]
    years = list(range(period.start.year, period.end.year + 1))
    b = Table(f'Leave balance summary ({", ".join(map(str, years))})', [
        Col('Year', 'int'), Col('Department'), Col('Leave type'), Col('Employees', 'int'), Col('Opening', NUM),
        Col('Accrued', NUM), Col('Used', NUM), Col('Balance', NUM)])
    bq = LeaveBalance.objects.filter(year__in=years).select_related('employee__department', 'leave_type')
    if d:
        bq = bq.filter(employee__department=d)
    bal = defaultdict(lambda: [set(), Z, Z, Z])
    for x in bq:
        k = (x.year, x.employee.department.name, x.leave_type.name)
        v = bal[k]
        v[0].add(x.employee_id)
        v[1] += x.opening
        v[2] += x.accrued
        v[3] += x.used
    for (y, dep, lt), (es, o, a, u) in sorted(bal.items()):
        b.add([y, dep, lt, len(es), o, a, u, o + a - u])
    r.card('Leave days taken', sum((sum(v.values(), Z) for v in agg.values()), Z), 'num')
    r.tables += [t, b]
    return r


PAYROLL_ITEMS = [('advances_outstanding', 'Advances & Loans Outstanding', advances_outstanding),
                 ('advance_schedule', 'Advance Recovery Schedule', advance_schedule)]
EMPLOYEE_ITEMS = [('leave_dept', 'Department-wise Leave', leave_dept)]
