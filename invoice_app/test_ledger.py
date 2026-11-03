"""Stage 4: invoice lifecycle, receipts and credit notes in the ledger."""
from datetime import date
from decimal import Decimal as D
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounting import postings
from accounting import services as acc
from accounting.models import JournalEntry
from .models import Client, CreditNote, Invoice, InvoiceLine, InvoicePayment
from . import ledger


def bal(key, party=None):
    a = acc.mapped_account(key)
    if party:
        return postings.party_balance(a, *party)
    return acc.balance(a)


class InvoiceLedgerTests(TestCase):
    def setUp(self):
        self.client_a = Client.objects.create(name='Acme')
        self.inv = self._inv('L001', igst=False)

    def _inv(self, no, igst=False, d=date(2026, 10, 31)):
        kw = dict(tax_type='igst', tax_rate=D('18'), igst_amount=D('18000.00')) if igst else dict(
            tax_type='cgst_sgst', tax_rate=D('18'), cgst_amount=D('9000.00'), sgst_amount=D('9000.00'))
        i = Invoice.objects.create(client=self.client_a, invoice_number=no, invoice_date=d, due_date=d,
                                   total=D('100000.00'), taxable_value=D('100000.00'), **kw)
        InvoiceLine.objects.create(invoice=i, project_name='P', description='x', quantity_days=D('1'), amount=D('100000.00'))
        return i

    def party(self):
        return ('customer', str(self.client_a.pk))

    def test_new_invoice_is_draft_and_unposted(self):
        self.assertEqual(self.inv.status, Invoice.DRAFT)
        self.assertIsNone(self.inv.ledger_entry)

    def test_finalize_posts_balanced_entry(self):
        ledger.finalize_invoice(self.inv)
        e = self.inv.ledger_entry
        self.assertEqual(e.status, JournalEntry.POSTED)
        lines = list(e.lines.all())
        self.assertEqual(sum(l.debit for l in lines), sum(l.credit for l in lines))
        self.assertEqual(bal('invoice.ar', self.party()), D('118000.00'))
        self.assertEqual(bal('invoice.revenue'), D('100000.00'))
        self.assertEqual(bal('gst.output.cgst'), D('9000.00'))
        self.assertEqual(bal('gst.output.sgst'), D('9000.00'))
        self.assertEqual(bal('gst.output.igst'), D('0.00'))

    def test_igst_invoice(self):
        inv = self._inv('L002', igst=True)
        ledger.finalize_invoice(inv)
        self.assertEqual(bal('gst.output.igst'), D('18000.00'))

    def test_post_is_idempotent(self):
        ledger.finalize_invoice(self.inv)
        n = JournalEntry.objects.count()
        postings.post_invoice(self.inv)
        ledger.post_to_ledger(self.inv)
        self.assertEqual(JournalEntry.objects.count(), n)

    def test_finalized_amount_edit_blocked_text_edit_allowed(self):
        ledger.finalize_invoice(self.inv)
        inv = Invoice.objects.get(pk=self.inv.pk)
        inv.taxable_value = D('90000.00')
        with self.assertRaises(ValidationError):
            inv.save()
        inv = Invoice.objects.get(pk=self.inv.pk)
        inv.pdf_number_override = 'X1'
        inv.save()

    def test_cancel_reverses_and_requires_reason(self):
        ledger.finalize_invoice(self.inv)
        with self.assertRaises(ValidationError):
            ledger.cancel_invoice(self.inv, '')
        ledger.cancel_invoice(self.inv, 'Raised in error', date(2026, 11, 2))
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Invoice.CANCELLED)
        self.assertTrue(self.inv.cancelled)
        self.assertIsNone(self.inv.ledger_entry)
        self.assertEqual(bal('invoice.ar', self.party()), D('0.00'))
        self.assertEqual(bal('invoice.revenue'), D('0.00'))
        self.assertEqual(JournalEntry.objects.filter(status=JournalEntry.REVERSED).count(), 1)
        inv = Invoice.objects.get(pk=self.inv.pk)
        inv.cancelled = False
        with self.assertRaises(ValidationError):
            inv.save()

    def test_cancel_blocked_with_receipts(self):
        ledger.finalize_invoice(self.inv)
        InvoicePayment.objects.create(invoice=self.inv, receipt_date=date(2026, 11, 5), amount=D('1000'))
        with self.assertRaises(ValidationError):
            ledger.cancel_invoice(self.inv, 'x')

    def test_receipt_with_tds_and_charges(self):
        ledger.finalize_invoice(self.inv)
        p = InvoicePayment.objects.create(invoice=self.inv, receipt_date=date(2026, 11, 10), amount=D('107500.00'),
                                          deductions=D('10500.00'), bank_charges=D('500.00'))
        self.assertEqual(bal('invoice.ar', self.party()), D('0.00'))
        self.assertEqual(bal('invoice.tds_receivable'), D('10000.00'))
        self.assertEqual(bal('bank.charges'), D('500.00'))
        e = postings.current(postings.receipt_key(p))
        self.assertEqual(sum(l.debit for l in e.lines.all()), D('118000.00'))
        # edit -> reverse + repost
        p.amount = D('100000.00')
        p.save()
        self.assertEqual(bal('invoice.ar', self.party()), D('7500.00'))
        self.assertEqual(postings.history(postings.receipt_key(p)).count(), 2)
        p.save()  # unchanged -> idempotent
        self.assertEqual(postings.history(postings.receipt_key(p)).count(), 2)
        p.delete()
        self.assertEqual(bal('invoice.ar', self.party()), D('118000.00'))
        self.assertEqual(bal('bank.charges'), D('0.00'))

    def test_receipt_on_unposted_invoice_not_posted_until_post(self):
        Invoice.objects.filter(pk=self.inv.pk).update(status=Invoice.FINALIZED)  # legacy invoice
        self.inv.refresh_from_db()
        p = InvoicePayment.objects.create(invoice=self.inv, receipt_date=date(2026, 11, 10), amount=D('18000'))
        self.assertIsNone(postings.current(postings.receipt_key(p)))
        ledger.post_to_ledger(self.inv)
        self.assertIsNotNone(postings.current(postings.receipt_key(p)))
        self.assertEqual(bal('invoice.ar', self.party()), D('100000.00'))

    def test_credit_note_math_and_outstanding(self):
        ledger.finalize_invoice(self.inv)
        cn = ledger.create_credit_note(self.inv, date=date(2026, 11, 15), reason='Discount', taxable_amount=D('10000.00'))
        self.assertEqual((cn.cgst_amount, cn.sgst_amount, cn.igst_amount, cn.total),
                         (D('900.00'), D('900.00'), D('0.00'), D('11800.00')))
        self.assertTrue(cn.number.startswith('CN-L001-'))
        self.assertEqual(bal('invoice.ar', self.party()), D('106200.00'))
        self.assertEqual(bal('invoice.revenue'), D('90000.00'))
        self.assertEqual(bal('gst.output.cgst'), D('8100.00'))
        InvoicePayment.objects.create(invoice=self.inv, receipt_date=date(2026, 11, 16), amount=D('100000'),
                                      deductions=D('1000'))
        s = Invoice.objects.get(pk=self.inv.pk).payment_summary()
        self.assertEqual(s['credited'], D('11800.00'))
        self.assertEqual(s['outstanding'], D('5200.00'))
        with self.assertRaises(ValidationError):
            ledger.create_credit_note(self.inv, date=date(2026, 11, 15), reason='too much', taxable_amount=D('95000'))
        ledger.cancel_credit_note(cn, 'wrong')
        self.assertEqual(bal('invoice.revenue'), D('100000.00'))
        self.assertEqual(Invoice.objects.get(pk=self.inv.pk).payment_summary()['outstanding'], D('17000.00'))

    def test_credit_note_rounding_proportional(self):
        ledger.finalize_invoice(self.inv)
        cn = ledger.create_credit_note(self.inv, date=date(2026, 11, 15), reason='r', taxable_amount=D('333.33'))
        self.assertEqual(cn.cgst_amount, D('30.00'))
        self.assertEqual(cn.total, D('393.33'))

    def test_closed_period_blocks_posting(self):
        fp = acc.period_for(date(2026, 10, 31))
        acc.close_period(fp, 'month end')
        with self.assertRaises(ValidationError):
            ledger.finalize_invoice(self.inv)
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Invoice.DRAFT)  # atomic: nothing changed

    def test_non_inr_cannot_post(self):
        self.inv.currency = 'USD'
        self.inv.save()
        with self.assertRaises(ValidationError):
            ledger.finalize_invoice(self.inv)

    def test_backfill_dry_run_then_apply(self):
        legacy = self._inv('L009')
        Invoice.objects.filter(pk=legacy.pk).update(status=Invoice.FINALIZED)
        legacy.refresh_from_db()
        InvoicePayment.objects.create(invoice=legacy, receipt_date=date(2026, 11, 3), amount=D('50000'))
        out = StringIO()
        call_command('backfill_invoice_ledger', stdout=out)
        self.assertIn('DRY-RUN', out.getvalue())
        self.assertIn('Invoices to post: 1, receipts: 1', out.getvalue())
        self.assertEqual(JournalEntry.objects.count(), 0)
        call_command('backfill_invoice_ledger', '--as-of', '2026-11-01', stdout=StringIO())
        self.assertEqual(JournalEntry.objects.count(), 0)
        call_command('backfill_invoice_ledger', '--apply', '--as-of', '2026-11-01', stdout=StringIO())
        self.assertEqual(JournalEntry.objects.count(), 1)
        call_command('backfill_invoice_ledger', '--apply', stdout=StringIO())
        call_command('backfill_invoice_ledger', '--apply', stdout=StringIO())
        self.assertEqual(JournalEntry.objects.count(), 2)

    def test_views(self):
        r = self.client.post(reverse('invoice_finalize', args=[self.inv.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertIsNotNone(Invoice.objects.get(pk=self.inv.pk).ledger_entry)
        self.client.post(reverse('credit_note_add', args=[self.inv.pk]),
                         {'date': '2026-11-20', 'reason': 'r', 'taxable_amount': '1000'})
        self.assertEqual(CreditNote.objects.count(), 1)
        r = self.client.get(reverse('invoice_detail', args=[self.inv.pk]))
        self.assertContains(r, 'Credit notes')
        self.client.post(reverse('invoice_cancel', args=[self.inv.pk]), {'reason': ''})
        self.assertEqual(Invoice.objects.get(pk=self.inv.pk).status, Invoice.FINALIZED)
