"""Stage 6: employee advances / loans, payroll recovery, attendance corrections, department leave report."""
from datetime import date
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from accounting import postings
from accounting import services as acc
from accounting.models import JournalEntry, JournalLine
from attendance import services as asvc
from attendance.models import AttendanceCorrection, AttendanceRecord as AR, LeaveBalance, LeaveType
from reporting import reports as R

from . import advances as adv
from . import payroll as engine
from .models import (Department, Employee, EmployeeAdvance, EmployeePayroll, EmployeeSalaryStructure, PayrollPeriod,
                     SalaryComponent)
from .pdf.payslip import payslip_for


class Base(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(name='Engineering', code='ENG')
        self.e1 = self.emp('Asha')
        self.e2 = self.emp('Ravi')
        self.per = PayrollPeriod.objects.create(year=2026, month=10)

    def emp(self, name, gross='42000'):
        e = Employee.objects.create(first_name=name, last_name='K', department=self.dept, designation='Dev',
                                    date_of_joining=date(2025, 1, 1), status=Employee.CONFIRMED)
        st = EmployeeSalaryStructure.objects.create(employee=e, effective_from=date(2025, 1, 1))
        SalaryComponent.objects.create(structure=st, kind='earning', code='basic', amount=D(gross))
        return e

    def advance(self, e=None, principal='10000', inst='3000', interest='0', y=2026, m=10, **kw):
        return adv.disburse(EmployeeAdvance(employee=e or self.e1, date=date(2026, 9, 15), principal=D(principal),
                                            interest_amount=D(interest), instalment_amount=D(inst) if inst else None,
                                            start_year=y, start_month=m, **kw))

    def gen(self, e=None, per=None):
        engine.generate(per or self.per)
        return EmployeePayroll.objects.get(period=per or self.per, employee=e or self.e1, is_current=True)

    def rec(self, p):
        return p.deductions.filter(code='advance_recovery').aggregate(s=Sum('amount'))['s'] or D('0')

    def adv_bal(self, e=None):
        return postings.party_balance(acc.mapped_account('payroll.advance'), 'employee', (e or self.e1).pk)

    def assert_tb(self):
        t = JournalLine.objects.filter(entry__status__in=acc.BOOKED).aggregate(d=Sum('debit'), c=Sum('credit'))
        self.assertEqual(t['d'], t['c'])


class AdvanceTests(Base):
    def test_disbursement_posting_and_cancel_reverses(self):
        a = self.advance(principal='10000', interest='500')
        self.assertEqual(self.adv_bal(), D('10500.00'))
        self.assertEqual(acc.balance(acc.mapped_account('payroll.advance_interest')), D('500.00'))
        self.assert_tb()
        self.assertEqual(a.outstanding(), D('10500.00'))
        with self.assertRaises(ValidationError):
            adv.cancel(a, '')
        adv.cancel(a, 'entered twice')
        self.assertEqual(self.adv_bal(), D('0'))
        self.assertEqual(a.status, a.CANCELLED)
        self.assert_tb()

    def test_cancel_blocked_after_recovery(self):
        a = self.advance()
        self.gen()
        with self.assertRaises(ValidationError):
            adv.cancel(a, 'x')

    def test_auto_recovery_instalment_and_caps(self):
        self.advance(principal='10000', inst='3000')
        p = self.gen()
        self.assertEqual(self.rec(p), D('3000.00'))
        self.assertEqual(p.net_salary, D('39000.00'))
        self.assertEqual(p.advance_recoveries.get().amount, D('3000.00'))
        # outstanding cap
        self.advance(e=self.e2, principal='2000', inst='3000')
        p2 = EmployeePayroll.objects.get(period=self.per, employee=self.e2)
        p2, _ = engine.recalculate(p2, {})
        self.assertEqual(self.rec(p2), D('2000.00'))

    def test_net_pay_cap(self):
        self.advance(principal='100000', inst='60000')
        p = self.gen()
        self.assertEqual(self.rec(p), D('42000.00'))
        self.assertEqual(p.net_salary, D('0.00'))

    def test_recovery_not_before_start_month(self):
        self.advance(y=2026, m=11)
        self.assertEqual(self.rec(self.gen()), D('0'))

    def test_manual_override(self):
        a = self.advance(principal='10000', inst='3000')
        p = self.gen()
        p, c = engine.recalculate(p, {'advance_recovery_override': D('1500')})
        self.assertEqual(self.rec(p), D('1500.00'))
        self.assertEqual(p.advance_recovery_override, D('1500.00'))
        p0, c = engine.recalculate(p, {'advance_recovery_override': D('20000')})
        self.assertIsNone(p0)
        self.assertTrue(any('outstanding' in x for x in c.errors))
        p, c = engine.recalculate(p, {'advance_recovery_override': D('0')})
        self.assertEqual(self.rec(p), D('0'))
        self.assertFalse(p.advance_recoveries.exists())
        self.assertEqual(a.outstanding(), D('10000.00'))

    def test_override_via_edit_screen(self):
        self.advance()
        p = self.gen()
        r = self.client.post(reverse('em:payroll_edit', args=[p.pk]), {'advance_recovery_override': '1234'})
        self.assertIn(r.status_code, (200, 302))
        self.assertEqual(self.rec(EmployeePayroll.objects.get(pk=p.pk)), D('1234.00'))

    def test_finalize_posts_recovery_tb_balanced(self):
        a = self.advance(principal='10000', inst='3000')
        p = self.gen()
        engine.finalize([p])
        self.assertEqual(self.adv_bal(), D('7000.00'))
        self.assertEqual(a.outstanding(), D('7000.00'))
        payable = postings.party_balance(acc.mapped_account('payroll.salary_payable'), 'employee', self.e1.pk)
        self.assertEqual(payable, D('-39000.00'))
        self.assert_tb()
        e = postings.current(postings.payroll_key(self.per))
        self.assertTrue(e.lines.filter(account=acc.mapped_account('payroll.advance'), party_id=str(self.e1.pk),
                                       credit=D('3000')).exists())
        # re-posting is a no-op
        n = JournalEntry.objects.count()
        postings.post_payroll(self.per)
        self.assertEqual(JournalEntry.objects.count(), n)

    def test_revision_reverses_and_reposts(self):
        a = self.advance(principal='10000', inst='3000')
        p = self.gen()
        engine.finalize([p])
        p.refresh_from_db()
        snap = EmployeePayroll.objects.filter(pk=p.pk).values().get()
        keep = lambda d: {k: v for k, v in d.items() if k not in ('is_current', 'updated_at')}
        r = engine.start_revision(p, 'lower recovery')
        self.assertEqual(self.rec(r), D('3000.00'))  # same month is excluded from outstanding when re-planning
        r, _ = engine.recalculate(r, {'advance_recovery_override': D('1000')})
        engine.finalize([r])
        self.assertEqual(self.adv_bal(), D('9000.00'))
        self.assertEqual(a.outstanding(), D('9000.00'))
        self.assertEqual(keep(EmployeePayroll.objects.filter(pk=p.pk).values().get()), keep(snap))  # original never rewritten
        self.assertEqual(p.advance_recoveries.get().amount, D('3000.00'))
        self.assertTrue(JournalEntry.objects.filter(source_type='payroll', status=JournalEntry.REVERSED).exists())
        self.assert_tb()

    def test_auto_close_and_reopen_on_revision(self):
        a = self.advance(principal='3000', inst='3000')
        p = self.gen()
        engine.finalize([p])
        p.refresh_from_db()
        a.refresh_from_db()
        self.assertEqual(a.status, a.CLOSED)
        self.assertEqual(self.adv_bal(), D('0'))
        r = engine.start_revision(p, 'skip')
        self.assertEqual(self.rec(r), D('3000.00'))  # closed by this month: still recovered on revision
        r, _ = engine.recalculate(r, {'advance_recovery_override': D('1000')})
        engine.finalize([r])
        a.refresh_from_db()
        self.assertEqual(a.status, a.ACTIVE)
        self.assertEqual(a.outstanding(), D('2000.00'))

    def test_repayment_and_auto_close(self):
        a = self.advance(principal='5000')
        with self.assertRaises(ValidationError):
            adv.repay(a, D('6000'))
        adv.repay(a, D('2000'), date(2026, 9, 20), reference='UTR1')
        self.assertEqual(a.outstanding(), D('3000.00'))
        adv.repay(a, D('3000'), date(2026, 9, 25))
        a.refresh_from_db()
        self.assertEqual(a.status, a.CLOSED)
        self.assertEqual(self.adv_bal(), D('0'))
        self.assert_tb()
        self.assertEqual(self.rec(self.gen()), D('0'))

    def test_write_off(self):
        a = self.advance(principal='10000', inst='3000')
        engine.finalize([self.gen()])
        with self.assertRaises(ValidationError):
            adv.write_off(a, ' ')
        adv.write_off(a, 'employee left', date(2026, 11, 5))
        a.refresh_from_db()
        self.assertEqual((a.status, a.written_off_amount), (a.WRITTEN_OFF, D('7000.00')))
        self.assertEqual(a.outstanding(), D('0'))
        self.assertEqual(self.adv_bal(), D('0'))
        self.assertEqual(acc.balance(acc.mapped_account('payroll.advance_writeoff')), D('7000.00'))
        self.assertEqual(acc.mapped_account('payroll.advance_writeoff').code, '5910')
        self.assert_tb()
        self.assertEqual([x['balance'] for x in adv.statement(a)], [D('10000.00'), D('7000.00'), D('0.00')])

    def test_screens_and_reports(self):
        a = self.advance()
        engine.finalize([self.gen()])
        for u in [reverse('em:advances'), reverse('em:advance_new'), reverse('em:advance_detail', args=[a.pk]),
                  reverse('em:advance_ledger', args=[self.e1.pk])]:
            self.assertEqual(self.client.get(u).status_code, 200, u)
        q = {'period': 'custom', 'date_from': '2026-04-01', 'date_to': '2026-10-31'}
        rep = R.build('advances_outstanding', q)
        self.assertEqual(rep.tables[0].total[4], D('7000.00'))
        sch = R.build('advance_schedule', q)
        self.assertEqual(sch.tables[0].total[5], D('3000.00'))
        self.assertEqual([r[4] for r in sch.tables[1].rows], [D('3000.00'), D('3000.00'), D('1000.00')])
        for k in ('advances_outstanding', 'advance_schedule'):
            for f in ('', 'xlsx', 'csv'):
                r = self.client.get(reverse('reporting:report', args=[k]), dict(q, format=f) if f else q)
                self.assertEqual(r.status_code, 200, (k, f))

    def test_new_advance_screen_posts(self):
        r = self.client.post(reverse('em:advance_new'), {
            'employee': self.e2.pk, 'kind': 'loan', 'date': '2026-09-01', 'principal': '12000', 'interest_amount': '0',
            'instalment_count': '4', 'start_year': '2026', 'start_month': '10'})
        self.assertEqual(r.status_code, 302)
        a = EmployeeAdvance.objects.get(employee=self.e2)
        self.assertEqual(a.instalment, D('3000.00'))
        self.assertEqual(self.adv_bal(self.e2), D('12000.00'))


class PayslipUnchangedTests(Base):
    def test_no_advance_payslip_identical(self):
        p = self.gen(self.e2)
        lines = list(p.deductions.values_list('code', 'label', 'amount'))
        net = p.net_salary
        self.advance(e=self.e1)  # another employee's advance must not affect this payslip
        p2, _ = engine.recalculate(EmployeePayroll.objects.get(pk=p.pk), {})
        self.assertEqual(list(p2.deductions.values_list('code', 'label', 'amount')), lines)
        self.assertEqual(p2.net_salary, net)
        self.assertFalse(p2.deductions.filter(code='advance_recovery').exists())
        pdf, _ = payslip_for(p2)
        self.assertTrue(pdf.startswith(b'%PDF'))


class AttendanceCorrectionTests(Base):
    def setUp(self):
        super().setUp()
        self.nov = PayrollPeriod.objects.create(year=2026, month=11)

    def test_correction_requires_reason_and_is_logged(self):
        AR.objects.create(employee=self.e1, date=date(2026, 11, 3), status=AR.PRESENT)
        self.gen(per=self.nov)
        r = AR.objects.get(employee=self.e1, date=date(2026, 11, 3))
        r.status = AR.ABSENT
        with self.assertRaises(ValidationError):
            r.save()
        r.correction_reason = 'was on site visit? no - absent'
        r.save()
        c = AttendanceCorrection.objects.get()
        self.assertEqual((c.old_value, c.new_value), ('Present', 'Absent'))
        self.assertTrue(c.reason and c.changed_at)
        # screen: missing reason -> error message, nothing saved
        self.client.post(reverse('att:daily'), {'date': '2026-11-04', 'status_%d' % self.e1.pk: 'A'})
        self.assertFalse(AR.objects.filter(date=date(2026, 11, 4)).exists())
        self.client.post(reverse('att:daily'), {'date': '2026-11-04', 'status_%d' % self.e1.pk: 'A',
                                                'reason': 'late entry'})
        self.assertTrue(AR.objects.filter(date=date(2026, 11, 4), status='A').exists())
        self.assertEqual(AttendanceCorrection.objects.count(), 2)
        self.assertEqual(self.client.get(reverse('att:corrections')).status_code, 200)

    def test_no_reason_needed_before_payroll(self):
        AR.objects.create(employee=self.e1, date=date(2026, 11, 3), status=AR.PRESENT)
        self.assertFalse(AttendanceCorrection.objects.exists())

    def test_blocked_when_payroll_finalized(self):
        p = self.gen(per=self.nov)
        engine.finalize([p])
        r = self.client.post(reverse('att:daily'), {'date': '2026-11-04', 'status_%d' % self.e1.pk: 'A',
                                                    'reason': 'fix'}, follow=True)
        self.assertFalse(AR.objects.filter(date=date(2026, 11, 4)).exists())
        self.assertContains(r, 'locked because payroll is finalized')
        self.assertFalse(AttendanceCorrection.objects.exists())


class DepartmentLeaveReportTests(Base):
    def test_leave_by_department_and_type(self):
        ops = Department.objects.create(name='Ops', code='OPS')
        e3 = self.emp('Meena')
        e3.department = ops
        e3.save()
        cl, sl = LeaveType.objects.get(code='CL'), LeaveType.objects.get(code='SL')
        AR.objects.create(employee=self.e1, date=date(2026, 9, 2), status=AR.LEAVE, leave_type=cl)
        AR.objects.create(employee=self.e2, date=date(2026, 9, 3), status=AR.HALF_DAY, leave_type=cl)
        AR.objects.create(employee=e3, date=date(2026, 9, 4), status=AR.LEAVE, leave_type=sl)
        AR.objects.create(employee=e3, date=date(2026, 10, 4), status=AR.LEAVE, leave_type=sl)  # outside period
        asvc.ensure_balances(2026)
        q = {'period': 'custom', 'date_from': '2026-09-01', 'date_to': '2026-09-30'}
        rep = R.build('leave_dept', q)
        t = rep.tables[0]
        codes = [c.label.split(' ')[0] for c in t.cols]
        rows = {r[0]: dict(zip(codes, r)) for r in t.rows}
        self.assertEqual(rows['Engineering']['CL'], D('1.5'))
        self.assertEqual(rows['Engineering']['Employees'], 2)
        self.assertEqual(rows['Ops']['SL'], D('1'))
        self.assertEqual(t.total[-1], D('2.5'))
        self.assertTrue(rep.tables[1].rows)
        self.assertEqual(LeaveBalance.objects.filter(year=2026).exists(), True)
        for f in ('', 'xlsx', 'csv'):
            r = self.client.get(reverse('reporting:report', args=['leave_dept']), dict(q, format=f) if f else q)
            self.assertEqual(r.status_code, 200, f)
