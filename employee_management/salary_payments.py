"""Salary payments (Stage 4): partial / multiple payments per payslip, posted Dr salary payable / Cr bank."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from accounting import postings
from accounting.money import ZERO, q2

from .models import EmployeePayroll, SalaryPayment

UNPAID, PARTIAL, PAID = 'unpaid', 'partial', 'paid'


def paid_amount(p):
    """Sum of live payments for this employee + month, across all revisions."""
    t = SalaryPayment.objects.filter(payroll__period_id=p.period_id, payroll__employee_id=p.employee_id,
                                     cancelled=False).aggregate(s=Sum('amount'))['s']
    return q2(t or ZERO)


def status_for(p):
    """{'paid', 'balance', 'status'} for a payslip. Only finalized records carry a balance."""
    paid = paid_amount(p)
    net = q2(p.net_salary) if p.is_finalized else ZERO
    bal = q2(net - paid)
    st = PAID if paid > 0 and bal <= 0 else PARTIAL if paid > 0 else UNPAID
    return {'paid': paid, 'balance': bal, 'status': st, 'overpaid': bal < 0}


def record_payment(p, *, amount, payment_date, bank_account=None, mode='bank', reference='', remarks=''):
    amount = q2(amount or 0)
    with transaction.atomic():
        p = EmployeePayroll.objects.select_for_update().select_related('period', 'employee').get(pk=p.pk)
        if not (p.is_finalized and p.is_current):
            raise ValidationError('Salary can only be paid against the current finalized payslip.')
        if amount <= 0:
            raise ValidationError({'amount': 'Amount must be greater than zero.'})
        if payment_date < p.period.start:
            raise ValidationError({'payment_date': 'Payment date cannot be before the payroll month.'})
        bal = status_for(p)['balance']
        if amount > bal:
            raise ValidationError({'amount': f'Overpayment blocked: the balance due for {p.code} is {bal}.'})
        sp = SalaryPayment.objects.create(payroll=p, amount=amount, payment_date=payment_date, bank_account=bank_account,
                                          mode=mode, reference=(reference or '')[:80], remarks=(remarks or '')[:240])
        postings.post_salary_payment(sp)
    return sp


def cancel_payment(sp, reason):
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('A reason is required to cancel a salary payment.')
    with transaction.atomic():
        sp = SalaryPayment.objects.select_for_update().get(pk=sp.pk)
        if sp.cancelled:
            raise ValidationError('Already cancelled.')
        sp.cancelled, sp.cancel_reason = True, reason[:240]
        sp.save()
        postings.unpost(postings.salary_payment_key(sp), f'Salary payment cancelled: {reason}')
    return sp


def pay_all_unpaid(period, *, payment_date, bank_account=None, mode='bank', reference=''):
    """Pay the outstanding balance of every current finalized payslip in the month from one bank."""
    made = []
    with transaction.atomic():
        rows = period.payrolls.filter(status=EmployeePayroll.FINALIZED, is_current=True).order_by('snap_employee_code')
        for p in rows:
            bal = status_for(p)['balance']
            if bal > 0:
                made.append(record_payment(p, amount=bal, payment_date=payment_date, bank_account=bank_account,
                                           mode=mode, reference=reference, remarks='Bulk salary payment'))
    return made
