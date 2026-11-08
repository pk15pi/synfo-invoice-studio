"""UI action rules for the expense module: Edit only when editable, locked documents, no redundant buttons."""
from datetime import date

from django.urls import reverse

from . import services
from .models import CompanyExpense, EmployeeExpenseClaim as C
from .tests import Base, pdf


class ExpenseUIActionTests(Base):
    def draft(self, **kw):
        e = self.expense(**kw)
        CompanyExpense.objects.filter(pk=e.pk).update(is_draft=True)
        e.refresh_from_db()
        return e

    def test_edit_button_only_on_editable_expense(self):
        d = self.draft()
        edit = reverse('exp:expense_edit', args=[d.pk])
        r = self.client.get(reverse('exp:expense_detail', args=[d.pk]))
        self.assertContains(r, edit)
        self.assertContains(r, 'Payments can be recorded after this draft is finalized.')
        self.client.post(reverse('exp:expense_finalize', args=[d.pk]))
        r = self.client.get(reverse('exp:expense_detail', args=[d.pk]))
        self.assertNotContains(r, edit)
        self.assertNotContains(r, 'Finalize &amp; post')
        all_page = self.client.get(reverse('exp:all')).content.decode()
        self.assertNotIn(edit, all_page)

    def test_edit_button_only_on_editable_claim(self):
        c = self.claim()
        edit = reverse('exp:claim_edit', args=[c.pk])
        self.assertContains(self.client.get(reverse('exp:claim_detail', args=[c.pk])), edit)
        services.submit_claim(c); services.start_review(c); services.approve_claim(c)
        self.assertFalse(c.is_editable)
        r = self.client.get(reverse('exp:claim_detail', args=[c.pk]))
        self.assertNotContains(r, edit)
        self.assertRedirects(self.client.get(edit), reverse('exp:claim_detail', args=[c.pk]))

    def test_documents_locked_after_finalize(self):
        d = self.draft()
        att = services.add_attachment(d, pdf(), 'vendor_invoice')
        rep = reverse('exp:att_replace', args=['company', att.pk])
        dele = reverse('exp:att_delete', args=['company', att.pk])
        self.assertContains(self.client.get(reverse('exp:expense_detail', args=[d.pk])), rep)
        self.client.post(reverse('exp:expense_finalize', args=[d.pk]))
        r = self.client.get(reverse('exp:expense_detail', args=[d.pk]))
        self.assertNotContains(r, rep); self.assertNotContains(r, dele)
        self.assertContains(r, reverse('exp:att_view', args=['company', att.pk]))
        self.assertContains(r, reverse('exp:expense_attach', args=[d.pk]))  # can still add documents
        self.client.post(dele)
        self.assertTrue(att.__class__.objects.filter(pk=att.pk).exists())
        self.client.post(rep, {'file': pdf('new.pdf')})
        att.refresh_from_db(); self.assertNotEqual(att.original_filename, 'new.pdf')

    def test_cancelled_expense_blocks_uploads(self):
        d = self.draft()
        services.cancel_expense(d, 'duplicate')
        r = self.client.get(reverse('exp:expense_detail', args=[d.pk]))
        self.assertNotContains(r, reverse('exp:expense_attach', args=[d.pk]))
        self.client.post(reverse('exp:expense_attach', args=[d.pk]), {'doc_type': 'vendor_invoice', 'attachments': pdf()})
        self.assertEqual(d.attachments.count(), 0)

    def test_claim_documents_locked_when_closed(self):
        c = self.claim()
        att = services.add_attachment(c, pdf(), 'receipt') if hasattr(services, 'add_attachment') else None
        services.submit_claim(c); services.start_review(c); services.approve_claim(c)
        if att:
            self.client.post(reverse('exp:att_delete', args=['claim', att.pk]))
            self.assertTrue(att.__class__.objects.filter(pk=att.pk).exists())

    def test_redundant_buttons_removed(self):
        d = self.draft()
        dash = self.client.get(reverse('exp:dashboard')).content.decode()
        for label in ('View all expenses', 'Manage categories', 'Manage vendors'):
            self.assertNotIn(label, dash)
        self.assertIn('class="xm-cards"', dash)
        self.assertNotIn('>All expenses</a></div>', self.client.get(reverse('exp:expense_detail', args=[d.pk])).content.decode())
        self.assertNotContains(self.client.get(reverse('exp:claim_detail', args=[self.claim().pk])), '>Review list<')
        self.assertNotContains(self.client.get(reverse('exp:vendor_detail', args=[self.vendor.pk])), '>Vendors</a></div>')

    def test_create_finalize_pay_workflow(self):
        d = self.draft()
        self.assertTrue(d.is_editable)
        self.client.post(reverse('exp:expense_finalize', args=[d.pk]))
        d.refresh_from_db()
        self.assertFalse(d.is_editable)
        self.client.post(reverse('exp:expense_payment', args=[d.pk]),
                         {'payment_date': '2026-05-15', 'method': 'upi', 'amount': str(d.total_amount)})
        d.refresh_from_db()
        self.assertEqual(d.payment_status, CompanyExpense.PAID)