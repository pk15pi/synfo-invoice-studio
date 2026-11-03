"""Stage 9: one end-to-end scenario through every module, with exact Decimal checks against the ledger.

Running it writes nothing outside the test DB except a temp backup folder. Set QB_E2E_REPORT=<path> to also dump
the expected-vs-actual table (used to produce docs/E2E_RESULTS.md)."""
import os
import shutil
import tempfile
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Sum
from django.test import TestCase, override_settings
from django.urls import reverse

from accounting import postings
from accounting import services as acc
from accounting.models import JournalEntry
from audit.models import AuditEvent
from backup import services as BK
from bank_management import importer, services as BS
from bank_management.models import BankTransaction, ImportBatch
from bank_management.tests import make_bank
from compliance import services as CS
from compliance.models import StatutoryPayment
from employee_management import advances as adv, payroll as engine, salary_payments as sal
from employee_management.models import (Department, Employee, EmployeeAdvance, EmployeePayroll,
                                        EmployeeSalaryStructure, PayrollPeriod, SalaryComponent)
from expense_management import services as XS
from expense_management.models import CompanyExpense, EmployeeExpenseClaim, ExpenseCategory, Vendor
from invoice_app import ledger as IL
from invoice_app.models import BusinessProfile, Client, Invoice, InvoiceLine, InvoicePayment
from reporting import ledger as LG, reports as R

Z = D('0.00')
FY_START, OCT_END, NOV_END = date(2026, 4, 1), date(2026, 10, 31), date(2026, 11, 30)


def q(v):
    return D(v).quantize(D('0.01'))


