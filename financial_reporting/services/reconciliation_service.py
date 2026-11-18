"""
Reconciliation execution service.

Runs a named reconciliation type over a period, populates the ReconciliationRun
(total / matched / unmatched records, difference_amount) and creates individual
ReconciliationIssue rows for every discrepancy found.

Supported reconciliation types
--------------------------------
- ``mgmt_vs_ledger``  : management-report figures vs general-ledger balances
- ``ar_vs_ledger``    : accounts-receivable outstanding vs ledger AR balance
- ``ap_vs_ledger``    : accounts-payable outstanding vs ledger AP balance
- ``payroll_vs_ledger``: salary payable vs ledger salary-payable account
- ``gst_vs_ledger``   : GST payable vs ledger GST accounts
"""
import traceback
from decimal import Decimal

from django.utils import timezone

from financial_reporting.models import (
    FinancialPeriod,
    ReconciliationIssue,
    ReconciliationRun,
)
from financial_reporting.services.period_service import get_or_create_period

ZERO = Decimal('0.00')
TOLERANCE = Decimal('0.01')   # differences ≤ 1 paisa are considered matched


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _period_dates(period):
    """Return (start_date, end_date) from either a reporting.periods.Period or FinancialPeriod."""
    if hasattr(period, 'start') and hasattr(period, 'end'):
        return period.start, period.end
    return period.start_date, period.end_date


def _get_fp(period):
    if hasattr(period, 'start') and hasattr(period, 'end'):
        return get_or_create_period(period)
    return period


def _add_issue(run, source_app, source_ref, expected, actual, category):
    diff = (actual or ZERO) - (expected or ZERO)
    ReconciliationIssue.objects.create(
        reconciliation_run=run,
        source_application=source_app,
        source_reference=source_ref,
        expected_value=expected,
        actual_value=actual,
        difference=diff,
        issue_category=category,
        resolution_status=ReconciliationIssue.OPEN,
    )


# ---------------------------------------------------------------------------
# Individual reconciliation runners
# ---------------------------------------------------------------------------

def _run_mgmt_vs_ledger(run, s, e):
    """Compare management-report totals against ledger movements/balances."""
    from accounting.models import Account
    from reporting import ledger as LG
    from reporting import services as S
    from accounting.money import q2

    checks = []

    # Revenue
    mrev = q2(S.invoice_revenue(s, e)[0])
    lrev = q2(
        LG.account_movement('invoice.revenue', s, e)
        + LG.account_movement('invoice.recruitment_revenue', s, e)
    )
    checks.append(('Revenue', 'invoice_app', 'invoice revenue vs ledger', mrev, lrev))

    # Company expenses
    mexp = q2(S.company_expense_total(s, e))
    lexp = q2(LG.source_movement(['company_expense'], [Account.EXPENSE], s, e))
    checks.append(('CompanyExpenses', 'expense_management', 'company expenses vs ledger', mexp, lexp))

    # Employee claims
    mcl = q2(S.reimbursement_expense(s, e))
    lcl = q2(LG.source_movement(['expense_claim'], [Account.EXPENSE], s, e))
    checks.append(('EmployeeClaims', 'expense_management', 'employee claims vs ledger', mcl, lcl))

    # Payroll
    g, er = S.payroll_expense(s, e)
    mpay = q2(g + er)
    lpay = q2(LG.source_movement(['payroll'], [Account.EXPENSE], s, e))
    checks.append(('Payroll', 'employee_management', 'payroll expense vs ledger', mpay, lpay))

    # AR (balance as of period end)
    mar = q2(S.receivables_outstanding(e))
    lar = q2(LG.account_balance('invoice.ar', e))
    checks.append(('AR', 'invoice_app', 'AR outstanding vs ledger AR balance', mar, lar))

    # AP (balance as of period end)
    map_ = q2(S.dsum(x['outstanding_inr'] for x in S.vendor_payables(e)))
    lap = q2(LG.account_balance('expense.ap', e))
    checks.append(('AP', 'expense_management', 'AP outstanding vs ledger AP balance', map_, lap))

    total = len(checks)
    matched = 0
    total_diff = ZERO
    for category, source_app, ref, expected, actual in checks:
        diff = abs((actual or ZERO) - (expected or ZERO))
        if diff <= TOLERANCE:
            matched += 1
        else:
            total_diff += diff
            _add_issue(run, source_app, ref, expected, actual, category)

    return total, matched, total_diff


def _run_ar_vs_ledger(run, s, e):
    from reporting import ledger as LG
    from reporting import services as S
    from accounting.money import q2

    mar = q2(S.receivables_outstanding(e))
    lar = q2(LG.account_balance('invoice.ar', e))
    diff = abs(mar - lar)
    if diff > TOLERANCE:
        _add_issue(run, 'invoice_app', 'AR outstanding vs ledger AR', mar, lar, 'AR')
        return 1, 0, diff
    return 1, 1, ZERO


