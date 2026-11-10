"""Read-only registers built from source documents and cross-checked against the ledger."""
from collections import OrderedDict, defaultdict
from datetime import date

from django.db.models import Q, Sum

from accounting import services as acc
from accounting.models import JournalEntry, JournalLine
from accounting.money import ZERO, q2

from .models import PAYMENT_KINDS, GstInputClaim, StatutoryPayment
from .services import gst_input_key


def months_between(start, end):
    y, mth, out = start.year, start.month, []
    while (y, mth) <= (end.year, end.month):
        out.append((y, mth))
        y, mth = (y + 1, 1) if mth == 12 else (y, mth + 1)
    return out


def _label(y, mth):
    return date(y, mth, 1).strftime('%b %Y')


def ledger_movement(key, start, end, credit_positive=True):
    """Net movement on a mapped account per (year, month), excluding statutory payments (and their reversals)."""
    qs = (JournalLine.objects.filter(account=acc.mapped_account(key), entry__status__in=acc.BOOKED,
                                     entry__date__gte=start, entry__date__lte=end)
          .exclude(entry__source_type='statutory_payment')
          .exclude(entry__reversal_of__source_type='statutory_payment'))
    out = defaultdict(lambda: ZERO)
    for ln in qs.select_related('entry'):
        v = (ln.credit - ln.debit) if credit_positive else (ln.debit - ln.credit)
        out[(ln.entry.date.year, ln.entry.date.month)] += v
    return out


def paid_by_period(kinds, start=None, end=None):
    out = defaultdict(lambda: ZERO)
    for p in StatutoryPayment.objects.filter(cancelled=False, kind__in=kinds):
        out[(p.kind, p.period_year, p.period_month)] += p.amount
    return out


# ------------------------------------------------------------------ output GST
def output_gst(start, end):
    from invoice_app.models import CreditNote, Invoice
    src = OrderedDict(((y, mth), {'taxable': ZERO, 'cgst': ZERO, 'sgst': ZERO, 'igst': ZERO, 'docs': 0})
                      for y, mth in months_between(start, end))
    invs = Invoice.objects.filter(status=Invoice.FINALIZED, invoice_date__gte=start, invoice_date__lte=end)
    for i in invs:
        r = src[(i.invoice_date.year, i.invoice_date.month)]
        if not (i.cgst_amount or i.sgst_amount or i.igst_amount):
            continue
        r['taxable'] += i.taxable_value
        r['cgst'] += i.cgst_amount
        r['sgst'] += i.sgst_amount
        r['igst'] += i.igst_amount
        r['docs'] += 1
    for cn in CreditNote.objects.filter(status='active', date__gte=start, date__lte=end,
                                        invoice__status=Invoice.FINALIZED):
        r = src[(cn.date.year, cn.date.month)]
        r['taxable'] -= cn.taxable_amount
        r['cgst'] -= cn.cgst_amount
        r['sgst'] -= cn.sgst_amount
        r['igst'] -= cn.igst_amount
    led = {k: ledger_movement(f'gst.output.{k}', start, end) for k in ('cgst', 'sgst', 'igst')}
    paid = paid_by_period(['gst_cgst', 'gst_sgst', 'gst_igst'])
    rows = []
    for (y, mth), r in src.items():
        row = dict(r, label=_label(y, mth), year=y, month=mth)
        row['total'] = q2(r['cgst'] + r['sgst'] + r['igst'])
        row['ledger'] = q2(sum((led[k][(y, mth)] for k in led), ZERO))
        row['diff'] = q2(row['ledger'] - row['total'])
        row['paid'] = q2(sum((paid[(f'gst_{k}', y, mth)] for k in ('cgst', 'sgst', 'igst')), ZERO))
        rows.append(row)
    tot = {k: q2(sum((r[k] for r in rows), ZERO)) for k in ('taxable', 'cgst', 'sgst', 'igst', 'total', 'ledger',
                                                            'diff', 'paid')}
    return rows, tot


