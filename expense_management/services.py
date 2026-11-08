"""Financial operations. Every money movement runs inside transaction.atomic and re-reads
the parent row so totals are validated against the current database state."""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from employee_management.validators import CONTENT_TYPES, sniff_upload

from .models import (ZERO, CompanyExpense, CompanyExpenseAttachment, CompanyExpensePayment, EmployeeClaimReview,
                     EmployeeExpenseAttachment, EmployeeExpenseClaim, EmployeeReimbursement, ExpenseSettings, q2)

from .ledger import post_expense, post_payment, post_reimbursement

C = EmployeeExpenseClaim


def to_decimal(v, field='amount'):
    try:
        d = Decimal(str(v).replace(',', '').strip())
    except (InvalidOperation, AttributeError):
        raise ValidationError({field: 'Enter a valid amount.'})
    if not d.is_finite():
        raise ValidationError({field: 'Enter a valid amount.'})
    return q2(d)


# --------------------------------------------------------------------------- company expense payments
def record_payment(expense, *, payment_date, method, amount, reference='', bank_account='', remarks='', bank=None,
                   tds_amount=None):
    amount = to_decimal(amount)
    tds = q2(to_decimal(tds_amount, 'tds_amount')) if tds_amount not in (None, '') else ZERO
    with transaction.atomic():
        exp = CompanyExpense.objects.select_for_update().get(pk=expense.pk)
        if exp.is_cancelled:
            raise ValidationError('This expense is cancelled; payments cannot be added.')
        if exp.is_draft:
            raise ValidationError('Finalize this draft before recording a payment.')
        if amount <= 0:
            raise ValidationError({'amount': 'Payment amount must be greater than zero.'})
        if tds < 0:
            raise ValidationError({'tds_amount': 'TDS cannot be negative.'})
        outstanding = exp.outstanding_amount
        if amount + tds > outstanding:
            raise ValidationError({'amount': f'Payment {amount} + TDS {tds} exceeds the outstanding amount {outstanding}.'})
        if exp.payments.filter(payment_date=payment_date, method=method, amount=amount,
                               reference=(reference or '').strip()).exists():
            raise ValidationError('An identical payment (same date, method, amount and reference) is already recorded.')
        p = CompanyExpensePayment.objects.create(expense=exp, payment_date=payment_date, method=method, amount=amount,
                                                 reference=(reference or '').strip(), bank_account=bank_account or '',
                                                 remarks=remarks or '', bank=bank, tds_amount=tds)
        post_payment(p)
        exp.refresh_status()
    expense.payment_status = exp.payment_status
    return p


def cancel_expense(expense, reason):
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError({'reason': 'A cancellation reason is required.'})
    with transaction.atomic():
        exp = CompanyExpense.objects.select_for_update().get(pk=expense.pk)
        if exp.is_cancelled:
            raise ValidationError('This expense is already cancelled.')
        CompanyExpense.objects.filter(pk=exp.pk).update(payment_status=CompanyExpense.CANCELLED, is_draft=False,
                                                        cancel_reason=reason[:300], cancelled_at=timezone.now())
        exp.refresh_from_db()
        post_expense(exp)
    expense.refresh_from_db()
    return expense


# --------------------------------------------------------------------------- attachments
def add_attachment(parent, f, doc_type, remarks=''):
    limit = ExpenseSettings.load().max_attachment_mb * 1024 * 1024
    ext = sniff_upload(f, limit)
    if isinstance(parent, CompanyExpense):
        att = CompanyExpenseAttachment(expense=parent, doc_type=doc_type)
    else:
        att = EmployeeExpenseAttachment(claim=parent, doc_type=doc_type)
    att._validated_ext = ext
    att.original_filename = (f.name or '')[-255:]
    att.content_type = CONTENT_TYPES[ext]
    att.file_size = f.size
    att.remarks = remarks or ''
    att.file.save(f'upload.{ext}', f, save=False)
    att.save()
    return att


def validate_uploads(files):
    limit = ExpenseSettings.load().max_attachment_mb * 1024 * 1024
    errors = []
    for f in files:
        try:
            sniff_upload(f, limit)
        except ValidationError as e:
            errors.append(f'{f.name}: {" ".join(e.messages)}')
    return errors


def replace_attachment(att, f):
    limit = ExpenseSettings.load().max_attachment_mb * 1024 * 1024
    ext = sniff_upload(f, limit)
    old = att.file.name
    att._validated_ext = ext
    att.file.save(f'upload.{ext}', f, save=False)
    att.original_filename = (f.name or '')[-255:]
    att.content_type = CONTENT_TYPES[ext]
    att.file_size = f.size
    att.uploaded_at = timezone.now()
    att.save()
    if old and old != att.file.name:
        att.file.storage.delete(old)
    return att


def delete_attachment(att):
    name, storage = att.file.name, att.file.storage
    type(att).objects.filter(pk=att.pk).delete()
    if name:
        storage.delete(name)


# --------------------------------------------------------------------------- claims
def _log(claim, action, old, remarks='', amount=None):
    EmployeeClaimReview.objects.create(claim=claim, action=action, from_status=old, to_status=claim.status,
                                       remarks=(remarks or '')[:300], amount=amount)


def _lock(claim):
    return C.objects.select_for_update().get(pk=claim.pk)


def _finish(claim, fresh):
    claim.refresh_from_db()
    return claim


def submit_claim(claim, remarks=''):
    with transaction.atomic():
        c = _lock(claim)
        if c.status != C.DRAFT:
            raise ValidationError('Only draft claims can be submitted.')
        old, c.status, c.returned_for_correction = c.status, C.SUBMITTED, False
        c.save()
        _log(c, 'submit', old, remarks)
    return _finish(claim, c)


