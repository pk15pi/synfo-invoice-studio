"""Stage 5: ledger-based financial figures. Every number here is computed from accounting.JournalLine (booked
entries only: posted, plus reversed originals which sit next to their mirror entries) so the reports tie to the
trial balance by construction. Signs: ``net`` is debit-positive (debit - credit) unless a function says otherwise.
Nothing here writes to the database."""
from collections import OrderedDict, defaultdict
from datetime import date

from django.db.models import Q, Sum

from accounting import services as acc
from accounting.models import Account, AccountMapping, JournalEntry, JournalLine
from accounting.money import ZERO, q2

from .periods import fy_start_year

BEGIN = date(1900, 1, 1)
A, L_, E, I, X = Account.ASSET, Account.LIABILITY, Account.EQUITY, Account.INCOME, Account.EXPENSE
CASH_KEYS = ('invoice.receipt_bank', 'payroll.payment_bank', 'expense.payment_bank', 'claim.payment_bank',
             'cash.default')
BUCKETS = ['0-30', '31-60', '61-90', '90+']
PARTY_TYPES = {'customer': 'Customer', 'vendor': 'Vendor', 'employee': 'Employee'}


def booked(start=None, end=None):
    qs = JournalLine.objects.filter(entry__status__in=acc.BOOKED)
    if start:
        qs = qs.filter(entry__date__gte=start)
    if end:
        qs = qs.filter(entry__date__lte=end)
    return qs


def sums(start=None, end=None, **flt):
    """{account_id: (debit, credit)} for booked lines dated start..end."""
    out = {}
    for aid, d, c in booked(start, end).filter(**flt).values('account_id').annotate(
            d=Sum('debit'), c=Sum('credit')).values_list('account_id', 'd', 'c'):
        out[aid] = (q2(d), q2(c))
    return out


def natural(acct, net):
    """Debit-positive net -> the account's natural sign (liability/equity/income come back positive)."""
    return net if acct.account_type in Account.DEBIT_NORMAL else -net


def accounts():
    return list(Account.objects.filter(is_group=False).order_by('code'))


def mapped(key):
    mp = AccountMapping.objects.select_related('account').filter(key=key).first()
    return mp.account if mp and mp.account_id else None


# ------------------------------------------------------------------ trial balance
def trial_balance(start, end):
    """Per posting account: opening (before start), period debit/credit, closing. Closing split into Dr/Cr."""
    op, mv = sums(None, None if start is None else date.fromordinal(start.toordinal() - 1)), sums(start, end)
    rows, tot = [], dict(opening_dr=ZERO, opening_cr=ZERO, debit=ZERO, credit=ZERO, closing_dr=ZERO, closing_cr=ZERO)
    for a in accounts():
        od, oc = op.get(a.pk, (ZERO, ZERO))
        d, c = mv.get(a.pk, (ZERO, ZERO))
        o, cl = q2(od - oc), q2(od - oc + d - c)
        if not (o or d or c):
            continue
        r = dict(account=a, opening=o, debit=d, credit=c, closing=cl,
                 opening_dr=max(o, ZERO), opening_cr=max(-o, ZERO), closing_dr=max(cl, ZERO), closing_cr=max(-cl, ZERO))
        for k in tot:
            tot[k] += r[k]
        rows.append(r)
    tot = {k: q2(v) for k, v in tot.items()}
    tot['difference'] = q2(tot['closing_dr'] - tot['closing_cr'])
    tot['period_difference'] = q2(tot['debit'] - tot['credit'])
    tot['balanced'] = tot['difference'] == 0 and tot['period_difference'] == 0
    return rows, tot


# ------------------------------------------------------------------ P&L / balance sheet
def profit_and_loss(start, end):
    mv = sums(start, end, account__account_type__in=[I, X])
    inc, exp = [], []
    for a in Account.objects.filter(pk__in=mv.keys()).order_by('code'):
        d, c = mv[a.pk]
        (inc if a.account_type == I else exp).append(dict(account=a, amount=natural(a, q2(d - c))))
    ti, te = q2(sum((r['amount'] for r in inc), ZERO)), q2(sum((r['amount'] for r in exp), ZERO))
    return dict(income=inc, expenses=exp, total_income=ti, total_expenses=te, profit=q2(ti - te))


def balance_sheet(as_of):
    fy0 = date(fy_start_year(as_of), 4, 1)
    bal = sums(None, as_of, account__account_type__in=[A, L_, E])
    sec = {A: [], L_: [], E: []}
    for a in Account.objects.filter(pk__in=bal.keys()).order_by('code'):
        d, c = bal[a.pk]
        v = natural(a, q2(d - c))
        if v:
            sec[a.account_type].append(dict(account=a, amount=v))
    cye = profit_and_loss(fy0, as_of)['profit']
    pye = profit_and_loss(None, date.fromordinal(fy0.toordinal() - 1))['profit']
    t = {k: q2(sum((r['amount'] for r in v), ZERO)) for k, v in sec.items()}
    total_le = q2(t[L_] + t[E] + cye + pye)
    return dict(as_of=as_of, fy_start=fy0, assets=sec[A], liabilities=sec[L_], equity=sec[E], total_assets=t[A],
                total_liabilities=t[L_], total_equity=t[E], current_year_earnings=cye, prior_unclosed_earnings=pye,
                total_liabilities_equity=total_le, difference=q2(t[A] - total_le), balanced=t[A] == total_le)