# ------------------------------------------------------------------ input GST
def input_gst(start, end, status=''):
    from expense_management.models import CompanyExpense
    qs = (CompanyExpense.objects.filter(expense_date__gte=start, expense_date__lte=end)
          .filter(Q(gst_amount__gt=0) | Q(cgst_amount__gt=0) | Q(sgst_amount__gt=0) | Q(igst_amount__gt=0))
          .select_related('vendor', 'category').order_by('expense_date', 'id'))
    claims = {c.expense_id: c for c in GstInputClaim.objects.filter(expense__in=qs)}
    keys = {gst_input_key(e) for e in qs}
    live = {}
    for entry in (JournalEntry.objects.filter(source_key__startswith='gst_input:', status=JournalEntry.POSTED)
                  .order_by('id')):
        base = entry.source_key.split(':v')[0]
        if base in keys:
            live[base] = entry
    rows = []
    for e in qs:
        c = claims.get(e.pk)
        st = c.status if c else GstInputClaim.REVIEW
        if status and st != status:
            continue
        rate = e.exchange_rate or 1
        r = {'e': e, 'claim': c, 'status': st,
             'status_label': dict(GstInputClaim.STATUSES)[st], 'reason': c.reason if c else '',
             'taxable': q2(e.amount_before_tax * rate), 'cgst': q2(e.cgst_amount * rate),
             'sgst': q2(e.sgst_amount * rate), 'igst': q2(e.igst_amount * rate), 'gst': q2(e.gst_amount * rate),
             'posted': live.get(gst_input_key(e)), 'cancelled': e.is_cancelled}
        r['no_split'] = not (e.cgst_amount or e.sgst_amount or e.igst_amount)
        rows.append(r)
    tot = {k: q2(sum((r[k] for r in rows), ZERO)) for k in ('taxable', 'cgst', 'sgst', 'igst', 'gst')}
    tot['eligible'] = q2(sum((r['cgst'] + r['sgst'] + r['igst'] for r in rows if r['status'] == 'eligible'), ZERO))
    return rows, tot


# ------------------------------------------------------------------ TDS
def tds_register(start, end):
    from employee_management.models import EmployeePayroll, PayrollDeduction
    from expense_management.models import CompanyExpensePayment
    from invoice_app.models import InvoicePayment
    receivable = []
    for p in (InvoicePayment.objects.filter(receipt_date__gte=start, receipt_date__lte=end)
              .select_related('invoice__client').order_by('receipt_date', 'id')):
        tds = q2((p.deductions or 0) - (p.bank_charges or 0))
        if tds > 0:
            receivable.append({'date': p.receipt_date, 'party': p.invoice.client.name,
                               'doc': p.invoice.invoice_number, 'gross': q2(p.amount + (p.deductions or 0)),
                               'tds': tds, 'ref': p.reference})
    vendor = []
    for p in (CompanyExpensePayment.objects.filter(payment_date__gte=start, payment_date__lte=end, tds_amount__gt=0)
              .select_related('expense__vendor').order_by('payment_date', 'id')):
        e, rate = p.expense, p.expense.exchange_rate or 1
        vendor.append({'date': p.payment_date, 'party': e.vendor.name if e.vendor_id else '-',
                       'pan_gstin': e.vendor.gstin if e.vendor_id else '', 'doc': f'{e.expense_id} {e.invoice_number}',
                       'gross': q2((p.amount + p.tds_amount) * rate), 'tds': q2(p.tds_amount * rate),
                       'ref': p.reference, 'period': (p.payment_date.year, p.payment_date.month)})
    salary = []
    ded = (PayrollDeduction.objects.filter(code='tds', side='employee', amount__gt=0,
                                           payroll__status=EmployeePayroll.FINALIZED, payroll__is_current=True)
           .select_related('payroll__period', 'payroll__employee').order_by('payroll__period__year',
                                                                           'payroll__period__month', 'payroll__employee__employee_id'))
    for d in ded:
        per = d.payroll.period
        if not (start <= per.end and per.start <= end):
            continue
        salary.append({'date': per.end, 'party': d.payroll.snap_name or d.payroll.employee.full_name,
                       'doc': f'{d.payroll.snap_employee_code} {per.label}', 'gross': d.payroll.gross_earnings,
                       'tds': d.amount, 'ref': f'rev {d.payroll.revision}', 'period': (per.year, per.month)})
    vendor_periods = {r['period'] for r in vendor}
    salary_periods = {r['period'] for r in salary}
    deposits = list(StatutoryPayment.objects.filter(kind__in=['tds_vendor', 'tds_salary'], cancelled=False)
                    .order_by('date', 'id'))
    paid = [p for p in deposits
            if (p.kind == 'tds_vendor' and (p.period_year, p.period_month) in vendor_periods)
            or (p.kind == 'tds_salary' and (p.period_year, p.period_month) in salary_periods)]

    def tot(rows):
        return q2(sum((r['tds'] for r in rows), ZERO))
    t = {'receivable': tot(receivable), 'vendor': tot(vendor), 'salary': tot(salary),
         'paid_vendor': q2(sum((p.amount for p in paid if p.kind == 'tds_vendor'), ZERO)),
         'paid_salary': q2(sum((p.amount for p in paid if p.kind == 'tds_salary'), ZERO))}
    t['ledger_receivable'] = acc.balance(acc.mapped_account('invoice.tds_receivable'), None, end)
    t['due_vendor'] = q2(t['vendor'] - t['paid_vendor'])
    t['due_salary'] = q2(t['salary'] - t['paid_salary'])
    return {'receivable': receivable, 'vendor': vendor, 'salary': salary, 'paid': paid, 'totals': t}