def start_review(claim, remarks=''):
    with transaction.atomic():
        c = _lock(claim)
        if c.status != C.SUBMITTED:
            raise ValidationError('Only submitted claims can be moved to Under Review.')
        old, c.status = c.status, C.UNDER_REVIEW
        c.save()
        _log(c, 'start_review', old, remarks)
    return _finish(claim, c)


def approve_claim(claim, approved_amount=None, remarks=''):
    """Full approval when approved_amount is None or equals the claim; partial when lower.
    Approval never records or implies a reimbursement."""
    with transaction.atomic():
        c = _lock(claim)
        if c.status not in (C.SUBMITTED, C.UNDER_REVIEW, C.APPROVED, C.PARTIALLY_APPROVED):
            raise ValidationError(f'A {c.get_status_display()} claim cannot be approved.')
        amt = q2(c.amount_claimed) if approved_amount in (None, '') else to_decimal(approved_amount, 'approved_amount')
        if amt <= 0:
            raise ValidationError({'approved_amount': 'Approved amount must be greater than zero (use Reject instead).'})
        if amt > c.amount_claimed:
            raise ValidationError({'approved_amount': 'Approved amount cannot exceed the amount claimed.'})
        paid = c.reimbursed_amount
        if amt < paid:
            raise ValidationError({'approved_amount': f'Already reimbursed {paid}; approved amount cannot be lower.'})
        old = c.status
        c.approved_amount = amt
        c.status = C.APPROVED if amt == q2(c.amount_claimed) else C.PARTIALLY_APPROVED
        c.rejection_reason = ''
        if remarks:
            c.review_remarks = remarks
        c.save()
        _log(c, 'approve' if c.status == C.APPROVED else 'partial', old, remarks, amt)
        if paid and paid >= amt:  # re-approval at exactly the amount already paid
            C.objects.filter(pk=c.pk).update(status=C.REIMBURSED)
    return _finish(claim, c)


def reject_claim(claim, reason):
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError({'reason': 'A rejection reason is required.'})
    with transaction.atomic():
        c = _lock(claim)
        if c.status not in (C.SUBMITTED, C.UNDER_REVIEW, C.APPROVED, C.PARTIALLY_APPROVED):
            raise ValidationError(f'A {c.get_status_display()} claim cannot be rejected.')
        if c.reimbursed_amount > 0:
            raise ValidationError('This claim already has reimbursements recorded and cannot be rejected.')
        old = c.status
        c.status, c.rejection_reason, c.approved_amount = C.REJECTED, reason[:300], None
        c.save()
        _log(c, 'reject', old, reason)
    return _finish(claim, c)


def return_for_correction(claim, remarks=''):
    with transaction.atomic():
        c = _lock(claim)
        if c.status not in (C.SUBMITTED, C.UNDER_REVIEW, C.APPROVED, C.PARTIALLY_APPROVED, C.REJECTED):
            raise ValidationError(f'A {c.get_status_display()} claim cannot be returned for correction.')
        if c.reimbursed_amount > 0:
            raise ValidationError('This claim already has reimbursements recorded and cannot be returned.')
        old = c.status
        c.status, c.returned_for_correction, c.approved_amount = C.DRAFT, True, None
        if remarks:
            c.review_remarks = remarks
        c.save()
        _log(c, 'return', old, remarks)
    return _finish(claim, c)


def cancel_claim(claim, reason):
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError({'reason': 'A cancellation reason is required.'})
    with transaction.atomic():
        c = _lock(claim)
        if c.status in (C.CANCELLED, C.REIMBURSED):
            raise ValidationError(f'A {c.get_status_display()} claim cannot be cancelled.')
        if c.reimbursed_amount > 0:
            raise ValidationError('This claim already has reimbursements recorded and cannot be cancelled.')
        old = c.status
        c.status, c.cancel_reason = C.CANCELLED, reason[:300]
        c.save()
        _log(c, 'cancel', old, reason)
    return _finish(claim, c)


def record_reimbursement(claim, *, reimbursement_date, method, amount, bank_account='', reference='', remarks='',
                         bank=None):
    amount = to_decimal(amount)
    with transaction.atomic():
        c = _lock(claim)
        if c.status not in (C.APPROVED, C.PARTIALLY_APPROVED):
            raise ValidationError('Only approved or partially approved claims can be reimbursed.')
        if amount <= 0:
            raise ValidationError({'amount': 'Reimbursement amount must be greater than zero.'})
        outstanding = c.outstanding_amount
        if amount > outstanding:
            raise ValidationError({'amount': f'Reimbursement {amount} exceeds the outstanding amount {outstanding}.'})
        if c.reimbursements.filter(reimbursement_date=reimbursement_date, method=method, amount=amount,
                                   reference=(reference or '').strip()).exists():
            raise ValidationError('An identical reimbursement (same date, method, amount and reference) is already recorded.')
        r = EmployeeReimbursement.objects.create(claim=c, reimbursement_date=reimbursement_date, method=method,
                                                 amount=amount, bank_account=bank_account or '',
                                                 reference=(reference or '').strip(), remarks=remarks or '', bank=bank)
        post_reimbursement(r)
        old = c.status
        if c.outstanding_amount <= 0:
            C.objects.filter(pk=c.pk).update(status=C.REIMBURSED, updated_at=timezone.now())
            c.status = C.REIMBURSED
        _log(c, 'reimburse', old, remarks or reference, amount)
    claim.refresh_from_db()
    return r
