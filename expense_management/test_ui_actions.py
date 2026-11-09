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

    def test_all_expenses_action_icons(self):
        d = self.draft()
        detail = reverse('exp:expense_detail', args=[d.pk])
        edit = reverse('exp:expense_edit', args=[d.pk])
        r = self.client.get(reverse('exp:all'))
        body = r.content.decode()
        self.assertIn('title="View details" aria-label="View details"', body)
        self.assertIn('title="Edit" aria-label="Edit"', body)
        self.assertIn('title="Cancel" aria-label="Cancel"', body)
        self.assertIn('class="xm-cancel"', body)
        self.assertIn('>Cancel entry</button>', body)
        self.assertIn(f'href="{detail}"', body)
        # Expense ID link and View details share the same detail URL
        self.assertIn(f'<a href="{detail}">{d.expense_id}</a>', body)
        self.assertIn(f'href="{detail}" title="View details"', body)
        self.assertIn(f'href="{edit}"', body)
        self.assertIn('<svg class="ico ico-view"', body)
        self.assertIn('<svg class="ico ico-edit"', body)
        self.assertIn('<svg class="ico ico-deactivate"', body)
        self.assertNotIn('>Edit</a>', body)
        self.assertNotIn('<summary class="button secondary">Cancel</summary>', body)

        c = self.claim()
        claim_detail = reverse('exp:claim_detail', args=[c.pk])
        body = self.client.get(reverse('exp:all')).content.decode()
        self.assertIn(f'<a href="{claim_detail}">{c.claim_id}</a>', body)
        self.assertIn(f'href="{claim_detail}" title="View details"', body)


