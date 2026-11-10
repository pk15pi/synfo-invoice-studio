"""Read-only comparison of finalized invoices / payslips against the configured rules for their dates.
Nothing is changed; a missing rule is reported as 'rule not configured', never filled with a default."""
from decimal import Decimal

from accounting.money import ZERO, q2

from . import services as S

OK, MISMATCH, NOT_CONFIGURED = 'ok', 'mismatch', 'not_configured'
LABELS = {OK: 'OK', MISMATCH: 'MISMATCH', NOT_CONFIGURED: 'Rule not configured'}


def _cmp(actual, expected):
    return OK if abs(q2(actual) - q2(expected)) <= S.TOLERANCE else MISMATCH


def _row(doc, date, item, actual, expected=None, status=None, note=''):
    return {'doc': doc, 'date': date, 'item': item, 'actual': actual, 'expected': expected,
            'status': status or _cmp(actual, expected), 'label': LABELS[status or _cmp(actual, expected)],
            'note': note}


def check_invoice(inv):
    from invoice_app import gst
    from invoice_app.models import Invoice
    rows = []
    if inv.tax_type == Invoice.TAX_NONE or not (inv.cgst_amount or inv.sgst_amount or inv.igst_amount):
        return rows
    igst = inv.tax_type == Invoice.TAX_IGST
    exp = {'cgst': ZERO, 'sgst': ZERO, 'igst': ZERO}
    lines = list(inv.lines.all())
    groups = {}
    for ln in lines:
        sac = (ln.sac_code or gst.DEFAULT_SAC).strip()
        groups[sac] = groups.get(sac, ZERO) + (ln.amount or ZERO)
    if not groups:
        groups = {gst.DEFAULT_SAC: inv.taxable_value}
    doc = inv.invoice_number
    missing = False
    for sac, taxable in groups.items():
        try:
            e = S.expected_gst(taxable, sac, inv.invoice_date, igst=igst)
        except S.RuleNotConfigured as ex:
            rows.append(_row(doc, inv.invoice_date, f'GST SAC {sac}', inv.total_tax, status=NOT_CONFIGURED, note=str(ex)))
            missing = True
            continue
        for k in exp:
            exp[k] += e[k]
        if inv.tax_rate and q2(inv.tax_rate) != q2(e['rate']):
            rows.append(_row(doc, inv.invoice_date, f'GST rate SAC {sac}', inv.tax_rate, e['rate'], status=MISMATCH,
                             note=f'Invoice rate {inv.tax_rate}% vs rule {e["rate"]}%'))
    if missing:
        return rows
    for k in ('cgst', 'sgst', 'igst'):
        actual = getattr(inv, f'{k}_amount') or ZERO
        if actual or exp[k]:
            rows.append(_row(doc, inv.invoice_date, k.upper(), actual, q2(exp[k])))
    return rows


def check_payslip(p):
    per, rows = p.period, []
    on = per.end
    doc = f'{p.snap_employee_code or p.employee.employee_id} {per.label}'
    earn = {e.code: e.amount for e in p.earnings.all()}
    ded = {}
    for d in p.deductions.all():
        ded[(d.code, d.side)] = ded.get((d.code, d.side), ZERO) + d.amount
    basic, gross = earn.get('basic', ZERO), p.gross_earnings or ZERO

    def add(item, fn, pairs):
        try:
            e = fn()
        except S.RuleNotConfigured as ex:
            actual = sum((ded.get(k, ZERO) for k, _ in pairs), ZERO)
            if actual:
                rows.append(_row(doc, on, item, actual, status=NOT_CONFIGURED, note=str(ex)))
            return
        for k, ek in pairs:
            actual, expected = ded.get(k, ZERO), e[ek]
            if actual or expected:
                rows.append(_row(doc, on, f'{item} ({k[1]})', actual, expected))
    add('PF', lambda: S.expected_pf(basic, on), [(('pf', 'employee'), 'employee'), (('pf', 'employer'), 'employer')])
    add('ESI', lambda: S.expected_esi(gross, on), [(('esi', 'employee'), 'employee'), (('esi', 'employer'), 'employer')])
    state = (p.employee.state or '').strip()
    if state:
        add(f'PT {state}', lambda: S.expected_pt(gross, state, on), [(('professional_tax', 'employee'), 'amount')])
    elif ded.get(('professional_tax', 'employee')):
        rows.append(_row(doc, on, 'PT', ded[('professional_tax', 'employee')], status=NOT_CONFIGURED,
                         note='rule not configured: employee has no state'))
    return rows


def run(start, end, which=('invoices', 'payslips')):
    from employee_management.models import EmployeePayroll
    from invoice_app.models import Invoice
    rows = []
    if 'invoices' in which:
        for inv in (Invoice.objects.filter(status=Invoice.FINALIZED, invoice_date__gte=start, invoice_date__lte=end)
                    .prefetch_related('lines').order_by('invoice_date', 'id')):
            rows += [dict(r, kind='Invoice', obj=inv) for r in check_invoice(inv)]
    if 'payslips' in which:
        qs = (EmployeePayroll.objects.filter(status=EmployeePayroll.FINALIZED, is_current=True)
              .select_related('period', 'employee').prefetch_related('earnings', 'deductions'))
        for p in qs:
            if start <= p.period.end and p.period.start <= end:
                rows += [dict(r, kind='Payslip', obj=p) for r in check_payslip(p)]
    summary = {k: sum(1 for r in rows if r['status'] == k) for k in LABELS}
    return rows, summary
