"""Stage 5: ledger-based reports (TB, GL, P&L, BS, cash flow, ageing, payables, day book, reconciliation)."""
from datetime import date
from decimal import Decimal as D

from django.test import TestCase, override_settings
from django.urls import reverse

from accounting import services as acc
from accounting.models import Account
from accounting.postings import L
from bank_management.tests import make_bank
from expense_management.models import Vendor
from invoice_app.models import Client

from . import exports, ledger as LG, reports as R
from .core import Col, Report, Table

FY = {'period': 'fy', 'fy': '2026'}
AS_OF = date(2026, 10, 31)
OCT = {'period': 'custom', 'date_from': '2026-04-01', 'date_to': '2026-10-31'}


class Scenario(TestCase):
    @classmethod
    def setUpTestData(cls):
        m = acc.mapped_account
        cls.bank, cls.bank2 = make_bank(), make_bank('ICICI', number='000401234567')
        cls.cust, cls.cust2 = Client.objects.create(name='Alpha Inc'), Client.objects.create(name='Beta LLC')
        cls.vendor = Vendor.objects.create(name='AWS')
        cls.equity = Account.objects.filter(account_type='equity', is_group=False).first() or \
            Account.objects.create(code='3999', name='Capital', account_type='equity')
        ar, rev, ap, exp = m('invoice.ar'), m('invoice.revenue'), m('expense.ap'), m('expense.default')
        bk = cls.bank.ledger_account
        c, v = ('customer', cls.cust.pk), ('vendor', cls.vendor.pk)

        def post(k, d, *lines):
            acc.post_from_source(f'test5:{k}', d, k, list(lines), source_type='test5', source_id=k)
        post('capital', date(2025, 4, 1), L(bk, D('100000')), L(cls.equity, credit=D('100000')))
        post('py_exp', date(2026, 1, 15), L(exp, D('500')), L(bk, credit=D('500')))
        post('inv1', date(2026, 4, 10), L(ar, D('11800'), party=c), L(rev, credit=D('10000')),
             L(m('gst.output.igst'), credit=D('1800')))
        post('inv3', date(2026, 9, 15), L(ar, D('3000'), party=c), L(rev, credit=D('3000')))
        post('inv2', date(2026, 10, 15), L(ar, D('5000'), party=c), L(rev, credit=D('5000')))
        post('inv_b', date(2026, 10, 20), L(ar, D('700'), party=('customer', cls.cust2.pk)), L(rev, credit=D('700')))
        post('rcpt', date(2026, 9, 1), L(bk, D('6000')), L(ar, credit=D('6000'), party=c))
        post('bill', date(2026, 6, 1), L(exp, D('2000')), L(ap, credit=D('2000'), party=v))
        post('pay', date(2026, 10, 1), L(ap, D('1500'), party=v), L(bk, credit=D('1400')),
             L(m('expense.tds_payable'), credit=D('100')))
        post('xfer', date(2026, 10, 5), L(cls.bank2.ledger_account, D('1000')), L(bk, credit=D('1000')))

    def test_trial_balance_balanced(self):
        rows, tot = LG.trial_balance(date(2026, 4, 1), AS_OF)
        self.assertTrue(tot['balanced'])
        self.assertEqual(tot['closing_dr'], tot['closing_cr'])
        self.assertEqual(tot['debit'], D('31000.00'))  # 11800+3000+5000+700+6000+2000+1500+1000
        rep = R.build('ledger_tb', OCT)
        self.assertEqual(rep.cards[2]['value'], 'Yes')
        self.assertFalse(rep.statement)

    def test_unbalanced_tb_is_highlighted(self):
        from unittest import mock
        with mock.patch.object(LG, 'trial_balance', return_value=([], dict(
                opening_dr=D(0), opening_cr=D(0), debit=D(1), credit=D(0), closing_dr=D(1), closing_cr=D(0),
                difference=D(1), period_difference=D(1), balanced=False))):
            rep = R.build('ledger_tb', OCT)
        self.assertIn('NOT BALANCED', rep.statement)

    def test_pnl_ties_to_tb(self):
        p = LG.profit_and_loss(date(2026, 4, 1), AS_OF)
        self.assertEqual(p['total_income'], D('18700.00'))
        self.assertEqual(p['total_expenses'], D('2000.00'))
        rows, _ = LG.trial_balance(date(2026, 4, 1), AS_OF)
        tb = sum((r['credit'] - r['debit'] for r in rows if r['account'].account_type in ('income', 'expense')), D(0))
        self.assertEqual(p['profit'], tb)
        self.assertEqual(R.build('ledger_pnl', OCT).statement, "Management P&L (ledger-based), not statutory")

    def test_balance_sheet_balances(self):
        b = LG.balance_sheet(AS_OF)
        self.assertTrue(b['balanced'])
        self.assertEqual(b['difference'], D('0.00'))
        self.assertEqual(b['current_year_earnings'], D('16700.00'))
        self.assertEqual(b['prior_unclosed_earnings'], D('-500.00'))
        self.assertEqual(b['total_assets'], b['total_liabilities'] + b['total_equity'] + D('16200.00'))
        rep = R.build('ledger_bs', OCT)
        self.assertEqual(rep.tables[0].total[2], D('0.00'))

    def test_ageing_buckets(self):
        a = LG.ageing('invoice.ar', 'customer', AS_OF)
        alpha = next(r for r in a['rows'] if r['name'] == 'Alpha Inc')
        self.assertEqual(alpha['buckets'], {'0-30': D('5000.00'), '31-60': D('3000.00'), '61-90': D('0.00'),
                                            '90+': D('5800.00')})
        self.assertEqual(a['total'], D('14500.00'))
        self.assertEqual(a['total'], a['account_balance'])
        ap = LG.ageing('expense.ap', 'vendor', AS_OF)
        self.assertEqual(ap['rows'][0]['buckets']['90+'], D('500.00'))
        self.assertEqual(ap['total'], D('500.00'))
        self.assertEqual(LG.bucket(30), '0-30')
        self.assertEqual(LG.bucket(31), '31-60')
        self.assertEqual(LG.bucket(90), '61-90')
        self.assertEqual(LG.bucket(91), '90+')

    def test_cash_flow_direct(self):
        c = LG.cash_flow(date(2026, 4, 1), AS_OF)
        self.assertEqual(c['opening'], D('99500.00'))
        self.assertEqual(c['net'], D('4600.00'))  # +6000 receipt, -1500 AP (1400 cash), transfers net 0
        self.assertEqual(c['closing'], D('104100.00'))
        self.assertEqual(c['difference'], D('0.00'))
        self.assertEqual(c['transfers'], D('0.00'))

    def test_payables_and_gl(self):
        pay = {r['account'].pk: r['balance'] for r in LG.payables(AS_OF)}
        self.assertEqual(pay[acc.mapped_account('expense.tds_payable').pk], D('100.00'))
        self.assertEqual(pay[acc.mapped_account('gst.output.igst').pk], D('1800.00'))
        g = LG.general_ledger(date(2026, 4, 1), AS_OF, self.bank.ledger_account)[0]
        self.assertEqual(g['opening'], D('99500.00'))
        self.assertEqual(g['closing'], D('103100.00'))
        self.assertEqual(g['lines'][-1]['balance'], g['closing'])

    def test_reconciliation_builds(self):
        rep = R.build('ledger_recon', OCT)
        self.assertEqual(len(rep.tables[0].rows), 8)
        rows = {r[0]: r for r in rep.tables[0].rows}
        self.assertEqual(rows['Revenue (invoice taxable value)'][2], D('18700.00'))
        self.assertEqual(rows['Accounts payable (as of end)'][2], D('500.00'))
        for r in rep.tables[0].rows:
            self.assertEqual(r[3], r[1] - r[2])
            self.assertTrue(r[4])

    def test_masking(self):
        s = 'PAN ABCDE1234F, A/c 50100012345678, Aadhaar 1234 5678 9012, inv RPT0001, GSTIN 29ABCDE1234F1Z5'
        m = exports.mask_text(s)
        for raw in ('ABCDE1234F,', '50100012345678', '1234 5678 9012'):
            self.assertNotIn(raw, m)
        self.assertIn('XXXXXX234F', m)
        self.assertIn('XXXXXXXXXX5678', m)
        self.assertIn('XXXXXXXX9012', m)
        self.assertIn('29ABCDE1234F1Z5', m)
        rep = Report('x', 'X', 'Ledger', 'b', R.P.resolve(OCT))
        t = Table('t', [Col('a'), Col('b', 'money')])
        t.add([s, D('1.00')])
        rep.tables.append(t)
        rep.filters.append(('Bank', '000401234567'))
        for blob in (exports.report_csv(rep), exports.report_xlsx(rep)):
            self.assertNotIn(b'50100012345678', blob)
            self.assertNotIn(b'000401234567', blob)
        import zipfile, io
        z = zipfile.ZipFile(io.BytesIO(exports.report_xlsx(rep)))
        xml = b''.join(z.read(n) for n in z.namelist())
        self.assertNotIn(b'50100012345678', xml)
        self.assertNotIn(b'ABCDE1234F,', xml)
        self.assertEqual(t.rows[0][0], s)  # original report untouched