class ExpenseFormComboTests(Base):
    """Create/Edit expense + claim forms: searchable selects, prior-period help, FY enable/disable."""

    EXPENSE_SELECTS = (
        "vendor", "category", "subcategory", "amount_mode", "currency",
        "client", "project", "department", "employee", "relates_to_fy",
    )
    CLAIM_SELECTS = (
        "employee", "category", "subcategory", "currency", "client", "project", "relates_to_fy",
    )
    PRIOR_SNIPPET = "earlier financial year"

    def test_company_expense_form_marks_selects_and_prior_ui(self):
        from accounting.models import FinancialYear
        from .forms import CompanyExpenseForm

        form = CompanyExpenseForm()
        for name in self.EXPENSE_SELECTS:
            self.assertEqual(form.fields[name].widget.attrs.get("data-combo"), name, name)
        self.assertIn(self.PRIOR_SNIPPET, form.fields["is_prior_period"].help_text)
        self.assertTrue(form.fields["relates_to_fy"].disabled)
        self.assertTrue(form.fields["prior_period_note"].disabled)

        fy = FinancialYear.objects.get_or_create(
            start_year=2025, defaults={"start_date": date(2025, 4, 1), "end_date": date(2026, 3, 31)}
        )[0]
        e = self.expense(is_prior_period=True, relates_to_fy=fy, prior_period_note="old bill")
        CompanyExpense.objects.filter(pk=e.pk).update(is_draft=True)
        e.refresh_from_db()
        edit = CompanyExpenseForm(instance=e)
        self.assertFalse(edit.fields["relates_to_fy"].disabled)
        self.assertFalse(edit.fields["prior_period_note"].disabled)

        # Unticked POST (FY field absent because disabled) must clear relates_to_fy / note.
        cleared = CompanyExpenseForm(
            data={
                "expense_date": "2026-05-10",
                "vendor": str(e.vendor_id),
                "category": str(e.category_id),
                "subcategory": str(e.subcategory_id),
                "description": "VPS",
                "amount_mode": e.amount_mode,
                "amount_before_tax": "10000",
                "gst_amount": "1800",
                "cgst_amount": "0",
                "sgst_amount": "0",
                "igst_amount": "1800",
                "other_taxes": "0",
                "discount": "0",
                "total_amount": "11800",
                "currency": e.currency,
                "exchange_rate": "1",
                # is_prior_period omitted => False; relates_to_fy / note omitted (disabled)
            },
            instance=e,
        )
        self.assertTrue(cleared.is_valid(), cleared.errors)
        self.assertFalse(cleared.cleaned_data["is_prior_period"])
        self.assertIsNone(cleared.cleaned_data["relates_to_fy"])
        self.assertEqual(cleared.cleaned_data["prior_period_note"], "")

    def test_claim_form_marks_selects_and_prior_ui(self):
        from .forms import ClaimForm

        form = ClaimForm()
        for name in self.CLAIM_SELECTS:
            self.assertEqual(form.fields[name].widget.attrs.get("data-combo"), name, name)
        self.assertIn(self.PRIOR_SNIPPET, form.fields["is_prior_period"].help_text)
        self.assertTrue(form.fields["relates_to_fy"].disabled)
        self.assertTrue(form.fields["prior_period_note"].disabled)

    def test_expense_new_page_markup(self):
        r = self.client.get(reverse("exp:expense_new"))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("invoice_app/combobox.js", body)
        for name in self.EXPENSE_SELECTS:
            self.assertIn(f'data-combo="{name}"', body, name)
        self.assertIn(self.PRIOR_SNIPPET, body)
        # Unticked create: Relates to FY rendered disabled
        self.assertRegex(body, r'id="id_relates_to_fy"[^>]*disabled|disabled[^>]*id="id_relates_to_fy"')
        self.assertRegex(body, r'id="id_prior_period_note"[^>]*disabled|disabled[^>]*id="id_prior_period_note"')

    def test_claim_new_page_markup(self):
        r = self.client.get(reverse("exp:claim_new"))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("invoice_app/combobox.js", body)
        for name in self.CLAIM_SELECTS:
            self.assertIn(f'data-combo="{name}"', body, name)
        self.assertIn(self.PRIOR_SNIPPET, body)
        self.assertRegex(body, r'id="id_relates_to_fy"[^>]*disabled|disabled[^>]*id="id_relates_to_fy"')

    def test_expense_edit_prior_enables_fy(self):
        from accounting.models import FinancialYear

        fy = FinancialYear.objects.get_or_create(
            start_year=2025, defaults={"start_date": date(2025, 4, 1), "end_date": date(2026, 3, 31)}
        )[0]
        e = self.expense(is_prior_period=True, relates_to_fy=fy, prior_period_note="old")
        CompanyExpense.objects.filter(pk=e.pk).update(is_draft=True)
        r = self.client.get(reverse("exp:expense_edit", args=[e.pk]))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        # When prior is ticked, relates_to_fy must NOT be disabled
        self.assertIn('id="id_relates_to_fy"', body)
        self.assertNotRegex(body, r'<select[^>]*id="id_relates_to_fy"[^>]*disabled')
        self.assertIn('checked', body)  # prior checkbox


class ClaimMenuNameTests(Base):
    """Expense Management menu / page labels for Add + Review Employee Claim."""

    def test_nav_menu_labels(self):
        r = self.client.get(reverse("exp:dashboard"))
        body = r.content.decode()
        self.assertIn(">Add Employee Claim</a>", body)
        self.assertIn(">Review Employee Claim</a>", body)
        self.assertNotIn(">Employee Claims</a>", body)
        self.assertNotIn(">Review Claims</a>", body)
        # Add points at the create form; Review still at the review queue (routes unchanged).
        self.assertIn(reverse("exp:claim_new"), body)
        self.assertIn(reverse("exp:review"), body)

    def test_add_claim_page_title(self):
        r = self.client.get(reverse("exp:claim_new"))
        self.assertContains(r, "Add Employee Claim")
        self.assertNotContains(r, "New employee claim")

    def test_review_page_title(self):
        r = self.client.get(reverse("exp:review"))
        self.assertContains(r, "Review Employee Claim")
        body = r.content.decode()
        self.assertNotIn(">Review claims<", body)

    def test_dashboard_and_all_quick_links(self):
        for name in ("exp:dashboard", "exp:all"):
            body = self.client.get(reverse(name)).content.decode()
            self.assertIn(">Add Employee Claim</a>", body)
            self.assertNotIn(">+ Employee claim</a>", body)
