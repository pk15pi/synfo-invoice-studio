"""
Audit readiness evaluation service.

Evaluates every active AuditChecklist item for a given FinancialPeriod and
produces a readiness score (0–100).  Each check examines real data from the
relevant app module so the result is computed rather than hand-entered.

Score formula
-------------
- Each check contributes equally.
- Passed / NotApplicable  → full credit.
- Warning                 → half credit.
- Failed / Pending        → no credit.
- Score = (full_credit + 0.5 * warning_credit) / total_checks * 100, rounded to nearest integer.

Built-in automated checks
--------------------------
  unposted-journals        No unposted draft journal entries in the period.
  ar-reconciled            AR management figure vs ledger within ₹1.
  ap-reconciled            AP management figure vs ledger within ₹1.
  payroll-reconciled       Salary payable management vs ledger within ₹1.
  tb-balanced              Trial balance debits == credits.
  invoices-finalised       No draft invoices with an invoice_date inside the period.
  expenses-approved        No pending company expenses in the period.
  compliance-filed         All compliance submissions for the period are Filed.

Any AuditChecklist code not in the built-in list is evaluated as PENDING
(needs manual review) rather than raising an error.
"""
import traceback
from decimal import Decimal

from django.utils import timezone

from financial_reporting.models import (
    AuditChecklist,
    AuditChecklistResult,
    ComplianceSubmission,
    FinancialPeriod,
)

ZERO = Decimal('0.00')
TOLERANCE = Decimal('0.01')


# ---------------------------------------------------------------------------
# Individual check runners — each returns (status, summary, details_dict)
# ---------------------------------------------------------------------------

def _check_unposted_journals(s, e):
    try:
        from accounting.models import JournalEntry
        count = JournalEntry.objects.filter(
            entry_date__gte=s, entry_date__lte=e,
            status='draft',
        ).count()
        if count == 0:
            return AuditChecklistResult.PASSED, 'No unposted draft journals in the period.', {'draft_count': 0}
        return AuditChecklistResult.FAILED, f'{count} draft journal entr{"ies" if count != 1 else "y"} remain unposted.', {'draft_count': count}
    except Exception as exc:
        return AuditChecklistResult.WARNING, f'Could not check: {exc}', {'error': str(exc)}


def _check_ar_reconciled(s, e):
    try:
        from reporting import ledger as LG
        from reporting import services as S
        from accounting.money import q2
        mar = q2(S.receivables_outstanding(e))
        lar = q2(LG.account_balance('invoice.ar', e))
        diff = abs(mar - lar)
        if diff <= TOLERANCE:
            return AuditChecklistResult.PASSED, 'AR management figure ties to ledger.', {'management': str(mar), 'ledger': str(lar), 'difference': str(diff)}
        return AuditChecklistResult.FAILED, f'AR differs by ₹{diff}.', {'management': str(mar), 'ledger': str(lar), 'difference': str(diff)}
    except Exception as exc:
        return AuditChecklistResult.WARNING, f'Could not check: {exc}', {'error': str(exc)}


def _check_ap_reconciled(s, e):
    try:
        from reporting import ledger as LG
        from reporting import services as S
        from accounting.money import q2
        map_ = q2(S.dsum(x['outstanding_inr'] for x in S.vendor_payables(e)))
        lap = q2(LG.account_balance('expense.ap', e))
        diff = abs(map_ - lap)
        if diff <= TOLERANCE:
            return AuditChecklistResult.PASSED, 'AP management figure ties to ledger.', {'management': str(map_), 'ledger': str(lap), 'difference': str(diff)}
        return AuditChecklistResult.FAILED, f'AP differs by ₹{diff}.', {'management': str(map_), 'ledger': str(lap), 'difference': str(diff)}
    except Exception as exc:
        return AuditChecklistResult.WARNING, f'Could not check: {exc}', {'error': str(exc)}


def _check_payroll_reconciled(s, e):
    try:
        from reporting import ledger as LG
        from reporting import services as S
        from accounting.money import q2
        mpay = q2(S.dsum(x['outstanding'] for x in S.payroll_payable(e)))
        lpay = q2(LG.account_balance('payroll.salary_payable', e))
        diff = abs(mpay - lpay)
        if diff <= TOLERANCE:
            return AuditChecklistResult.PASSED, 'Salary payable ties to ledger.', {'management': str(mpay), 'ledger': str(lpay), 'difference': str(diff)}
        return AuditChecklistResult.FAILED, f'Salary payable differs by ₹{diff}.', {'management': str(mpay), 'ledger': str(lpay), 'difference': str(diff)}
    except Exception as exc:
        return AuditChecklistResult.WARNING, f'Could not check: {exc}', {'error': str(exc)}


def _check_tb_balanced(s, e):
    try:
        from reporting import ledger as LG
        rows, tot = LG.trial_balance(s, e)
        if tot.get('balanced', False):
            return AuditChecklistResult.PASSED, 'Trial balance is balanced.', {'difference': str(tot.get('difference', ZERO))}
        diff = tot.get('difference', ZERO)
        return AuditChecklistResult.FAILED, f'Trial balance out of balance by ₹{diff}.', {'difference': str(diff)}
    except Exception as exc:
        return AuditChecklistResult.WARNING, f'Could not check: {exc}', {'error': str(exc)}


