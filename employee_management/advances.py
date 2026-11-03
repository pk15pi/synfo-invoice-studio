"""Employee salary advances & loans (Stage 6).

* Disbursement posts Dr employee advances (party employee) / Cr bank (+ Cr interest income for flat interest).
* Payroll recovers automatically: each active advance whose recovery start month has come gets its instalment,
  capped at its outstanding balance and at the net pay still available. The per-payslip override
  (EmployeePayroll.advance_recovery_override) replaces the automatic total and is allocated oldest advance first.
* Only recoveries on CURRENT FINALIZED payslips reduce the outstanding balance; a revision replaces them.
* Outstanding = principal + interest - recoveries - repayments - write-off. Auto-closes at zero.
"""
from collections import defaultdict
from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounting.money import ZERO, q2

from .models import AdvanceRecovery, AdvanceRepayment, EmployeeAdvance, EmployeePayroll


def _postings():
    from accounting import postings
    return postings


# CLOSED is included so a revision of the month that closed an advance still recovers the same amount
OPEN = (EmployeeAdvance.ACTIVE, EmployeeAdvance.CLOSED)


def due_advances(employee, period):
    key = period.year * 12 + period.month
    return [a for a in EmployeeAdvance.objects.filter(employee=employee, status__in=OPEN,
                                                      date__lte=period.end).order_by('date', 'id')
            if a.start_key <= key]


def plan_recovery(employee, period, available, override=None):
    """[(advance, amount)], errors. ``available`` = net pay before the recovery."""
    if not getattr(employee, 'pk', None) or not EmployeeAdvance.objects.filter(
            employee=employee, status__in=OPEN).exists():
        if override not in (None, '') and q2(override) > 0:
            return [], ['Advance recovery override given but the employee has no active advance.']
        return [], []
    advs = [(a, max(q2(a.outstanding(exclude_period=period)), ZERO)) for a in due_advances(employee, period)]
    advs = [(a, o) for a, o in advs if o > 0]
    available = max(q2(available), ZERO)
    out, errors = [], []
    if override not in (None, ''):
        want = q2(override)
        total_out = q2(sum((o for _, o in advs), ZERO))
        if want < 0:
            return [], ['Advance recovery cannot be negative.']
        if want > total_out:
            errors.append(f'Advance recovery {want} exceeds the outstanding advance balance {total_out}.')
        if want > available:
            errors.append(f'Advance recovery {want} exceeds the net pay available ({available}).')
        if errors:
            return [], errors
        for a, o in advs:
            amt = min(o, want)
            if amt > 0:
                out.append((a, amt))
                want -= amt
        return out, []
    for a, o in advs:
        amt = q2(min(a.instalment, o, available))
        if amt > 0:
            out.append((a, amt))
            available -= amt
    return out, []


def refresh_status(employees=None, advances=None):
    """Auto-close fully recovered advances; reopen a closed one if a revision raised its balance again."""
    qs = EmployeeAdvance.objects.filter(status__in=[EmployeeAdvance.ACTIVE, EmployeeAdvance.CLOSED])
    if employees is not None:
        qs = qs.filter(employee_id__in=employees)
    if advances is not None:
        qs = qs.filter(pk__in=[a.pk for a in advances])
    for a in qs:
        o = a.outstanding()
        if a.status == a.ACTIVE and o <= 0:
            a.status, a.closed_on = a.CLOSED, timezone.localdate()
            a.save(update_fields=['status', 'closed_on'])
        elif a.status == a.CLOSED and o > 0:
            a.status, a.closed_on = a.ACTIVE, None
            a.save(update_fields=['status', 'closed_on'])


@transaction.atomic
def disburse(a):
    a.full_clean()
    a.save()
    _postings().post_advance(a)
    return a


def has_recovery(a):
    return a.recoveries.exists() or a.repayments.filter(cancelled=False).exists()


@transaction.atomic
def cancel(a, reason):
    if not (reason or '').strip():
        raise ValidationError('A reason is required to cancel.')
    if a.status != a.ACTIVE:
        raise ValidationError('Only an active advance can be cancelled.')
    if has_recovery(a):
        raise ValidationError('This advance already has recoveries or repayments (or a draft payslip recovering it); '
                              'it cannot be cancelled. Recalculate the draft, or write it off instead.')
    a.status, a.cancel_reason = a.CANCELLED, reason.strip()[:240]
    a.save(update_fields=['status', 'cancel_reason'])
    _postings().post_advance(a)  # posts nothing -> reverses the disbursement
    return a


