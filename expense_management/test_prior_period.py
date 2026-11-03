"""Prior-period expenses: validation, posting to expense.prior_period on the actual date, and the shared
Include / Exclude / Only reporting filter (services, reports, builder, ledger P&L)."""
from datetime import date
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.http import QueryDict

from accounting import postings
from accounting import services as acc
from accounting.models import Account, FinancialYear
from reporting import builder, ledger as LG, periods as P, prior as PR, reports as R, services as RS

from . import ledger, services
from .tests import Base


def fy(y):
    return FinancialYear.objects.get_or_create(start_year=y, defaults={'start_date': date(y, 4, 1),
                                                                       'end_date': date(y + 1, 3, 31)})[0]


class PriorPeriodTests(Base):
    def setUp(self):
        super().setUp()
        self.fy25, self.fy26 = fy(2025), fy(2026)

    def prior(self, **kw):
        kw.setdefault('description', 'Laptop')
        return self.expense(is_prior_period=True, relates_to_fy=self.fy25, prior_period_note='Laptop bought FY 2025-26',
                            **kw)

    def test_validation(self):
        with self.assertRaises(ValidationError) as cm:
            self.expense(is_prior_period=True)
        self.assertIn('relates_to_fy', cm.exception.message_dict)
        with self.assertRaises(ValidationError) as cm:
            self.expense(relates_to_fy=self.fy25)
        self.assertIn('is_prior_period', cm.exception.message_dict)
        with self.assertRaises(ValidationError) as cm:
            self.expense(is_prior_period=True, relates_to_fy=self.fy26)  # same FY as 10 May 2026
        self.assertIn('relates_to_fy', cm.exception.message_dict)
        self.assertTrue(self.prior().pk)

    def test_posts_to_prior_period_account_on_actual_date(self):
        e = self.prior()
        cur = postings.current(ledger.expense_key(e))
        self.assertEqual(cur.date, date(2026, 5, 10))
        pp = acc.mapped_account('expense.prior_period')
        self.assertEqual(pp.code, '5990')
        self.assertEqual(acc.balance(pp), D('11800.00'))
        self.assertEqual(acc.balance(acc.mapped_account('expense.default')), D('0.00'))
        e.save()
        self.assertEqual(postings.history(ledger.expense_key(e)).count(), 1)  # idempotent
        e.is_prior_period, e.relates_to_fy = False, None
        e.full_clean()
        e.save()
        self.assertEqual(acc.balance(pp), D('0.00'))
        self.assertEqual(acc.balance(acc.mapped_account('expense.default')), D('11800.00'))

    def test_claim_prior_period(self):
        c = self.claim(is_prior_period=True, relates_to_fy=self.fy25)
        services.submit_claim(c)
        services.approve_claim(c)
        self.assertEqual(acc.balance(acc.mapped_account('expense.prior_period')), D('5000.00'))
        with self.assertRaises(ValidationError):
            self.claim(is_prior_period=True, relates_to_fy=self.fy26)

    def test_filter_modes_services_reports_builder_ledger(self):
        self.expense()
        self.prior()
        s, e = date(2026, 4, 1), date(2027, 3, 31)
        self.assertEqual(len(RS.company_expenses(s, e)), 2)
        with PR.scope('exclude'):
            self.assertEqual(len(RS.company_expenses(s, e)), 1)
            self.assertEqual(RS.prior_period_subtotal(s, e), D('0'))
        with PR.scope('only'):
            self.assertEqual([r['is_prior_period'] for r in RS.company_expenses(s, e)], [True])
        self.assertEqual(RS.prior_period_subtotal(s, e), D('11800.00'))
        self.assertEqual(PR.current(), 'include')
        for mode, total in (('include', D('23600.00')), ('exclude', D('11800.00')), ('only', D('11800.00'))):
            q = QueryDict(f'period=fy&fy=2026&prior={mode}')
            r = R.build('company_expenses', q)
            self.assertEqual(r.period.label, 'FY 2026-27')  # label unchanged by the filter
            card = [c for c in r.cards if c['label'] == 'Prior-period expenses (subtotal)'][0]
            self.assertEqual(card['value'], D('0') if mode == 'exclude' else D('11800.00'))
            if mode != 'include':
                self.assertIn(('Prior-period expenses', PR.LABELS[mode]), r.filters)
                self.assertEqual(r.period.params.get('prior'), mode)
            pnl = R.build('ledger_pnl', q)
            tot = [c for c in pnl.cards if c['label'] == 'Expenses'][0]['value']
            self.assertEqual(tot, total, mode)
            rep = builder.build(QueryDict(f'dataset=company_expenses&period=fy&fy=2026&prior={mode}'))[0]
            n = len([s_ for s_ in rep.tables[0].styles if s_ == ''])
            self.assertEqual(n, 2 if mode == 'include' else 1)
        x = LG.profit_and_loss(s, e)
        self.assertEqual(x['prior_period_total'], D('11800.00'))
        bs = LG.balance_sheet(e)
        self.assertTrue(bs['balanced'])
        with PR.scope('exclude'):  # balance sheet earnings ignore the filter
            self.assertEqual(LG.balance_sheet(e)['current_year_earnings'], bs['current_year_earnings'])

    def test_summary_has_subtotal_row(self):
        self.expense()
        self.prior()
        r = R.build('exp_summary', QueryDict('period=fy&fy=2026'))
        t = r.tables[0]
        sub = [row for row, st in zip(t.rows, t.styles) if st == 'subtotal']
        self.assertEqual(sub[0][0], 'of which prior-period expenses')
        self.assertEqual(sub[0][2], D('11800.00'))