def _check_invoices_finalised(s, e):
    try:
        from invoice_app.models import Invoice
        count = Invoice.objects.filter(
            invoice_date__gte=s, invoice_date__lte=e,
            status='draft',
        ).count()
        if count == 0:
            return AuditChecklistResult.PASSED, 'No draft invoices in the period.', {'draft_count': 0}
        return AuditChecklistResult.WARNING, f'{count} draft invoice{"s" if count != 1 else ""} exist in the period.', {'draft_count': count}
    except Exception as exc:
        return AuditChecklistResult.WARNING, f'Could not check: {exc}', {'error': str(exc)}


def _check_expenses_approved(s, e):
    try:
        from expense_management.models import CompanyExpense
        count = CompanyExpense.objects.filter(
            expense_date__gte=s, expense_date__lte=e,
            status='pending',
        ).count()
        if count == 0:
            return AuditChecklistResult.PASSED, 'No pending company expenses in the period.', {'pending_count': 0}
        return AuditChecklistResult.WARNING, f'{count} pending company expense{"s" if count != 1 else ""} need approval.', {'pending_count': count}
    except Exception as exc:
        return AuditChecklistResult.WARNING, f'Could not check: {exc}', {'error': str(exc)}


def _check_compliance_filed(fp):
    """Check whether all compliance submissions linked to this period are filed."""
    try:
        total = ComplianceSubmission.objects.filter(reporting_period=fp).count()
        if total == 0:
            return AuditChecklistResult.NOT_APPLICABLE, 'No compliance submissions for this period.', {'total': 0}
        unfiled = ComplianceSubmission.objects.filter(
            reporting_period=fp,
        ).exclude(status=ComplianceSubmission.FILED).count()
        if unfiled == 0:
            return AuditChecklistResult.PASSED, f'All {total} compliance submission{"s" if total != 1 else ""} filed.', {'total': total, 'unfiled': 0}
        return AuditChecklistResult.FAILED, f'{unfiled} of {total} submissions not yet filed.', {'total': total, 'unfiled': unfiled}
    except Exception as exc:
        return AuditChecklistResult.WARNING, f'Could not check: {exc}', {'error': str(exc)}


# Map checklist codes → runner callables.
# Runners that need only (start, end) take two positional args.
# _check_compliance_filed takes (financial_period) — handled specially below.
_RUNNERS = {
    'unposted-journals': _check_unposted_journals,
    'ar-reconciled': _check_ar_reconciled,
    'ap-reconciled': _check_ap_reconciled,
    'payroll-reconciled': _check_payroll_reconciled,
    'tb-balanced': _check_tb_balanced,
    'invoices-finalised': _check_invoices_finalised,
    'expenses-approved': _check_expenses_approved,
}

_PERIOD_RUNNERS = {
    'compliance-filed': _check_compliance_filed,
}


# ---------------------------------------------------------------------------
# Score calculation
# ---------------------------------------------------------------------------

def _score(results):
    """Return integer 0–100 readiness score from a list of AuditChecklistResult objects."""
    total = len(results)
    if total == 0:
        return 0
    full = sum(
        1 for r in results
        if r.status in (AuditChecklistResult.PASSED, AuditChecklistResult.NOT_APPLICABLE)
    )
    half = sum(1 for r in results if r.status == AuditChecklistResult.WARNING)
    return round((full + half * 0.5) / total * 100)


def score_label(score):
    """Human-readable label for a readiness score integer."""
    if score >= 95:
        return 'Audit ready'
    if score >= 80:
        return 'Mostly ready'
    if score >= 60:
        return 'Needs attention'
    return 'Not ready'


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_audit_readiness(financial_period):
    """
    Evaluate all active AuditChecklist items for ``financial_period``.

    Creates or updates AuditChecklistResult rows and returns a dict:
        {
            'score': int (0–100),
            'label': str,
            'results': list[AuditChecklistResult],
            'errors': list[str],
        }

    Args:
        financial_period: a FinancialPeriod model instance.
    """
    s, e = financial_period.start_date, financial_period.end_date
    checklists = AuditChecklist.objects.filter(active_status=True).order_by('display_order', 'name')
    results = []
    errors = []

    for checklist in checklists:
        try:
            if checklist.code in _RUNNERS:
                status, summary, details = _RUNNERS[checklist.code](s, e)
            elif checklist.code in _PERIOD_RUNNERS:
                status, summary, details = _PERIOD_RUNNERS[checklist.code](financial_period)
            else:
                # No automated runner — mark pending for manual review
                status = AuditChecklistResult.PENDING
                summary = 'Requires manual review.'
                details = {}

            result, _ = AuditChecklistResult.objects.update_or_create(
                checklist=checklist,
                financial_period=financial_period,
                defaults={
                    'status': status,
                    'summary': summary,
                    'details': details,
                    'checked_at': timezone.now(),
                },
            )
            results.append(result)
        except Exception:
            errors.append(f'{checklist.code}: {traceback.format_exc()}')

    return {
        'score': _score(results),
        'label': score_label(_score(results)),
        'results': results,
        'errors': errors,
    }


def get_readiness_for_period(financial_period):
    """
    Return the latest readiness score for a period without re-running checks.
    If no results exist yet, returns score=None and label='Not assessed'.
    """
    results = list(
        AuditChecklistResult.objects.filter(financial_period=financial_period)
    )
    if not results:
        return {'score': None, 'label': 'Not assessed', 'results': []}
    score = _score(results)
    return {'score': score, 'label': score_label(score), 'results': results}