# ------------------------------------------------------------------ PF / ESI / PT
PAYROLL_STAT = [('pf', 'employee', 'PF (employee)'), ('pf', 'employer', 'PF (employer)'),
                ('esi', 'employee', 'ESI (employee)'), ('esi', 'employer', 'ESI (employer)'),
                ('professional_tax', 'employee', 'Professional tax')]
PAY_KIND = {'pf': 'pf', 'esi': 'esi', 'professional_tax': 'pt'}


def payroll_statutory(start, end):
    from employee_management.models import EmployeePayroll, PayrollDeduction, PayrollPeriod
    pers = [p for p in PayrollPeriod.objects.order_by('year', 'month') if start <= p.end and p.start <= end]
    paid = paid_by_period(['pf', 'esi', 'pt'])
    rows = []
    keys = [(c, s) for c, s, _ in PAYROLL_STAT]
    for per in pers:
        qs = PayrollDeduction.objects.filter(payroll__period=per, payroll__status=EmployeePayroll.FINALIZED,
                                             payroll__is_current=True)
        totals = {(row['code'], row['side']): q2(row['t'] or ZERO)
                  for row in qs.values('code', 'side').annotate(t=Sum('amount'))}
        amt = {key: totals.get(key, ZERO) for key in keys}
        r = {'period': per, 'label': per.label, 'amounts': [amt[(c, s)] for c, s, _ in PAYROLL_STAT]}
        due = {'pf': amt[('pf', 'employee')] + amt[('pf', 'employer')],
               'esi': amt[('esi', 'employee')] + amt[('esi', 'employer')],
               'pt': amt[('professional_tax', 'employee')]}
        r['groups'] = [{'kind': k, 'due': q2(due[k]), 'paid': q2(paid[(k, per.year, per.month)]),
                        'balance': q2(due[k] - paid[(k, per.year, per.month)])} for k in ('pf', 'esi', 'pt')]
        rows.append(r)
    ledger = [(lab, acc.balance(acc.mapped_account(key), None, end))
              for lab, key in (('PF payable', 'payroll.pf_payable'), ('ESI payable', 'payroll.esi_payable'),
                               ('PT payable', 'payroll.pt_payable'))]
    return rows, [lab for _, _, lab in PAYROLL_STAT], ledger
