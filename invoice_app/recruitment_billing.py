"""Client-approved candidate placement fee calculations and eligibility rules."""
from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import RecruitmentAgreement, RecruitmentPlacement

CENT = Decimal('0.01')


def fee_basis(placement):
    agreement = placement.agreement
    if agreement.ctc_basis == RecruitmentAgreement.ANNUAL_CTC:
        return placement.finalized_annual_ctc
    if agreement.ctc_basis == RecruitmentAgreement.MONTHLY_ANNUALIZED:
        return placement.monthly_ctc * Decimal('12') if placement.monthly_ctc is not None else None
    if agreement.ctc_basis == RecruitmentAgreement.AGREED_BASIS:
        return placement.agreed_basis_amount
    return None


def calculate_fee(placement):
    agreement = placement.agreement
    basis = None
    if agreement.billing_method == RecruitmentAgreement.FIXED:
        raw = agreement.fixed_fee
    elif agreement.billing_method == RecruitmentAgreement.PERCENTAGE:
        basis = fee_basis(placement)
        if not placement.ctc_finalized:
            raise ValidationError('Placement fee is pending: record the finalized, client-approved CTC first.')
        if basis is None:
            raise ValidationError('The finalized amount required by the agreement CTC basis is missing.')
        raw = basis * agreement.percentage / Decimal('100')
    else:
        raise ValidationError('Choose a valid recruitment billing method in the agreement.')
    if raw is None:
        raise ValidationError('The agreement does not contain a recruitment fee amount.')
    if agreement.minimum_fee is not None:
        raw = max(raw, agreement.minimum_fee)
    if agreement.maximum_fee is not None:
        raw = min(raw, agreement.maximum_fee)
    return raw.quantize(CENT, rounding=ROUND_HALF_UP), basis


def _terms_snapshot(agreement):
    return {
        'client_id': agreement.client_id,
        'service_type': agreement.service_type,
        'billing_method': agreement.billing_method,
        'percentage': str(agreement.percentage) if agreement.percentage is not None else None,
        'fixed_fee': str(agreement.fixed_fee) if agreement.fixed_fee is not None else None,
        'ctc_basis': agreement.ctc_basis,
        'ctc_basis_description': agreement.ctc_basis_description,
        'agreement_reference': agreement.agreement_reference,
        'minimum_fee': str(agreement.minimum_fee) if agreement.minimum_fee is not None else None,
        'maximum_fee': str(agreement.maximum_fee) if agreement.maximum_fee is not None else None,
        'payment_terms': agreement.payment_terms,
        'sac_code': agreement.sac_code,
        'gst_rate': str(agreement.gst_rate) if agreement.gst_rate is not None else None,
        'billing_trigger': agreement.billing_trigger,
        'retention_days': agreement.retention_days,
        'custom_trigger_description': agreement.custom_trigger_description,
        'guarantee_days': agreement.guarantee_days,
        'replacement_terms': agreement.replacement_terms,
        'refund_terms': agreement.refund_terms,
        'guarantee_conditions': agreement.guarantee_conditions,
    }


@transaction.atomic
def approve_fee(placement, *, override_amount=None, override_reason=''):
    placement = RecruitmentPlacement.objects.select_for_update().select_related(
        'agreement', 'candidate', 'client').get(pk=placement.pk)
    if placement.fee_approved:
        raise ValidationError('This approved fee snapshot is locked; create a new placement record for a new commercial version.')
    if placement.invoice_id and placement.invoice.status != 'draft':
        raise ValidationError('An invoiced placement fee is preserved; use the existing credit-note workflow for adjustments.')
    placement.full_clean()
    if not placement.can_approve_fee:
        if not placement.agreement.billing_trigger:
            raise ValidationError('Configure the contractual billing trigger before approving this placement fee.')
        if not placement.trigger_met:
            raise ValidationError('The agreed billing trigger has not been completed; the fee remains pending.')
        raise ValidationError('Finalize and record the client-approved CTC before approving a percentage fee.')
    calculated, basis = calculate_fee(placement)
    if override_amount is not None:
        if not override_reason.strip():
            raise ValidationError('A fee override requires a reason.')
        if override_amount < 0:
            raise ValidationError('A recruitment fee cannot be negative.')
        final_amount = override_amount.quantize(CENT, rounding=ROUND_HALF_UP)
    else:
        final_amount = calculated
    old_amount = placement.fee_amount
    placement.candidate_name_snapshot = placement.candidate.candidate_name
    placement.designation_snapshot = placement.designation
    placement.agreement_terms_snapshot = _terms_snapshot(placement.agreement)
    placement.billing_method_snapshot = placement.agreement.billing_method
    placement.percentage_snapshot = placement.agreement.percentage
    placement.fixed_fee_snapshot = placement.agreement.fixed_fee
    placement.ctc_basis_snapshot = placement.agreement.ctc_basis
    placement.fee_basis_amount_snapshot = basis
    placement.fee_amount = final_amount
    placement.fee_override_reason = override_reason.strip() if override_amount is not None else ''
    placement.fee_approved = True
    placement.fee_approved_on = timezone.localdate()
    placement.guarantee_start = placement.joining_date if placement.agreement.guarantee_days else None
    placement.guarantee_end = (placement.joining_date + timedelta(days=placement.agreement.guarantee_days)
                               if placement.joining_date and placement.agreement.guarantee_days else None)
    placement.save()
    if override_amount is not None:
        from audit.recorder import log_manual
        log_manual('invoice_app', 'RecruitmentPlacement', placement.pk, str(placement), 'update',
                   reason=override_reason.strip(),
                   changes={'fee_amount': [str(old_amount or calculated), str(final_amount)],
                            'calculated_fee': [None, str(calculated)]})
    return placement
