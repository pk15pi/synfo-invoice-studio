"""Stage 7: compliance rules, checks, GST input reclass, statutory payments, TDS register."""
from datetime import date
from decimal import Decimal as D
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from accounting import postings
from accounting import services as acc
from accounting.models import JournalEntry, JournalLine
from employee_management.models import (Department, Employee, EmployeePayroll, PayrollDeduction, PayrollEarning,
                                        PayrollPeriod)
from expense_management.models import CompanyExpense, CompanyExpensePayment, ExpenseCategory, Vendor

from . import checks, registers
from . import services as S
from .models import DueDateEntry, GstInputClaim, StatutoryPayment, StatutoryRule as R


def rule(kind, key='', frm=date(2026, 4, 1), to=None, **params):
    r = R(kind=kind, key=key, effective_from=frm, effective_to=to, params=params)
    r.save()
    return r


def tb_ok(t):
    s = JournalLine.objects.filter(entry__status__in=acc.BOOKED).aggregate(d=Sum('debit'), c=Sum('credit'))
    t.assertEqual(s['d'], s['c'])


class RuleTests(TestCase):
    def test_lookup_by_date_and_overlap_rejected(self):
        old = rule(R.GST, '998314', date(2025, 4, 1), date(2026, 3, 31), rate='12')
        new = rule(R.GST, ' 998314 ', date(2026, 4, 1), rate='18')
        self.assertEqual(S.get_rule(R.GST, '998314', date(2025, 12, 1)), old)
        self.assertEqual(S.get_rule(R.GST, '998314', date(2026, 4, 1)), new)
        self.assertEqual(S.get_rule(R.GST, '998314', date(2030, 1, 1)), new)
        with self.assertRaises(ValidationError):
            rule(R.GST, '998314', date(2026, 1, 1), date(2026, 6, 30), rate='5')
        with self.assertRaises(ValidationError):  # open-ended vs open-ended
            rule(R.GST, '998314', date(2027, 1, 1), rate='5')
        rule(R.GST, '998315', date(2026, 1, 1), rate='5')  # different key is fine
        R(kind=R.GST, key='998314', effective_from=date(2026, 5, 1), params={'rate': '5'}, active=False).save()

    def test_param_validation(self):
        for kind, key, params in [(R.GST, '1', {}), (R.GST, '1', {'rate': '120'}), (R.PF, '', {'employee_pct': 'x'}),
                                  (R.ESI, '', {'employee_pct': '1', 'employer_pct': '3'}),
                                  (R.PT, 'KA', {'slabs': [{'from': '0', 'to': None, 'amount': '1'},
                                                          {'from': '10', 'to': None, 'amount': '2'}]}),
                                  (R.TDS, '', {'rate': '10'})]:
            with self.assertRaises(ValidationError, msg=(kind, params)):
                rule(kind, key, **params)

    def test_not_configured_never_falls_back(self):
        with self.assertRaises(S.RuleNotConfigured) as cm:
            S.expected_pf(D('20000'), date(2026, 10, 31))
        self.assertIn('rule not configured', str(cm.exception))
        for fn in (lambda: S.expected_gst(D('100'), '998314', date(2026, 5, 1)),
                   lambda: S.expected_esi(D('100'), date(2026, 5, 1)),
                   lambda: S.expected_pt(D('100'), 'Maharashtra', date(2026, 5, 1)),
                   lambda: S.expected_tds(D('100'), '194J', date(2026, 5, 1))):
            self.assertRaises(S.RuleNotConfigured, fn)
        rule(R.PF, frm=date(2027, 1, 1), employee_pct='12', employer_pct='12')
        self.assertRaises(S.RuleNotConfigured, S.expected_pf, D('1'), date(2026, 10, 31))

    def test_calculators(self):
        rule(R.PF, employee_pct='12', employer_pct='12', wage_ceiling='15000')
        rule(R.ESI, employee_pct='0.75', employer_pct='3.25', wage_ceiling='21000')
        rule(R.PT, 'maharashtra', slabs=[{'from': '0', 'to': '10000', 'amount': '0'},
                                         {'from': '10000.01', 'to': None, 'amount': '200'}])
        d = date(2026, 10, 31)
        self.assertEqual(S.expected_pf(D('40000'), d)['employee'], D('1800.00'))
        self.assertEqual(S.expected_esi(D('20000'), d)['employee'], D('150.00'))
        self.assertEqual(S.expected_esi(D('25000'), d)['employee'], D('0'))
        self.assertEqual(S.expected_pt(D('50000'), ' Maharashtra', d)['amount'], D('200'))

    def test_example_loader_inactive_and_labelled(self):
        call_command('load_example_compliance_rules', stdout=StringIO())
        call_command('load_example_compliance_rules', stdout=StringIO())
        self.assertTrue(R.objects.exists())
        self.assertFalse(R.objects.filter(active=True).exists())
        self.assertFalse(R.objects.filter(is_example=False).exists())
        self.assertTrue(all('NOT VERIFIED' in r.source_note for r in R.objects.all()))
        self.assertRaises(S.RuleNotConfigured, S.expected_pf, D('1'), date(2026, 10, 31))