class EndToEndTests(TestCase):
    results = []

    def check(self, label, expected, actual):
        self.results.append((label, q(expected), q(actual)))
        self.assertEqual(q(actual), q(expected), label)

    def bal(self, key, party=None, end=None):
        a = acc.mapped_account(key)
        if party:
            return postings.party_balance(a, *party)
        return acc.balance(a, end=end)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        out = os.environ.get('QB_E2E_REPORT')
        if out and cls.results:
            with open(out, 'w', encoding='utf-8') as f:
                f.write('| # | Check | Expected | Actual | Result |\n|---|---|---:|---:|---|\n')
                for i, (l, e, a) in enumerate(cls.results, 1):
                    f.write(f"| {i} | {l} | {e:,} | {a:,} | {'OK' if e == a else 'MISMATCH'} |\n")

    def test_full_scenario(self):
        tmp = Path(tempfile.mkdtemp(prefix='qbe2e-'))
        self.addCleanup(shutil.rmtree, tmp, True)
        ov = override_settings(BACKUP_DIR=tmp / 'bk', EMPLOYEE_PRIVATE_ROOT=tmp / 'priv', MEDIA_ROOT=tmp / 'media')
        ov.enable()
        self.addCleanup(ov.disable)
        n_audit0 = AuditEvent.objects.count()

        # 1. company, bank (opening 2,00,000), customer, project
        BusinessProfile.objects.update_or_create(pk=1, defaults={'name': 'QuantBud E2E LLP'})
        bank = make_bank(opening=D('200000'), od=FY_START)
        bank.is_default = True
        bank.save()
        cust = Client.objects.create(name='E2E Customer Ltd')
        project = 'E2E Project'  # projects are carried on invoice lines (project_name)
        self.check('1. Bank opening balance (ledger)', '200000', acc.balance(bank.ledger_account))

        # 2. invoice 1,00,000 + 18% GST, finalized
        inv = Invoice.objects.create(client=cust, invoice_number='E2E-001', invoice_date=date(2026, 10, 5),
                                     due_date=date(2026, 10, 20), total=D('100000.00'), taxable_value=D('100000.00'),
                                     tax_type='cgst_sgst', tax_rate=D('18'), cgst_amount=D('9000.00'),
                                     sgst_amount=D('9000.00'))
        InvoiceLine.objects.create(invoice=inv, project_name=project, description='Consulting',
                                   quantity_days=D('1'), amount=D('100000.00'))
        IL.finalize_invoice(inv)
        cparty = ('customer', str(cust.pk))
        self.check('2. AR after invoice', '118000', self.bal('invoice.ar', cparty))

        # 3. receipt 60,000 with TDS 2,000 and bank charges 500
        InvoicePayment.objects.create(invoice=inv, receipt_date=date(2026, 10, 20), amount=D('60000'),
                                      deductions=D('2500'), bank_charges=D('500'), bank_account=bank, reference='R1')
        IL.post_to_ledger(Invoice.objects.get(pk=inv.pk))
        self.check('3. AR after receipt (118000 - 62500)', '55500', self.bal('invoice.ar', cparty))
        self.check('3. TDS receivable', '2000', self.bal('invoice.tds_receivable'))

        # 4. small credit note 1,000 + GST
        IL.create_credit_note(Invoice.objects.get(pk=inv.pk), date=date(2026, 10, 25), reason='Rate correction',
                              taxable_amount=D('1000'))
        self.check('4. AR after credit note (55500 - 1180)', '54320', self.bal('invoice.ar', cparty))

        # 5. vendor expense 10,000 (no GST) and 6. partial payment 5,000
        vendor = Vendor.objects.create(name='E2E Vendor')
        tech = ExpenseCategory.objects.get(name='Technology')
        exp = CompanyExpense(expense_date=date(2026, 10, 10), vendor=vendor, category=tech, description='Hosting',
                             amount_before_tax=D('10000'), gst_amount=D('0'))
        exp.full_clean()
        exp.save()
        vparty = ('vendor', vendor.pk)
        self.check('5. AP after expense', '-10000', self.bal('expense.ap', vparty))
        XS.record_payment(exp, payment_date=date(2026, 10, 22), method='bank_transfer', amount=D('5000'), bank=bank)
        self.check('6. AP after partial payment', '-5000', self.bal('expense.ap', vparty))
        self.assertEqual(CompanyExpense.objects.get(pk=exp.pk).payment_status, CompanyExpense.PARTIAL)

        # 7. payroll 50,000 gross (October), finalized; advance 6,000 disbursed in October
        dept = Department.objects.create(name='Ops', code='OPS')
        emp = Employee.objects.create(first_name='Meera', last_name='E2E', department=dept, designation='Analyst',
                                      date_of_joining=date(2025, 1, 1), status=Employee.CONFIRMED)
        st = EmployeeSalaryStructure.objects.create(employee=emp, effective_from=date(2025, 1, 1))
        SalaryComponent.objects.create(structure=st, kind='earning', code='basic', amount=D('50000'))
        a = adv.disburse(EmployeeAdvance(employee=emp, date=date(2026, 10, 15), principal=D('6000'),
                                         interest_amount=D('0'), instalment_amount=D('3000'), start_year=2026,
                                         start_month=11, bank_account=bank))
        oct_ = PayrollPeriod.objects.create(year=2026, month=10)
        engine.generate(oct_)
        p_oct = EmployeePayroll.objects.get(period=oct_, employee=emp, is_current=True)
        engine.finalize([p_oct])
        p_oct.refresh_from_db()
        eparty = ('employee', str(emp.pk))
        self.check('7. Oct gross', '50000', p_oct.gross_earnings)
        self.check('7. Oct salary payable = net', p_oct.net_salary, -self.bal('payroll.salary_payable', eparty))
        self.EXP_OCT_NET, self.EXP_OCT_ER = p_oct.net_salary, p_oct.employer_contributions

        # 8. salary paid partly then fully
        sal.record_payment(p_oct, amount=D('20000'), payment_date=date(2026, 11, 2), bank_account=bank, reference='S1')
        self.assertEqual(sal.status_for(p_oct)['status'], 'partial')
        sal.record_payment(p_oct, amount=p_oct.net_salary - D('20000'), payment_date=date(2026, 11, 4),
                           bank_account=bank, reference='S2')
        self.assertEqual(sal.status_for(p_oct)['status'], 'paid')
        self.check('8. Salary payable after full payment', '0', self.bal('payroll.salary_payable', eparty))

        # 9. advance recovered in November payroll
        nov = PayrollPeriod.objects.create(year=2026, month=11)
        engine.generate(nov)
        p_nov = EmployeePayroll.objects.get(period=nov, employee=emp, is_current=True)
        engine.finalize([p_nov])
        p_nov.refresh_from_db()
        rec = p_nov.deductions.filter(code='advance_recovery').aggregate(s=Sum('amount'))['s'] or Z
        self.check('9. Nov advance recovery', '3000', rec)
        self.check('9. Advance balance (6000 - 3000)', '3000', self.bal('payroll.advance', eparty))
        self.check('9. Nov salary payable = Nov net', p_nov.net_salary, -self.bal('payroll.salary_payable', eparty))

        # 10. claim 5,000 approved, 3,000 reimbursed
        travel = ExpenseCategory.objects.get(name='Travel')
        c = EmployeeExpenseClaim(employee=emp, claim_date=date(2026, 10, 12), expense_date=date(2026, 10, 11),
                                 category=travel, description='Client visit', amount_claimed=D('5000'))
        c.full_clean()
        c.save()
        XS.submit_claim(c)
        XS.approve_claim(c)
        XS.record_reimbursement(c, reimbursement_date=date(2026, 10, 28), method='bank_transfer', amount=D('3000'),
                                bank=bank)
        self.check('10. Reimbursement payable', '-2000', self.bal('claim.reimbursement_payable', eparty))

        # 12. statutory payment: October CGST net of the credit note (9000 - 90)
        sp = StatutoryPayment(kind='gst_cgst', period_year=2026, period_month=10, amount=D('8910'),
                              date=date(2026, 11, 7), bank_account=bank, challan_ref='CIN-E2E-1')
        CS.record_payment(sp)
        self.check('12. CGST payable after payment', '0', self.bal('gst.output.cgst'))

        # expected bank = opening + receipt - vendor - advance - claim - Oct salary - CGST
        exp_bank = D('200000') + D('60000') - D('5000') - D('6000') - D('3000') - self.EXP_OCT_NET - D('8910')
        book = acc.balance(bank.ledger_account)
        self.check('Bank ledger balance', exp_bank, book)

        # 11. statement import + reconcile every row
        rows = [('2026-10-20', 'NEFT E2E Customer', 'R1', '', '60000.00'),
                ('2026-10-15', 'Advance Meera', 'A1', '6000.00', ''),
                ('2026-10-22', 'Vendor payment', 'V1', '5000.00', ''),
                ('2026-10-28', 'Claim reimbursement', 'C1', '3000.00', ''),
                ('2026-11-02', 'Salary part 1', 'S1', '20000.00', ''),
                ('2026-11-04', 'Salary part 2', 'S2', f'{self.EXP_OCT_NET - D("20000"):.2f}', ''),
                ('2026-11-07', 'GST CGST', 'CIN', '8910.00', '')]
        rows.sort()
        run, out = D('200000'), ['Date,Narration,Chq/Ref,Withdrawal,Deposit,Balance']
        for d, n, r, w, dp in rows:
            run += D(dp or '0') - D(w or '0')
            out.append(f'{d},{n},{r},{w},{dp},{run:.2f}')
        up = SimpleUploadedFile('e2e.csv', '\n'.join(out).encode('utf-8-sig'), content_type='text/csv')
        raw = importer.read_rows(up)
        mapping = {'header_row': '1', 'date': '0', 'description': '1', 'reference': '2', 'debit': '3',
                   'credit': '4', 'balance': '5', 'value_date': '', 'amount': ''}
        batch = ImportBatch.objects.create(bank_account=bank, file_name=up.name, raw_rows=raw, mapping=mapping)
        importer.confirm(batch, mapping)
        self.check('11. Statement rows imported', len(rows), batch.imported)
        lines = list(BS._booked_lines(bank).exclude(entry_id=bank.opening_entry_id))
        for t in BankTransaction.objects.filter(bank_account=bank).order_by('date', 'id'):
            ln = next(l for l in lines if BS.line_remaining(l) > 0 and BS.line_signed(l) == t.amount)
            BS.match(t, ln)
        s = BS.summary(bank, NOV_END)
        self.check('11. Reconciliation book balance', exp_bank, s['book_balance'])
        self.check('11. Statement balance', exp_bank, s['statement_balance'])
        self.check('11. Reconciliation difference', '0', s['difference'])
        self.check('11. Unexplained difference', '0', s['unexplained'])
        self.check('11. Unreconciled bank items', '0', s['unreconciled_bank_total'])
        self.check('11. Unreconciled book items', '0', s['unreconciled_book_total'])
        r = self.client.get(reverse('bank:reconciliation', args=[bank.pk]), {'end': '2026-11-30'})
        self.assertEqual(r.status_code, 200)

        # re-posting every source changes nothing
        n_entries = JournalEntry.objects.count()
        tb_before = LG.trial_balance(FY_START, NOV_END)[1]
        IL.post_to_ledger(Invoice.objects.get(pk=inv.pk))
        postings.post_payroll(oct_)
        postings.post_payroll(nov)
        postings.post_advance(EmployeeAdvance.objects.get(pk=a.pk))
        CompanyExpense.objects.get(pk=exp.pk).save()
        CS.record_payment(StatutoryPayment.objects.get(pk=sp.pk))
        BS.sync_opening(bank)
        self.check('Re-post: journal entry count unchanged', n_entries, JournalEntry.objects.count())
        self.assertEqual(LG.trial_balance(FY_START, NOV_END)[1], tb_before)

        # trial balance / balance sheet / P&L
        rows_tb, tot = LG.trial_balance(FY_START, NOV_END)
        self.assertTrue(tot['balanced'])
        self.check('TB difference', '0', tot['difference'])
        bs = LG.balance_sheet(NOV_END)
        self.assertTrue(bs['balanced'])
        self.check('BS difference', '0', bs['difference'])
        pl = LG.profit_and_loss(FY_START, NOV_END)
        exp_income = D('100000') - D('1000')
        exp_expense = (D('10000') + D('5000') + D('500') + p_oct.gross_earnings + p_oct.rounding_adjustment +
                       p_oct.employer_contributions + p_nov.gross_earnings + p_nov.rounding_adjustment +
                       p_nov.employer_contributions)
        self.check('P&L income (invoice 100000 - credit note 1000)', exp_income, pl['total_income'])
        self.check('P&L expenses (vendor + claim + bank charges + 2x payroll cost)', exp_expense, pl['total_expenses'])
        self.check('P&L profit', exp_income - exp_expense, pl['profit'])
        self.check('BS current-year earnings = P&L profit', pl['profit'], bs['current_year_earnings'])
        self.check('AR (ledger)', '54320', self.bal('invoice.ar'))
        self.check('AP (ledger, natural credit balance)', '5000', self.bal('expense.ap'))
        self.check('Salary payable (ledger) = Nov net unpaid', p_nov.net_salary, self.bal('payroll.salary_payable'))
        self.check('Reimbursement payable (ledger)', '2000', self.bal('claim.reimbursement_payable'))
        cf = LG.cash_flow(FY_START, NOV_END)
        self.check('Cash-flow opening (before 1 Apr)', '0', cf['opening'])
        self.check('Cash-flow closing = bank ledger', book, cf['closing'])
        self.check('Cash-flow closing = reconciliation book balance', s['book_balance'], cf['closing'])
        self.check('Cash-flow difference', '0', cf['difference'])
        rep = R.build('ledger_recon', {'period': 'custom', 'date_from': '2026-04-01', 'date_to': '2026-11-30'})
        diffs = {row[0]: row[3] for row in rep.tables[0].rows}
        for k in ('Revenue (invoice taxable value)', 'Company expenses', 'Employee claims expense',
                  'Accounts receivable (as of end)', 'Accounts payable (as of end)',
                  'Reimbursement payable (as of end)', 'Salary payable (as of end)', 'Salary + employer cost'):
            # the only expected gap: management revenue is gross of credit notes, the ledger nets them (1,000)
            self.check(f'Mgmt vs ledger difference: {k}', '1000' if k.startswith('Revenue') else '0', diffs[k])

        # 13. close October: posting into it is blocked
        acc.close_period(acc.period_for(OCT_END), 'October closed (E2E)')
        with self.assertRaises(ValidationError):
            acc.create_and_post(date(2026, 10, 30), 'Late entry', [
                {'account': acc.mapped_account('expense.default'), 'debit': D('100')},
                {'account': bank.ledger_account, 'credit': D('100')}])
        with self.assertRaises(ValidationError):
            XS.record_payment(CompanyExpense.objects.get(pk=exp.pk), payment_date=date(2026, 10, 30),
                              method='bank_transfer', amount=D('100'), bank=bank)
        self.check('13. Entries after blocked postings', n_entries, JournalEntry.objects.count())

        # 14. reversal in an open period (November)
        e = acc.create_and_post(date(2026, 11, 10), 'E2E accrual', [
            {'account': acc.mapped_account('expense.default'), 'debit': D('700')},
            {'account': acc.mapped_account('expense.ap'), 'credit': D('700')}])
        rv = acc.reverse(e, 'Entered in error', date(2026, 11, 11))
        self.assertEqual(JournalEntry.objects.get(pk=e.pk).status, JournalEntry.REVERSED)
        self.check('14. AP after entry + reversal (unchanged)', '5000', self.bal('expense.ap'))
        self.assertTrue(LG.trial_balance(FY_START, NOV_END)[1]['balanced'])

        # audit trail
        self.assertTrue(AuditEvent.objects.count() > n_audit0)
        for app, model in (('invoice_app', 'invoice'), ('invoice_app', 'invoicepayment'),
                           ('invoice_app', 'creditnote'), ('expense_management', 'companyexpense'),
                           ('expense_management', 'employeeexpenseclaim'), ('employee_management', 'employeepayroll'),
                           ('employee_management', 'salarypayment'), ('employee_management', 'employeeadvance'),
                           ('accounting', 'journalentry'), ('accounting', 'financialperiod'),
                           ('bank_management', 'banktransaction'), ('compliance', 'statutorypayment')):
            self.assertTrue(AuditEvent.objects.filter(app_label=app, model__iexact=model).exists(), model)
        self.check('Audit events recorded (> 0)', 1, min(1, AuditEvent.objects.count() - n_audit0))

        # 15. backup and verify
        bk = BK.create_backup(note='e2e')
        ok, problems = BK.verify(bk)
        self.assertTrue(ok, problems)
        self.check('15. Backup verify problems', 0, len(problems))
        self.assertTrue(Path(bk.path).exists())
