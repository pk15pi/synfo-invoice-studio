"""Stage 4 Part B: expense / claim ledger postings."""
from datetime import date
from decimal import Decimal as D
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command

from accounting import postings
from accounting import services as acc
from accounting.models import Account, AccountMapping, JournalEntry
from bank_management.tests import make_bank

from . import ledger, services
from .models import CompanyExpense
from .tests import Base


class ExpenseLedgerTests(Base):
    def bal(self, key, party=None):
        a = acc.mapped_account(key)
        return postings.party_balance(a, *party) if party else acc.balance(a)

    def vend(self):
        return ('vendor', self.vendor.pk)

    def test_expense_posts_bill_and_edit_reposts(self):
        e = self.expense()
        cur = postings.current(ledger.expense_key(e))
        self.assertEqual(cur.date, e.expense_date)
        self.assertEqual(self.bal('expense.ap', self.vend()), D('-11800.00'))
        self.assertEqual(self.bal('expense.default'), D('11800.00'))
        e.save()  # idempotent
        self.assertEqual(postings.history(ledger.expense_key(e)).count(), 1)
        e.amount_before_tax = D('20000')
        e.full_clean()
        e.save()
        self.assertEqual(self.bal('expense.ap', self.vend()), D('-21800.00'))
        self.assertEqual(postings.history(ledger.expense_key(e)).count(), 2)

    def test_category_mapping_used_when_present(self):
        acct = Account.objects.filter(account_type='expense', is_group=False).exclude(
            pk=acc.mapped_account('expense.default').pk).first()
        AccountMapping.objects.create(key=f'expense.category.{self.tech.pk}', account=acct, description='Tech')
        self.expense()
        self.assertEqual(acc.balance(acct), D('11800.00'))

    def test_payment_with_tds_settles_bill(self):
        bank = make_bank()
        e = self.expense()
        services.record_payment(e, payment_date=date(2026, 5, 15), method='bank_transfer', amount=D('10800'),
                                tds_amount=D('1000'), bank=bank)
        e.refresh_from_db()
        self.assertEqual(e.payment_status, CompanyExpense.PAID)
        self.assertEqual(self.bal('expense.ap', self.vend()), D('0.00'))
        self.assertEqual(self.bal('expense.tds_payable'), D('1000.00'))
        self.assertEqual(acc.balance(bank.ledger_account), D('-10800.00'))
        with self.assertRaises(ValidationError):
            services.record_payment(e, payment_date=date(2026, 5, 16), method='upi', amount=D('1'))

    def test_overpay_with_tds_blocked(self):
        e = self.expense()
        with self.assertRaises(ValidationError):
            services.record_payment(e, payment_date=date(2026, 5, 15), method='upi', amount=D('11000'),
                                    tds_amount=D('1000'))
        self.assertFalse(JournalEntry.objects.filter(source_type='expense_payment').exists())

    def test_default_bank_without_fk(self):
        bank = make_bank()
        bank.is_default = True
        bank.save()
        e = self.expense()
        self.pay(e, D('500'))
        self.assertEqual(acc.balance(bank.ledger_account), D('-500.00'))

    def test_cancel_reverses_bill(self):
        e = self.expense()
        services.cancel_expense(e, 'duplicate')
        self.assertIsNone(postings.current(ledger.expense_key(e)))
        self.assertEqual(self.bal('expense.default'), D('0.00'))
        self.assertEqual(postings.history(ledger.expense_key(e)).count(), 1)

    def test_foreign_currency_converted(self):
        self.expense(currency='USD', exchange_rate=D('83.5'), amount_before_tax=D('100'), gst_amount=D('0'),
                     igst_amount=D('0'))
        self.assertEqual(self.bal('expense.default'), D('8350.00'))

    def test_claim_lifecycle(self):
        c = self.claim('5000')
        services.submit_claim(c)
        self.assertFalse(JournalEntry.objects.filter(source_type='expense_claim').exists())
        services.approve_claim(c, '4000')
        emp = ('employee', self.emp.pk)
        self.assertEqual(self.bal('claim.reimbursement_payable', emp), D('-4000.00'))
        self.reimb(c, D('1500'))
        self.assertEqual(self.bal('claim.reimbursement_payable', emp), D('-2500.00'))
        services.approve_claim(c, '5000')
        self.assertEqual(self.bal('claim.reimbursement_payable', emp), D('-3500.00'))
        self.reimb(c, D('3500'), ref='R2')
        c.refresh_from_db()
        self.assertEqual(c.status, c.REIMBURSED)
        self.assertEqual(self.bal('claim.reimbursement_payable', emp), D('0.00'))
        self.assertEqual(self.bal('claim.expense_default'), D('5000.00'))

    def test_claim_cancel_after_approval_reverses(self):
        c = self.claim('900')
        services.submit_claim(c)
        services.approve_claim(c)
        services.cancel_claim(c, 'not needed')
        self.assertEqual(self.bal('claim.reimbursement_payable'), D('0.00'))

    def test_closed_period_blocks_expense(self):
        acc.close_period(acc.period_for(date(2026, 5, 10)), 'closed')
        with self.assertRaises(ValidationError):
            self.expense()
        self.assertFalse(CompanyExpense.objects.exists())

    def test_backfill_dry_run_and_apply(self):
        e = self.expense()
        self.pay(e, D('1000'))
        for k in (ledger.expense_key(e), ledger.payment_key(e.payments.get())):
            postings.unpost(k, 'test')
        out = StringIO()
        call_command('backfill_expense_ledger', stdout=out)
        self.assertIn('expenses to post: 1', out.getvalue())
        self.assertIn('vendor payments to post: 1', out.getvalue())
        self.assertIn('dry-run', out.getvalue())
        call_command('backfill_expense_ledger', '--apply', stdout=StringIO())
        out = StringIO()
        call_command('backfill_expense_ledger', stdout=out)
        self.assertIn('expenses to post: 0 (already posted: 1)', out.getvalue())
        self.assertEqual(self.bal('expense.ap', self.vend()), D('-10800.00'))