class PayslipBase(TestCase):
    def setUp(self):
        dept = Department.objects.create(name='Eng', code='ENG')
        self.emp = Employee.objects.create(first_name='Asha', last_name='R', department=dept, designation='Dev',
                                           date_of_joining=date(2024, 1, 1), status=Employee.CONFIRMED,
                                           state='Maharashtra')
        self.per = PayrollPeriod.objects.create(year=2026, month=10)

    def payslip(self, basic='20000', deds=(), status=EmployeePayroll.FINALIZED):
        p = EmployeePayroll.objects.create(period=self.per, employee=self.emp, gross_earnings=D(basic),
                                           net_salary=D(basic), monthly_salary=D(basic), status=status)
        PayrollEarning.objects.create(payroll=p, code='basic', label='Basic', amount=D(basic))
        for c, s, a in deds:
            PayrollDeduction.objects.create(payroll=p, code=c, side=s, label=c, amount=D(a))
        return p


class CheckTests(PayslipBase):
    def test_mismatch_ok_and_not_configured(self):
        p = self.payslip('20000', [('pf', 'employee', '1800'), ('pf', 'employer', '2400'),
                                   ('professional_tax', 'employee', '200')])
        before = list(p.deductions.values_list('code', 'side', 'amount'))
        rows, summ = checks.run(date(2026, 10, 1), date(2026, 10, 31), ['payslips'])
        self.assertEqual(summ['mismatch'], 0)
        self.assertEqual(summ['not_configured'], 2)  # PF and PT present, no rules
        self.assertTrue(all(r['expected'] is None for r in rows))
        rule(R.PF, employee_pct='12', employer_pct='12', wage_ceiling='15000')
        rule(R.PT, 'Maharashtra', slabs=[{'from': '0', 'to': None, 'amount': '200'}])
        rows, summ = checks.run(date(2026, 10, 1), date(2026, 10, 31), ['payslips'])
        by = {r['item']: r for r in rows}
        self.assertEqual(by['PF (employee)']['status'], checks.OK)  # 12% of 15000 ceiling = 1800
        self.assertEqual(by['PF (employer)']['status'], checks.MISMATCH)
        self.assertEqual(by['PF (employer)']['expected'], D('1800.00'))
        self.assertEqual(by['PT Maharashtra (employee)']['status'], checks.OK)
        self.assertEqual(summ['mismatch'], 1)
        p.refresh_from_db()  # read-only: payslip untouched
        self.assertEqual(list(p.deductions.values_list('code', 'side', 'amount')), before)

    def test_draft_payslips_ignored_and_page_renders(self):
        self.payslip('20000', [('pf', 'employee', '1')], status=EmployeePayroll.DRAFT)
        self.assertEqual(checks.run(date(2026, 10, 1), date(2026, 10, 31))[0], [])
        for name in ('home', 'rules', 'rule_new', 'check', 'gst_output', 'gst_input', 'tds', 'payroll', 'payments',
                     'due_dates'):
            r = self.client.get(reverse(f'compliance:{name}'))
            self.assertEqual(r.status_code, 200, name)
            self.assertContains(r, 'verified by you or your CA')
        self.assertEqual(self.client.get(reverse('compliance:check'), REMOTE_ADDR='10.0.0.5').status_code, 403)

    def test_invalid_start_keeps_valid_end_and_empty_selection(self):
        r = self.client.get(reverse('compliance:check'), {'start': 'nope', 'end': '2026-06-30', 'filters': '1'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['end'], date(2026, 6, 30))
        r = self.client.get(reverse('compliance:check'), {'filters': '1'})
        self.assertEqual((r.context['rows'], r.context['which']), ([], []))


class GstInputTests(TestCase):
    def setUp(self):
        self.cat = ExpenseCategory.objects.get(name='Technology')
        self.vendor = Vendor.objects.create(name='Host Co', gstin='29ABCDE1234F1Z5')

    def exp(self, **kw):
        data = dict(expense_date=date(2026, 5, 10), vendor=self.vendor, category=self.cat, description='VPS',
                    amount_before_tax=D('10000'), gst_amount=D('1800'), cgst_amount=D('900'), sgst_amount=D('900'))
        data.update(kw)
        e = CompanyExpense(**data)
        e.full_clean()
        e.save()
        return e

    def exp_bal(self):
        return acc.balance(postings.mapped_or(f'expense.category.{self.cat.pk}', 'expense.default'))

    def test_default_review_then_reclass_idempotent_and_reverse(self):
        e = self.exp()
        rows, _ = registers.input_gst(date(2026, 4, 1), date(2027, 3, 31))
        self.assertEqual(rows[0]['status'], GstInputClaim.REVIEW)
        self.assertEqual(rows[0]['status_label'], 'Not eligible / under review')
        base = self.exp_bal()
        self.assertEqual(base, D('11800.00'))
        with self.assertRaises(ValidationError):
            S.mark_eligible(e, '  ')
        S.mark_eligible(e, 'CA confirmed ITC')
        S.mark_eligible(e, 'again')  # idempotent
        self.assertEqual(JournalEntry.objects.filter(source_key__startswith=f'gst_input:{e.pk}').count(), 1)
        self.assertEqual(acc.balance(acc.mapped_account('gst.input.cgst')), D('900.00'))
        self.assertEqual(acc.balance(acc.mapped_account('gst.input.sgst')), D('900.00'))
        self.assertEqual(self.exp_bal(), D('10000.00'))
        c = GstInputClaim.objects.get(expense=e)
        self.assertEqual((c.status, c.events.count()), ('eligible', 1))
        tb_ok(self)
        S.unmark(e, 'Blocked credit')
        S.unmark(e, 'again')
        self.assertEqual(acc.balance(acc.mapped_account('gst.input.cgst')), D('0.00'))
        self.assertEqual(self.exp_bal(), base)
        self.assertEqual(GstInputClaim.objects.get(expense=e).status, 'review')
        self.assertIsNone(S.gst_input_posted(e))
        S.mark_eligible(e, 'Re-reviewed')
        self.assertEqual(acc.balance(acc.mapped_account('gst.input.cgst')), D('900.00'))
        tb_ok(self)

    def test_no_split_rejected_and_view(self):
        e = self.exp(cgst_amount=D('0'), sgst_amount=D('0'), igst_amount=D('0'))
        with self.assertRaises(ValidationError):
            S.mark_eligible(e, 'x')
        e2 = self.exp()
        r = self.client.post(reverse('compliance:gst_input_toggle', args=[e2.pk, 'mark']), {'reason': ''})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(GstInputClaim.objects.filter(expense=e2, status='eligible').exists())
        self.client.post(reverse('compliance:gst_input_toggle', args=[e2.pk, 'mark']), {'reason': 'ok'})
        self.assertTrue(GstInputClaim.objects.filter(expense=e2, status='eligible').exists())

    def test_toggle_rejects_unknown_action_and_offsite_next(self):
        e = self.exp()
        r = self.client.post(reverse('compliance:gst_input_toggle', args=[e.pk, 'delete']), {'reason': 'x'})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(GstInputClaim.objects.filter(expense=e, status='eligible').exists())
        r = self.client.post(reverse('compliance:gst_input_toggle', args=[e.pk, 'mark']),
                             {'reason': 'ok', 'next': 'https://evil.test/pwn'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r['Location'], reverse('compliance:gst_input'))
        e2 = self.exp()
        r = self.client.post(reverse('compliance:gst_input_toggle', args=[e2.pk, 'mark']),
                             {'reason': 'ok', 'next': '/compliance/gst/input/?status=eligible'})
        self.assertEqual(r['Location'], '/compliance/gst/input/?status=eligible')

    def test_tds_register_vendor_payment(self):
        e = self.exp()
        CompanyExpensePayment.objects.create(expense=e, payment_date=date(2026, 6, 1), amount=D('10800'),
                                             tds_amount=D('1000'))
        r = registers.tds_register(date(2026, 4, 1), date(2027, 3, 31))
        self.assertEqual(r['totals']['vendor'], D('1000.00'))
        self.assertEqual(r['vendor'][0]['pan_gstin'], '29ABCDE1234F1Z5')


class PaymentTests(PayslipBase):
    def test_statutory_payment_posts_and_cancel_reverses(self):
        self.payslip('20000', [('tds', 'employee', '3000')])
        p = StatutoryPayment(kind='tds_salary', period_year=2026, period_month=10, amount=D('3000'),
                             date=date(2026, 11, 7), challan_ref='CIN123')
        S.record_payment(p)
        S.record_payment(p)  # idempotent
        e = postings.current(S.statutory_payment_key(p))
        self.assertEqual(JournalEntry.objects.filter(source_key__startswith=f'statutory_payment:{p.pk}').count(), 1)
        dr = e.lines.get(debit__gt=0)
        self.assertEqual(dr.account, acc.mapped_account('payroll.tds_payable'))
        self.assertEqual(dr.debit, D('3000.00'))
        cr = e.lines.get(credit__gt=0)
        self.assertEqual(cr.account, postings.bank_ledger(None, 'payroll.payment_bank'))
        tb_ok(self)
        r = registers.tds_register(date(2026, 10, 1), date(2026, 11, 30))
        self.assertEqual((r['totals']['salary'], r['totals']['paid_salary'], r['totals']['due_salary']),
                         (D('3000.00'), D('3000.00'), D('0.00')))
        october = registers.tds_register(date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual((october['totals']['paid_salary'], october['totals']['due_salary']),
                         (D('3000.00'), D('0.00')))
        S.cancel_payment(p, 'Wrong challan')
        self.assertIsNone(postings.current(S.statutory_payment_key(p)))
        self.assertEqual(acc.balance(acc.mapped_account('payroll.tds_payable')), D('0.00'))
        self.assertEqual(registers.tds_register(date(2026, 10, 1), date(2026, 11, 30))['totals']['paid_salary'],
                         D('0.00'))
        self.assertRaises(ValidationError, S.record_payment,
                          StatutoryPayment(kind='pf', period_year=2026, period_month=10, amount=D('0'),
                                           date=date(2026, 11, 7), challan_ref='x'))

    def test_payroll_summary_and_due_calendar(self):
        self.payslip('20000', [('pf', 'employee', '1800'), ('pf', 'employer', '1800')])
        S.record_payment(StatutoryPayment(kind='pf', period_year=2026, period_month=10, amount=D('1000'),
                                          date=date(2026, 11, 10), challan_ref='TRRN1'))
        rows, heads, ledger = registers.payroll_statutory(date(2026, 10, 1), date(2026, 10, 31))
        g = {x['kind']: x for x in rows[0]['groups']}
        self.assertEqual((g['pf']['due'], g['pf']['paid'], g['pf']['balance']), (D('3600.00'), D('1000.00'), D('2600.00')))
        from .models import DueDateEntry
        DueDateEntry.objects.create(name='PF', kind='pf', day_of_month=15, month_offset=1)
        DueDateEntry.objects.create(name='Quarterly', kind='tds_return', day_of_month=31, month_offset=1, months='3,6,9,12')
        cal = S.due_calendar(date(2026, 11, 1), 1)
        self.assertEqual([(c['entry'].name, c['date'], c['paid']) for c in cal], [('PF', date(2026, 11, 15), True)])
        cal = S.due_calendar(date(2027, 1, 1), 1)
        self.assertIn(('Quarterly', date(2027, 1, 31)), [(c['entry'].name, c['date']) for c in cal])

    def test_payment_period_and_duplicate_validation(self):
        base = dict(kind='pf', period_year=2026, period_month=10, amount=D('100'),
                    date=date(2026, 11, 10), challan_ref='PF-1')
        for patch in ({'period_month': 0}, {'period_year': 0}, {'date': date(2020, 1, 1)}):
            with self.assertRaises(ValidationError, msg=patch):
                StatutoryPayment(**{**base, **patch}).full_clean()
        first = StatutoryPayment(**base)
        S.record_payment(first)
        with self.assertRaises(ValidationError):
            S.record_payment(StatutoryPayment(**base))
        S.cancel_payment(first, 'Duplicate entry')
        S.record_payment(StatutoryPayment(**base))

    def test_due_calendar_matches_return_deposits_and_far_offsets(self):
        DueDateEntry.objects.create(name='GST return', kind='gst_return', day_of_month=20, month_offset=1)
        S.record_payment(StatutoryPayment(kind='gst_cgst', period_year=2026, period_month=10, amount=D('500'),
                                          date=date(2026, 11, 20), challan_ref='GST-RET'))
        row = next(c for c in S.due_calendar(date(2026, 11, 1), 1) if c['entry'].name == 'GST return')
        self.assertTrue(row['paid'])
        self.assertEqual(row['paid_amount'], D('500.00'))
        DueDateEntry.objects.create(name='Far offset', kind='other', day_of_month=5, month_offset=18)
        cal = S.due_calendar(date(2028, 4, 1), 1)
        self.assertIn('Far offset', [c['entry'].name for c in cal])


class InvoiceCheckTests(TestCase):
    def test_invoice_gst_mismatch(self):
        from invoice_app.models import Client, Invoice, InvoiceLine
        c = Client.objects.create(name='Acme')
        d = date(2026, 6, 30)
        i = Invoice.objects.create(client=c, invoice_number='CHK001', invoice_date=d, due_date=d)
        InvoiceLine.objects.create(invoice=i, project_name='P', amount=D('100000'), sac_code='998314')
        Invoice.objects.filter(pk=i.pk).update(status=Invoice.FINALIZED, tax_type=Invoice.TAX_CGST_SGST,
                                               tax_rate=D('18'), taxable_value=D('100000'), cgst_amount=D('9000'),
                                               sgst_amount=D('8000'), total_tax=D('17000'))
        rows, s = checks.run(date(2026, 6, 1), date(2026, 6, 30), ['invoices'])
        self.assertEqual([r['status'] for r in rows], [checks.NOT_CONFIGURED])
        rule(R.GST, '998314', rate='18')
        rows, s = checks.run(date(2026, 6, 1), date(2026, 6, 30), ['invoices'])
        by = {r['item']: r for r in rows}
        self.assertEqual(by['CGST']['status'], checks.OK)
        self.assertEqual((by['SGST']['status'], by['SGST']['expected']), (checks.MISMATCH, D('9000.00')))

    def test_missing_rule_keeps_earlier_rows(self):
        from invoice_app.models import Client, Invoice, InvoiceLine
        c = Client.objects.create(name='Beta')
        d = date(2026, 7, 31)
        i = Invoice.objects.create(client=c, invoice_number='CHK002', invoice_date=d, due_date=d)
        InvoiceLine.objects.create(invoice=i, project_name='P', amount=D('100000'), sac_code='998314')
        InvoiceLine.objects.create(invoice=i, project_name='P', amount=D('50000'), sac_code='999999')
        Invoice.objects.filter(pk=i.pk).update(status=Invoice.FINALIZED, tax_type=Invoice.TAX_CGST_SGST,
                                               tax_rate=D('12'), taxable_value=D('150000'), cgst_amount=D('9000'),
                                               sgst_amount=D('9000'), total_tax=D('18000'))
        rule(R.GST, '998314', rate='18')
        rows, _ = checks.run(date(2026, 7, 1), date(2026, 7, 31), ['invoices'])
        items = {(r['item'], r['status']) for r in rows}
        self.assertIn(('GST rate SAC 998314', checks.MISMATCH), items)
        self.assertIn(('GST SAC 999999', checks.NOT_CONFIGURED), items)