@transaction.atomic
def repay(a, amount, when=None, bank_account=None, reference=''):
    amount = q2(amount or 0)
    if a.status != a.ACTIVE:
        raise ValidationError('Repayments can only be recorded against an active advance.')
    if amount <= 0:
        raise ValidationError('Amount must be more than zero.')
    if amount > a.outstanding():
        raise ValidationError(f'Amount exceeds the outstanding balance {q2(a.outstanding())}.')
    r = AdvanceRepayment.objects.create(advance=a, date=when or timezone.localdate(), amount=amount,
                                        bank_account=bank_account, reference=reference or '')
    _postings().post_advance_repayment(r)
    refresh_status(advances=[a])
    return r


@transaction.atomic
def write_off(a, reason, when=None):
    if not (reason or '').strip():
        raise ValidationError('A reason is required to write off.')
    if a.status != a.ACTIVE:
        raise ValidationError('Only an active advance can be written off.')
    o = q2(a.outstanding())
    if o <= 0:
        raise ValidationError('Nothing outstanding to write off.')
    a.status, a.write_off_reason = a.WRITTEN_OFF, reason.strip()[:240]
    a.written_off_amount, a.written_off_on = o, when or timezone.localdate()
    a.save(update_fields=['status', 'write_off_reason', 'written_off_amount', 'written_off_on'])
    _postings().post_advance_writeoff(a)
    return a


def statement(a):
    """Rows (date, description, debit, credit, balance) for one advance."""
    ev = []
    if a.status != a.CANCELLED:
        ev.append((a.date, 0, f'Disbursed ({a.get_kind_display()}) {a.reference}'.strip(), a.principal, ZERO))
        if a.interest_amount:
            ev.append((a.date, 1, 'Interest (flat)', a.interest_amount, ZERO))
    for r in a.recoveries_qs().select_related('payroll__period'):
        p = r.payroll
        ev.append((p.period.end, 2, f'Payroll recovery {p.period.label} (r{p.revision})', ZERO, r.amount))
    for r in a.repayments.filter(cancelled=False):
        ev.append((r.date, 3, f'Repayment {r.reference}'.strip(), ZERO, r.amount))
    if a.written_off_amount:
        ev.append((a.written_off_on, 4, f'Written off: {a.write_off_reason}', ZERO, a.written_off_amount))
    ev.sort(key=lambda x: (x[0], x[1]))
    bal, rows = ZERO, []
    for d, _, desc, dr, cr in ev:
        bal += dr - cr
        rows.append({'date': d, 'description': desc, 'debit': dr, 'credit': cr, 'balance': q2(bal)})
    return rows


def pending_draft_recoveries(a):
    return a.recoveries.filter(payroll__status=EmployeePayroll.DRAFT).select_related('payroll__period')


def projected_schedule(a, from_year=None, from_month=None, limit=120):
    """Future instalments (year, month, amount, balance after) from the later of the start month and the month
    after the last recovered payslip. Ignores net-pay caps (a projection)."""
    o = q2(a.outstanding())
    if a.status != a.ACTIVE or o <= 0:
        return []
    last = (a.recoveries_qs().order_by('-payroll__period__year', '-payroll__period__month')
            .values_list('payroll__period__year', 'payroll__period__month').first())
    k = a.start_key
    if last:
        k = max(k, last[0] * 12 + last[1] + 1)
    if from_year:
        k = max(k, from_year * 12 + (from_month or 1))
    rows = []
    while o > 0 and len(rows) < limit:
        y, mth = divmod(k - 1, 12)
        amt = min(q2(a.instalment), o)
        o -= amt
        rows.append({'year': y, 'month': mth + 1, 'date': date(y, mth + 1, 1), 'amount': amt, 'balance': q2(o)})
        k += 1
    return rows


def outstanding_by_employee(as_of=None):
    """{employee_id: (employee, [advances], total outstanding)} for active advances."""
    res = defaultdict(lambda: [None, [], ZERO])
    for a in EmployeeAdvance.objects.filter(status=EmployeeAdvance.ACTIVE).select_related('employee__department'):
        o = q2(a.outstanding())
        r = res[a.employee_id]
        r[0] = a.employee
        r[1].append((a, o))
        r[2] += o
    return res
