"""Stage 4: payroll posting and salary payments."""
from datetime import date
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounting import postings
from accounting import services as acc
from accounting.models import JournalEntry
from reporting import services as RS
from . import payroll as engine
from . import salary_payments as sal
from .models import Department, Employee, EmployeePayroll, PayrollDeduction, PayrollPeriod, SalaryPayment


class PayrollLedgerTests(TestCase):
    def setUp(self):
        dept = Department.objects.create(name='Eng', code='ENG')
        self.e1 = Employee.objects.create(first_name='Asha', last_name='R', department=dept, designation='Dev',
                                          date_of_joining=date(2024, 1, 1), status=Employee.CONFIRMED)
        self.e2 = Employee.objects.create(first_name='Ravi', last_name='K', department=dept, designation='Dev',
                                          date_of_joining=date(2024, 1, 1), status=Employee.CONFIRMED)
        self.per = PayrollPeriod.objects.create(year=2026, month=10)
        self.p1 = self._pr(self.e1, '50000', [('pf', 'employee', '1800'), ('professional_tax', 'employee', '200'),
                                               ('tds', 'employee', '3000'), ('advance', 'employee', '1000'),
                                               ('other_deduction', 'employee', '500'), ('pf', 'employer', '1800'),
                                               ('esi', 'employer', '0')])
        self.p2 = self._pr(self.e2, '20000', [('esi', 'employee', '150'), ('esi', 'employer', '650')], rnd='0.50')

    def _pr(self, emp, gross, deds, rnd='0', period=None, **kw):
        ee = sum((D(a) for c, s, a in deds if s == 'employee'), D('0'))
        er = sum((D(a) for c, s, a in deds if s == 'employer'), D('0'))
        p = EmployeePayroll.objects.create(period=period or self.per, employee=emp, gross_earnings=D(gross),
                                           total_deductions=ee, employer_contributions=er, rounding_adjustment=D(rnd),
                                           net_salary=D(gross) - ee + D(rnd), monthly_salary=D(gross), **kw)
        for c, s, a in deds:
            PayrollDeduction.objects.create(payroll=p, code=c, side=s, label=c, amount=D(a))
        return p

    def bal(self, key, party=None):
        a = acc.mapped_account(key)
        return postings.party_balance(a, *party) if party else acc.balance(a)

    def test_finalize_posts_one_balanced_entry(self):
        engine.finalize([self.p1, self.p2])
        e = postings.current(postings.payroll_key(self.per))
        self.assertEqual(e.date, self.per.end)
        lines = list(e.lines.all())
        self.assertEqual(sum(l.debit for l in lines), sum(l.credit for l in lines))
        self.assertEqual(self.bal('payroll.salary_expense'), D('70000.50'))
        self.assertEqual(self.bal('payroll.employer_contribution'), D('2450.00'))
        self.assertEqual(self.bal('payroll.salary_payable'), D('63350.50'))
        self.assertEqual(self.bal('payroll.salary_payable', ('employee', str(self.e1.pk))), D('-43500.00'))
        self.assertEqual(self.bal('payroll.pf_payable'), D('3600.00'))
        self.assertEqual(self.bal('payroll.esi_payable'), D('800.00'))
        self.assertEqual(self.bal('payroll.pt_payable'), D('200.00'))
        self.assertEqual(self.bal('payroll.tds_payable'), D('3000.00'))
        self.assertEqual(self.bal('payroll.advance'), D('-1000.00'))
        self.assertEqual(self.bal('payroll.other_deductions_payable'), D('500.00'))
        self.assertEqual(JournalEntry.objects.count(), 1)

    def test_partial_finalize_then_rest_and_idempotent(self):
        engine.finalize([self.p1])
        engine.finalize([self.p2])
        self.assertEqual(JournalEntry.objects.filter(status=JournalEntry.POSTED, source_type='payroll').count(), 1)
        n = JournalEntry.objects.count()
        postings.post_payroll(self.per)
        engine.finalize([self.p1, self.p2])
        self.assertEqual(JournalEntry.objects.count(), n)
        self.assertEqual(self.bal('payroll.salary_expense'), D('70000.50'))

    def test_revision_reverses_and_reposts(self):
        engine.finalize([self.p1, self.p2])
        snap = EmployeePayroll.objects.filter(pk=self.p2.pk).values().get()
        rev = self._pr(self.e2, '22000', [('esi', 'employee', '165'), ('esi', 'employer', '715')],
                       revision=2, revises=self.p2, is_current=False)
        engine.finalize([rev])
        self.assertEqual(self.bal('payroll.salary_expense'), D('72000.00'))
        self.assertEqual(JournalEntry.objects.filter(source_type='payroll', status=JournalEntry.REVERSED).count(), 1)
        self.assertEqual(EmployeePayroll.objects.filter(pk=self.p2.pk).values().get() | {'is_current': True}, snap)

    def test_closed_period_blocks_finalize(self):
        acc.close_period(acc.period_for(self.per.end), 'closed')
        with self.assertRaises(ValidationError):
            engine.finalize([self.p1])
        self.assertEqual(EmployeePayroll.objects.get(pk=self.p1.pk).status, 'draft')

    def test_partial_payments_and_overpayment_block(self):
        engine.finalize([self.p1, self.p2])
        p1 = EmployeePayroll.objects.get(pk=self.p1.pk)
        with self.assertRaises(ValidationError):
            sal.record_payment(p1, amount=D('43500.01'), payment_date=date(2026, 11, 1))
        sal.record_payment(p1, amount=D('20000'), payment_date=date(2026, 11, 1), reference='U1')
        self.assertEqual(sal.status_for(p1), {'paid': D('20000.00'), 'balance': D('23500.00'), 'status': 'partial',
                                              'overpaid': False})
        sal.record_payment(p1, amount=D('23500'), payment_date=date(2026, 11, 2))
        self.assertEqual(sal.status_for(p1)['status'], 'paid')
        self.assertEqual(self.bal('payroll.salary_payable', ('employee', str(self.e1.pk))), D('0.00'))
        with self.assertRaises(ValidationError):
            sal.record_payment(p1, amount=D('1'), payment_date=date(2026, 11, 3))
        cf = RS.cash_flow(date(2026, 11, 1), date(2026, 11, 30))
        self.assertEqual(cf['salary_paid'], D('43500.00'))
        sp = SalaryPayment.objects.get(payroll=p1, amount=D('23500'))
        sal.cancel_payment(sp, 'bounced')
        self.assertEqual(sal.status_for(p1)['status'], 'partial')
        self.assertEqual(RS.cash_flow(date(2026, 11, 1), date(2026, 11, 30))['salary_paid'], D('20000.00'))

    def test_payment_requires_finalized(self):
        with self.assertRaises(ValidationError):
            sal.record_payment(self.p1, amount=D('10'), payment_date=date(2026, 11, 1))

    def test_bulk_pay_all_and_views(self):
        engine.finalize([self.p1, self.p2])
        p1 = EmployeePayroll.objects.get(pk=self.p1.pk)
        sal.record_payment(p1, amount=D('500'), payment_date=date(2026, 11, 1))
        r = self.client.post(reverse('em:salary_pay_all', args=[self.per.pk]),
                             {'payment_date': '2026-11-02', 'mode': 'bank', 'reference': 'B1'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.bal('payroll.salary_payable'), D('0.00'))
        self.assertEqual(SalaryPayment.objects.count(), 3)
        r = self.client.get(reverse('em:payroll_detail', args=[p1.pk]))
        self.assertContains(r, 'Salary payments')
        rows = RS.payroll_payable(date(2026, 11, 30))
        self.assertTrue(all(x['outstanding'] == 0 for x in rows))