# ------------------------------------------------------------------ general ledger / day book
def general_ledger(start, end, account=None):
    """[{account, opening, lines:[{line, balance}], debit, credit, closing}] (debit-positive balances)."""
    accts = [account] if account else accounts()
    op = sums(None, date.fromordinal(start.toordinal() - 1))
    qs = booked(start, end).select_related('entry', 'account').order_by('account__code', 'entry__date', 'entry_id', 'id')
    if account:
        qs = qs.filter(account=account)
    by = defaultdict(list)
    for ln in qs:
        by[ln.account_id].append(ln)
    out = []
    for a in accts:
        od, oc = op.get(a.pk, (ZERO, ZERO))
        bal = q2(od - oc)
        lines = by.get(a.pk, [])
        if not account and not bal and not lines:
            continue
        g = dict(account=a, opening=bal, lines=[], debit=ZERO, credit=ZERO)
        for ln in lines:
            bal = q2(bal + ln.debit - ln.credit)
            g['debit'] += ln.debit
            g['credit'] += ln.credit
            g['lines'].append(dict(line=ln, balance=bal))
        g.update(debit=q2(g['debit']), credit=q2(g['credit']), closing=bal)
        out.append(g)
    return out


def day_book(start, end):
    return (booked(start, end).select_related('entry', 'account')
            .order_by('entry__date', 'entry__number', 'entry_id', 'id'))


# ------------------------------------------------------------------ cash flow (direct)
def cash_accounts():
    from bank_management.models import BankAccount
    ids = set(BankAccount.objects.values_list('ledger_account_id', flat=True))
    ids |= set(AccountMapping.objects.filter(key__in=CASH_KEYS, account__isnull=False).values_list('account_id', flat=True))
    return Account.objects.filter(pk__in=ids).order_by('code')


def cash_flow(start, end):
    """Direct method: every booked entry touching a bank/cash account; its cash effect is attributed to the
    counter-accounts (inflow = credit to the counter-account). Entries with only bank/cash lines are transfers."""
    cash = list(cash_accounts())
    ids = {a.pk for a in cash}
    o = sums(None, date.fromordinal(start.toordinal() - 1), account_id__in=ids)
    opening = q2(sum((d - c for d, c in o.values()), ZERO))
    entry_ids = booked(start, end).filter(account_id__in=ids).values_list('entry_id', flat=True)
    groups = defaultdict(lambda: ZERO)
    transfers = ZERO
    lines = JournalLine.objects.filter(entry_id__in=entry_ids).select_related('account')
    per_entry = defaultdict(list)
    for ln in lines:
        per_entry[ln.entry_id].append(ln)
    for ls in per_entry.values():
        other = [ln for ln in ls if ln.account_id not in ids]
        if not other:
            transfers += sum((ln.debit - ln.credit for ln in ls), ZERO)
        for ln in other:
            groups[ln.account] += ln.credit - ln.debit
    rows = [dict(account=a, amount=q2(v)) for a, v in sorted(groups.items(), key=lambda kv: kv[0].code) if q2(v)]
    inflow = q2(sum((r['amount'] for r in rows if r['amount'] > 0), ZERO))
    outflow = q2(sum((-r['amount'] for r in rows if r['amount'] < 0), ZERO))
    net = q2(inflow - outflow + transfers)
    cl = sums(None, end, account_id__in=ids)
    closing = q2(sum((d - c for d, c in cl.values()), ZERO))
    return dict(accounts=cash, opening=opening, rows=rows, inflow=inflow, outflow=outflow, transfers=q2(transfers),
                net=net, closing=closing, difference=q2(opening + net - closing))


# ------------------------------------------------------------------ parties / ageing
def party_names(party_type, ids):
    ids = [i for i in ids if str(i).isdigit()]
    if party_type == 'customer':
        from invoice_app.models import Client
        return {str(k): v for k, v in Client.objects.filter(pk__in=ids).values_list('pk', 'name')}
    if party_type == 'vendor':
        from expense_management.models import Vendor
        return {str(k): v for k, v in Vendor.objects.filter(pk__in=ids).values_list('pk', 'name')}
    if party_type == 'employee':
        from employee_management.models import Employee
        return {str(e.pk): f'{e.employee_id} {e.full_name}' for e in Employee.objects.filter(pk__in=ids)}
    return {}


def party_balances(account, party_type, as_of):
    """{party_id: debit-positive balance} on one account, plus the untagged remainder under ''."""
    out = {}
    for pt, pid, d, c in booked(None, as_of).filter(account=account).values('party_type', 'party_id').annotate(
            d=Sum('debit'), c=Sum('credit')).values_list('party_type', 'party_id', 'd', 'c'):
        k = pid if pt == party_type else ''
        out[k] = q2(out.get(k, ZERO) + d - c)
    return out


