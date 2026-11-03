"""Invoice lifecycle and ledger posting (Stage 4).

POLICY - editing a finalized invoice: the customer, invoice date, currency, taxable value, tax and grand
total of a FINALIZED invoice are locked (Invoice._guard_lifecycle). A reduction is made with a CreditNote
(reverses income + output GST proportionally and credits the customer); anything else is a cancellation
(reason required, ledger entry reversed) and a new invoice. Text-only edits (terms, PDF number override,
line descriptions with unchanged totals) stay allowed. Invoices that existed before Stage 4 were migrated
as 'finalized' WITHOUT a ledger entry; they are posted only by the explicit "Post to ledger" action or the
``backfill_invoice_ledger --apply`` command.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounting import postings
from accounting.money import q2

from .models import CreditNote, Invoice


def _lock(inv):
    return Invoice.objects.select_for_update().get(pk=inv.pk)


def finalize_invoice(inv):
    """Draft -> finalized and posted (with any receipts / credit notes already recorded)."""
    with transaction.atomic():
        i = _lock(inv)
        if i.status != Invoice.DRAFT:
            raise ValidationError(f'Only draft invoices can be finalized ({i.invoice_number} is {i.get_status_display()}).')
        if q2(i.grand_total) <= 0:
            raise ValidationError('An invoice with a zero total cannot be finalized.')
        i.status, i.finalized_at, i._lifecycle = Invoice.FINALIZED, timezone.now(), True
        i.save()
        postings.post_invoice(i)
    inv.refresh_from_db()
    return inv


def post_to_ledger(inv):
    """Explicit posting for a finalized invoice that has no entry yet (pre-Stage-4 invoices). Idempotent."""
    with transaction.atomic():
        i = _lock(inv)
        if i.status != Invoice.FINALIZED:
            raise ValidationError('Only finalized invoices can be posted to the ledger.')
        return postings.post_invoice(i)


def cancel_invoice(inv, reason, cancel_date=None):
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('A reason is required to cancel an invoice.')
    with transaction.atomic():
        i = _lock(inv)
        if i.status == Invoice.CANCELLED:
            raise ValidationError('This invoice is already cancelled.')
        if i.status == Invoice.FINALIZED:
            if i.payments.exists():
                raise ValidationError('This invoice has receipts. Delete them (their entries are reversed) or issue '
                                      'a credit note instead of cancelling.')
            if i.credit_notes.filter(status=CreditNote.ACTIVE).exists():
                raise ValidationError('Cancel the credit notes of this invoice first.')
            postings.unpost(i.ledger_key, f'Invoice {i.invoice_number} cancelled: {reason}', cancel_date)
        i.cancelled, i.cancelled_date, i.cancel_reason, i._lifecycle = True, cancel_date or timezone.localdate(), reason[:300], True
        i.save()
    inv.refresh_from_db()
    return inv


def create_credit_note(inv, *, date, reason, taxable_amount, cgst=None, sgst=None, igst=None):
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError({'reason': 'A reason is required.'})
    taxable_amount = q2(taxable_amount)
    with transaction.atomic():
        i = _lock(inv)
        if i.status != Invoice.FINALIZED:
            raise ValidationError('Credit notes can only be issued against finalized invoices.')
        if date < i.invoice_date:
            raise ValidationError({'date': 'Credit note date cannot be before the invoice date.'})
        if taxable_amount <= 0:
            raise ValidationError({'taxable_amount': 'Taxable amount must be greater than zero.'})
        cn = CreditNote(invoice=i, date=date, reason=reason[:300], taxable_amount=taxable_amount)
        cn.apply_proportional_tax()
        for f, v in (('cgst_amount', cgst), ('sgst_amount', sgst), ('igst_amount', igst)):
            if v not in (None, ''):
                setattr(cn, f, q2(v))
        cn.total = q2(cn.taxable_amount + cn.total_tax)
        already = sum((c.total for c in i.credit_notes.filter(status=CreditNote.ACTIVE)), Decimal('0.00'))
        taxable_done = sum((c.taxable_amount for c in i.credit_notes.filter(status=CreditNote.ACTIVE)), Decimal('0.00'))
        if taxable_done + taxable_amount > i.taxable_value or already + cn.total > i.grand_total:
            raise ValidationError({'taxable_amount': f'Credit notes would exceed the invoice (already credited {already}, '
                                                     f'grand total {i.grand_total}).'})
        cn.save()
        postings.post_credit_note(cn)
    return cn


def cancel_credit_note(cn, reason):
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('A reason is required to cancel a credit note.')
    with transaction.atomic():
        c = CreditNote.objects.select_for_update().get(pk=cn.pk)
        if c.status != CreditNote.ACTIVE:
            raise ValidationError('This credit note is already cancelled.')
        postings.unpost(postings.credit_note_key(c), f'Credit note {c.number} cancelled: {reason}')
        c.status, c.cancel_reason, c.cancelled_at = CreditNote.CANCELLED, reason[:300], timezone.now()
        c.save()
    cn.refresh_from_db()
    return cn