def _run_ap_vs_ledger(run, s, e):
    from reporting import services as S
    from reporting import ledger as LG
    from accounting.money import q2

    map_ = q2(S.dsum(x['outstanding_inr'] for x in S.vendor_payables(e)))
    lap = q2(LG.account_balance('expense.ap', e))
    diff = abs(map_ - lap)
    if diff > TOLERANCE:
        _add_issue(run, 'expense_management', 'AP outstanding vs ledger AP', map_, lap, 'AP')
        return 1, 0, diff
    return 1, 1, ZERO


def _run_payroll_vs_ledger(run, s, e):
    from reporting import services as S
    from reporting import ledger as LG
    from accounting.money import q2

    mpay = q2(S.dsum(x['outstanding'] for x in S.payroll_payable(e)))
    lpay = q2(LG.account_balance('payroll.salary_payable', e))
    diff = abs(mpay - lpay)
    if diff > TOLERANCE:
        _add_issue(run, 'employee_management', 'salary payable vs ledger', mpay, lpay, 'SalaryPayable')
        return 1, 0, diff
    return 1, 1, ZERO


def _run_gst_vs_ledger(run, s, e):
    from reporting import ledger as LG
    from accounting.money import q2

    # GST output tax (collected from customers)
    gst_output = q2(
        LG.account_movement('gst.output_cgst', s, e)
        + LG.account_movement('gst.output_sgst', s, e)
        + LG.account_movement('gst.output_igst', s, e)
    )
    # GST input tax (paid to vendors)
    gst_input = q2(
        LG.account_movement('gst.input_cgst', s, e)
        + LG.account_movement('gst.input_sgst', s, e)
        + LG.account_movement('gst.input_igst', s, e)
    )
    # Net GST payable = output - input
    net_gst_ledger = q2(gst_output - gst_input)
    # Management-side: total GST from invoices minus input from expenses
    from invoice_app.models import Invoice
    from expense_management.models import CompanyExpense
    from django.db.models import Sum

    inv_gst = Invoice.objects.filter(
        invoice_date__gte=s, invoice_date__lte=e, status__in=['final', 'paid']
    ).aggregate(t=Sum('total_gst'))['t'] or ZERO

    exp_gst = CompanyExpense.objects.filter(
        expense_date__gte=s, expense_date__lte=e,
    ).aggregate(t=Sum('gst_amount'))['t'] or ZERO

    net_gst_mgmt = q2(Decimal(str(inv_gst)) - Decimal(str(exp_gst)))

    diff = abs(net_gst_mgmt - net_gst_ledger)
    if diff > TOLERANCE:
        _add_issue(run, 'accounting', 'net GST payable vs ledger GST accounts',
                   net_gst_mgmt, net_gst_ledger, 'GST')
        return 1, 0, diff
    return 1, 1, ZERO


_RUNNERS = {
    'mgmt_vs_ledger': _run_mgmt_vs_ledger,
    'ar_vs_ledger': _run_ar_vs_ledger,
    'ap_vs_ledger': _run_ap_vs_ledger,
    'payroll_vs_ledger': _run_payroll_vs_ledger,
    'gst_vs_ledger': _run_gst_vs_ledger,
}

RECONCILIATION_TYPES = [
    ('mgmt_vs_ledger', 'Management Reports vs General Ledger'),
    ('ar_vs_ledger', 'Accounts Receivable vs Ledger'),
    ('ap_vs_ledger', 'Accounts Payable vs Ledger'),
    ('payroll_vs_ledger', 'Payroll Payable vs Ledger'),
    ('gst_vs_ledger', 'GST Payable vs Ledger'),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def execute_reconciliation(reconciliation_type, period, user=None):
    """
    Run a reconciliation and return a populated ReconciliationRun instance.

    Args:
        reconciliation_type: one of the keys in RECONCILIATION_TYPES.
        period: a reporting.periods.Period or FinancialPeriod instance.
        user: the requesting user (stored for auditing; not used here).

    Returns:
        ReconciliationRun with status set to 'completed' or 'failed'.
    """
    s, e = _period_dates(period)
    fp = _get_fp(period)

    run = ReconciliationRun.objects.create(
        reconciliation_type=reconciliation_type,
        financial_period=fp,
        start_date=s,
        end_date=e,
        status=ReconciliationRun.RUNNING,
        generated_at=timezone.now(),
    )

    runner = _RUNNERS.get(reconciliation_type)
    if runner is None:
        run.status = ReconciliationRun.FAILED
        run.error_details = (
            f'Unknown reconciliation type "{reconciliation_type}". '
            f'Supported types: {", ".join(_RUNNERS)}.'
        )
        run.save(update_fields=['status', 'error_details'])
        return run

    try:
        total, matched, diff = runner(run, s, e)
        run.total_records = total
        run.matched_records = matched
        run.unmatched_records = total - matched
        run.difference_amount = diff if diff else None
        run.status = ReconciliationRun.COMPLETED
        run.generated_at = timezone.now()
        run.save(update_fields=[
            'total_records', 'matched_records', 'unmatched_records',
            'difference_amount', 'status', 'generated_at',
        ])
    except Exception:
        run.status = ReconciliationRun.FAILED
        run.error_details = traceback.format_exc()
        run.generated_at = timezone.now()
        run.save(update_fields=['status', 'error_details', 'generated_at'])

    return run