def bucket(days):
    return BUCKETS[0] if days <= 30 else BUCKETS[1] if days <= 60 else BUCKETS[2] if days <= 90 else BUCKETS[3]


def ageing(account_key, party_type, as_of):
    """FIFO ageing from party-tagged lines. AR (debit-normal): debits are items, credits settle the oldest first.
    AP (credit-normal): credits are items, debits settle. Unsettled remainder is aged by entry date."""
    acct = acc.mapped_account(account_key)
    sign = 1 if acct.account_type in Account.DEBIT_NORMAL else -1
    per = defaultdict(list)
    for ln in booked(None, as_of).filter(account=acct).exclude(party_id='').select_related('entry').order_by(
            'entry__date', 'entry_id', 'id'):
        if ln.party_type == party_type:
            per[ln.party_id].append(ln)
    names = party_names(party_type, per.keys())
    rows = []
    for pid, ls in per.items():
        items, credit = [], ZERO
        for ln in ls:
            v = sign * (ln.debit - ln.credit)
            if v > 0:
                items.append([ln.entry.date, v])
            else:
                credit += -v
        for it in items:  # FIFO settle
            use = min(it[1], credit)
            it[1] -= use
            credit -= use
        b = OrderedDict((k, ZERO) for k in BUCKETS)
        for d, v in items:
            if v:
                b[bucket((as_of - d).days)] += v
        total = q2(sum(b.values(), ZERO) - credit)
        if not total and not credit:
            continue
        rows.append(dict(party_id=pid, name=names.get(pid, f'{party_type} #{pid}'),
                         buckets={k: q2(v) for k, v in b.items()}, advance=q2(credit), total=total))
    rows.sort(key=lambda r: r['name'])
    tot = {k: q2(sum((r['buckets'][k] for r in rows), ZERO)) for k in BUCKETS}
    untagged = party_balances(acct, party_type, as_of).get('', ZERO) * sign
    return dict(account=acct, rows=rows, totals=tot, advance=q2(sum((r['advance'] for r in rows), ZERO)),
                total=q2(sum((r['total'] for r in rows), ZERO)), untagged=q2(untagged),
                account_balance=q2(sign * (lambda s: s[0] - s[1])(sums(None, as_of, account=acct).get(acct.pk, (ZERO, ZERO)))))


# ------------------------------------------------------------------ payables
PAYABLE_GROUPS = [
    ('Employee payables', ['payroll.salary_payable', 'claim.reimbursement_payable']),
    ('GST', ['gst.output.cgst', 'gst.output.sgst', 'gst.output.igst', 'gst.input.cgst', 'gst.input.sgst',
             'gst.input.igst']),
    ('TDS', ['payroll.tds_payable', 'expense.tds_payable', 'invoice.tds_receivable']),
    ('PF / ESI / PT / other statutory', ['payroll.pf_payable', 'payroll.esi_payable', 'payroll.pt_payable',
                                          'payroll.other_deductions_payable']),
]


def payables(as_of):
    """Per mapped account (deduplicated when several keys share one account): natural-sign balance as of."""
    bal = sums(None, as_of)
    out, seen = [], set()
    for grp, keys in PAYABLE_GROUPS:
        for k in keys:
            a = mapped(k)
            if not a:
                continue
            if a.pk in seen:
                for r in out:
                    if r['account'].pk == a.pk:
                        r['keys'].append(k)
                continue
            seen.add(a.pk)
            d, c = bal.get(a.pk, (ZERO, ZERO))
            out.append(dict(group=grp, account=a, keys=[k], balance=natural(a, q2(d - c))))
    return out


def employee_payable(account_key, as_of):
    a = acc.mapped_account(account_key)
    pb = party_balances(a, 'employee', as_of)
    names = party_names('employee', pb.keys())
    rows = [dict(party_id=k, name=names.get(k, 'Untagged' if not k else f'employee #{k}'), balance=-v)
            for k, v in pb.items() if v]
    rows.sort(key=lambda r: r['name'])
    return dict(account=a, rows=rows, total=q2(sum((r['balance'] for r in rows), ZERO)))


# ------------------------------------------------------------------ reconciliation helpers
def source_movement(source_types, account_types, start, end, natural_sign=True):
    """Net movement on accounts of ``account_types`` from entries of ``source_types`` (natural sign)."""
    t = booked(start, end).filter(entry__source_type__in=source_types, account__account_type__in=account_types
                                  ).aggregate(d=Sum('debit'), c=Sum('credit'))
    net = q2((t['d'] or ZERO) - (t['c'] or ZERO))
    return net if account_types[0] in Account.DEBIT_NORMAL else -net


def account_balance(key, as_of, start=None):
    a = acc.mapped_account(key)
    d, c = sums(start, as_of, account=a).get(a.pk, (ZERO, ZERO))
    return natural(a, q2(d - c))


def account_movement(key, start, end):
    return account_balance(key, end, start)
