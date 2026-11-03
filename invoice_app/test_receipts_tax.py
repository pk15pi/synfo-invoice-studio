from datetime import date
from decimal import Decimal as D
from importlib import import_module

from django.apps import apps
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Client, Invoice, InvoicePayment


def inv(total='1000.00', d=date(2026, 4, 10), **k):
    c = Client.objects.get_or_create(name='Tax Co')[0]
    return Invoice.objects.create(client=c, invoice_number=f'T{Invoice.objects.count() + 1:03d}', invoice_date=d,
                                  due_date=d, total=D(total), **k)


class TaxTests(TestCase):
    def test_no_tax_defaults(self):
        i = inv()
        self.assertEqual((i.taxable_value, i.total_tax, i.grand_total, i.tax_type), (D('1000'), D('0'), D('1000'), 'none'))

    def test_cgst_sgst_rupee_down(self):
        i = inv('1001.00', taxable_value=D('1001.00'), tax_type='cgst_sgst', tax_rate=D('18'))
        i.apply_tax('rupee_down')
        i.save()
        self.assertEqual((i.cgst_amount, i.sgst_amount), (D('90'), D('90')))   # 90.09 -> 90
        self.assertEqual((i.total_tax, i.grand_total), (D('180'), D('1181')))

    def test_igst_paise(self):
        i = inv('1001.00', taxable_value=D('1001.00'), tax_type='igst', tax_rate=D('18'))
        i.apply_tax('paise')
        i.save()
        self.assertEqual((i.igst_amount, i.cgst_amount, i.grand_total), (D('180.18'), D('0'), D('1181.18')))

    def test_none_clears_rate(self):
        i = inv(taxable_value=D('1000'), tax_type='bogus', tax_rate=D('18'))
        i.apply_tax()
        i.save()
        self.assertEqual((i.tax_type, i.tax_rate, i.total_tax), ('none', D('0'), D('0')))


class PaymentStatusTests(TestCase):
    def pay(self, i, a, ded='0'):
        return InvoicePayment.objects.create(invoice=i, receipt_date=date(2026, 5, 1), amount=D(a), deductions=D(ded))

    def test_partial_full_overpaid_cancelled(self):
        i = inv()
        self.assertEqual(i.payment_summary()['status'], 'Unpaid')
        self.pay(i, '400')
        s = i.payment_summary()
        self.assertEqual((s['status'], s['outstanding']), ('Partially Paid', D('600')))
        self.pay(i, '500', '100')
        s = i.payment_summary()
        self.assertEqual((s['status'], s['outstanding'], s['deductions']), ('Paid', D('0'), D('100')))
        self.pay(i, '50')
        s = i.payment_summary()
        self.assertEqual((s['status'], s['outstanding']), ('Overpaid', D('-50')))
        i.cancelled = True
        i.save()
        self.assertEqual(i.payment_summary()['status'], 'Cancelled')
        self.assertIsNotNone(i.cancelled_date)
        i.cancelled = False
        i.save()
        self.assertIsNone(i.cancelled_date)


@override_settings(ALLOWED_HOSTS=['testserver', '127.0.0.1', 'localhost'])
class PaymentViewTests(TestCase):
    def test_add_requires_confirm_before_invoice_date(self):
        i = inv()
        url = reverse('payment_add', args=[i.pk])
        data = dict(receipt_date='2026-04-01', amount='100', mode='bank', reference='UTR9', deductions='0')
        self.client.post(url, data)
        self.assertEqual(i.payments.count(), 0)
        self.client.post(url, dict(data, confirm_early='on'))
        self.assertEqual(i.payments.count(), 1)
        self.client.post(url, dict(data, receipt_date='2026-05-01', amount='-5'))
        self.assertEqual(i.payments.count(), 1)
        self.assertEqual(self.client.get(reverse('receipts')).status_code, 200)
        self.assertContains(self.client.get(reverse('receipts')), 'UTR9')
        p = i.payments.get()
        self.client.post(reverse('payment_delete', args=[i.pk, p.pk]))
        self.assertEqual(i.payments.count(), 0)


class MigrationBackfillTests(TestCase):
    def test_existing_invoices_unchanged(self):
        i = inv('2345.67')
        Invoice.objects.filter(pk=i.pk).update(taxable_value=0, grand_total=0, total_tax=5, tax_type='igst')
        import_module('invoice_app.migrations.0007_receipts_tax').backfill(apps, None)
        i.refresh_from_db()
        self.assertEqual((i.total, i.taxable_value, i.grand_total, i.total_tax, i.tax_type),
                         (D('2345.67'), D('2345.67'), D('2345.67'), D('0'), 'none'))
        self.assertFalse(i.cancelled)
        self.assertEqual(i.amount_due, D('2345.67'))