@override_settings(ALLOWED_HOSTS=['testserver', '127.0.0.1', 'localhost'])
class LedgerViewTests(Scenario):
    def test_all_reports_and_exports(self):
        for key, _, _ in R.LR.ITEMS:
            url = reverse('reporting:report', args=[key])
            r = self.client.get(url, OCT)
            self.assertEqual(r.status_code, 200, key)
            self.assertContains(r, 'Export CSV')
            for fmt, sig in (('xlsx', b'PK'), ('pdf', b'%PDF'), ('csv', b'\xef\xbb\xbf')):
                r = self.client.get(url, dict(OCT, format=fmt))
                self.assertEqual(r.status_code, 200, (key, fmt))
                self.assertTrue(r.content.startswith(sig), (key, fmt))
        r = self.client.get(reverse('reporting:report', args=['ledger_gl']),
                            dict(OCT, account=self.bank.ledger_account.pk, format='csv'))
        self.assertIn(b'103100.00', r.content)
        self.assertNotIn(b'50100012345678', r.content)

    def test_section_and_link(self):
        r = self.client.get(reverse('reporting:section', args=['ledger']))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Trial Balance')
        r = self.client.get(reverse('reporting:report', args=['pnl']), OCT)
        self.assertContains(r, 'See ledger reports')
        r = self.client.get(reverse('reporting:report', args=['ledger_pnl']), OCT)
        self.assertNotContains(r, 'See ledger reports')
