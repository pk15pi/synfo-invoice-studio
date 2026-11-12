import io
import shutil
import tempfile
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from accounting import services as A
from accounting.models import Account, JournalEntry

from . import importer, services as S
from .models import BankAccount, BankTransaction, ImportBatch, ImportDraftRow, mask_number

PDF_MEDIA_ROOT = Path(tempfile.mkdtemp(prefix='qb-bank-import-tests-'))
UI_MEDIA_ROOT = Path(tempfile.mkdtemp(prefix='qb-bank-ui-tests-'))
DRAFT_MEDIA_ROOT = Path(tempfile.mkdtemp(prefix='qb-bank-draft-tests-'))


def make_bank(name='HDFC Current', kind='bank', opening=D('0'), od=None, number='50100012345678'):
    b = BankAccount(kind=kind, name=name, bank_name='HDFC Bank', account_number=number, ifsc='HDFC0001234',
                    opening_balance=opening, opening_date=od)
    b.ledger_account = S.create_ledger_account(name, kind)
    b.full_clean(exclude=['ledger_account'])
    b.save()
    S.sync_opening(b)
    return b


def acct(code):
    return Account.objects.get(code=code)


CSV = ('Date,Narration,Chq/Ref,Withdrawal,Deposit,Balance\n'
       '01/04/2026,Opening deposit,R1,,"10,000.00","10,000.00"\n'
       '02/04/2026,Bank charges,,59.00,,9941.00\n'
       '02/04/2026,Bank charges,,59.00,,9882.00\n'
       'bad-date,oops,,1,,\n')


def csv_upload(text=CSV, name='stmt.csv'):
    return SimpleUploadedFile(name, text.encode('utf-8-sig'), content_type='text/csv')


MAP = {'header_row': '1', 'date': '0', 'description': '1', 'reference': '2', 'debit': '3', 'credit': '4',
       'balance': '5', 'value_date': '', 'amount': ''}


class MaskingTests(TestCase):
    def test_mask(self):
        self.assertEqual(mask_number('50100012345678'), 'XXXX5678')
        self.assertEqual(mask_number(''), '')
        b = make_bank()
        self.assertEqual(b.masked_number, 'XXXX5678')

    def test_list_masks_detail_shows_full(self):
        b = make_bank()
        r = self.client.get(reverse('bank:accounts'))
        self.assertContains(r, 'XXXX5678')
        self.assertNotContains(r, '50100012345678')
        self.assertContains(self.client.get(reverse('bank:account_detail', args=[b.pk])), '50100012345678')


class OpeningTests(TestCase):
    def test_opening_posted_once_and_reposted_on_change(self):
        b = make_bank(opening=D('25000.50'), od=date(2026, 4, 1))
        self.assertEqual(A.balance(b.ledger_account), D('25000.50'))
        self.assertEqual(A.balance(acct('3100')), D('25000.50'))
        e1 = b.opening_entry
        S.sync_opening(b)
        self.assertEqual(b.opening_entry, e1)
        self.assertEqual(JournalEntry.objects.filter(source_type='bank_opening').count(), 1)
        b.opening_balance = D('20000')
        b.save()
        S.sync_opening(b)
        e1.refresh_from_db()
        self.assertEqual(e1.status, 'reversed')
        self.assertEqual(A.balance(b.ledger_account), D('20000.00'))

    def test_negative_opening_overdraft(self):
        b = make_bank(opening=D('-500'), od=date(2026, 4, 1))
        self.assertEqual(A.balance(b.ledger_account), D('-500.00'))

    def test_auto_ledger_under_cash_and_bank(self):
        b = make_bank()
        c = make_bank('Petty cash', 'cash', number='')
        self.assertEqual(b.ledger_account.parent.code, '1100')
        self.assertTrue(b.ledger_account.code.startswith('112'))
        self.assertTrue(c.ledger_account.code.startswith('111'))
        with self.assertRaises(S.BankError):
            S.validate_ledger_account(b.ledger_account, c)

    def test_ifsc_validation(self):
        b = BankAccount(name='X', ifsc='BAD', ledger_account=acct('1120'))
        with self.assertRaises(Exception):
            b.full_clean()


class ImportTests(TestCase):
    def setUp(self):
        self.b = make_bank()

    def _batch(self, up):
        rows = importer.read_rows(up)
        return ImportBatch.objects.create(bank_account=self.b, file_name=up.name, raw_rows=rows, mapping=importer.guess_mapping(rows[0]))

    def test_guess_mapping(self):
        m = importer.guess_mapping(['Date', 'Narration', 'Chq/Ref', 'Withdrawal', 'Deposit', 'Balance'])
        self.assertEqual((m['date'], m['description'], m['reference'], m['debit'], m['credit'], m['balance']), ('0', '1', '2', '3', '4', '5'))

    def test_csv_import_and_idempotent(self):
        bt = self._batch(csv_upload())
        prev = importer.preview(bt, MAP)
        self.assertEqual(sum(1 for r in prev if 'error' in r), 1)
        importer.confirm(bt, MAP)
        self.assertEqual((bt.total_rows, bt.imported, bt.duplicates, bt.errors), (4, 3, 0, 1))
        self.assertEqual(BankTransaction.objects.count(), 3)  # two identical charge rows both kept
        bt2 = self._batch(csv_upload())
        self.assertTrue(all(r.get('duplicate') for r in importer.preview(bt2, MAP) if 'error' not in r))
        importer.confirm(bt2, MAP)
        self.assertEqual((bt2.imported, bt2.duplicates), (0, 3))
        self.assertEqual(BankTransaction.objects.count(), 3)
        with self.assertRaises(ValueError):
            importer.confirm(bt2, MAP)

    def test_xlsx_import_signed_amount(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['Statement for HDFC'])
        ws.append(['Txn Date', 'Description', 'Amount'])
        ws.append([date(2026, 5, 3), 'Client receipt', 1500.5])
        ws.append(['04-05-2026', 'Rent', '(1,200.00)'])
        ws.append(['05-May-2026', 'Fee', '25 Dr'])
        buf = io.BytesIO()
        wb.save(buf)
        bt = self._batch(SimpleUploadedFile('s.xlsx', buf.getvalue()))
        m = {'header_row': '2', 'date': '0', 'description': '1', 'amount': '2'}
        importer.confirm(bt, m)
        self.assertEqual((bt.imported, bt.errors), (3, 0))
        amounts = sorted(t.amount for t in BankTransaction.objects.all())
        self.assertEqual(amounts, [D('-1200.00'), D('-25.00'), D('1500.50')])

    def test_manual_duplicate_skipped(self):
        _, c1 = S.add_transaction(self.b, date(2026, 4, 5), 'Cheque  deposit', credit=D('100'), reference='C1')
        _, c2 = S.add_transaction(self.b, date(2026, 4, 5), 'cheque deposit', credit=D('100.00'), reference='c1')
        self.assertTrue(c1)
        self.assertFalse(c2)

    def test_parse_helpers(self):
        self.assertEqual(importer.parse_amount('1,234.50 Cr'), D('1234.50'))
        self.assertEqual(importer.parse_amount('(10)'), D('-10'))
        self.assertEqual(importer.parse_date('31.03.2027'), date(2027, 3, 31))
        with self.assertRaises(ValueError):
            importer.parse_date('31/31/2026')

    def test_transaction_comparison_uses_reference_and_marks_ambiguous_rows(self):
        from .models import ImportDraftRow
        old, _ = S.add_transaction(self.b, date(2026, 4, 7), 'Supplier payment', debit=D('500'), reference='UTR-77')
        exact = {'transaction_date': old.date, 'description': old.description, 'reference': old.reference,
                 'debit': old.debit, 'credit': old.credit, 'confidence': D('100')}
        self.assertEqual(importer.classify_draft_row(self.b, exact)[0], ImportDraftRow.EXACT)
        changed = dict(exact, debit=D('550'))
        self.assertEqual(importer.classify_draft_row(self.b, changed)[0], ImportDraftRow.MODIFY)
        similar = dict(exact, reference='OTHER-UTR', description='Different payment')
        self.assertEqual(importer.classify_draft_row(self.b, similar)[0], ImportDraftRow.POSSIBLE)


def text_statement_pdf():
    import pymupdf as fitz
    doc = fitz.open()
    doc.set_metadata({'creationDate': 'D:20260401000000', 'modDate': 'D:20260401000000'})
    page = doc.new_page(width=800, height=600)
    for text, y in [('HDFC Bank', 30), ('Statement Period: 01/04/2026 to 30/04/2026', 55),
                    ('Account No: 50100012345678', 75), ('Opening Balance: 1000.00', 92),
                    ('Closing Balance: 1075.00', 102)]:
        page.insert_text((30, y), text)
    for label, x in [('Date', 30), ('Value Date', 105), ('Narration', 205), ('Reference', 390),
                     ('Debit', 500), ('Credit', 585), ('Balance', 670)]:
        page.insert_text((x, 125), label)
    rows = [
        [('01/04/2026', 30), ('01/04/2026', 105), ('Bank charges', 205), ('TXN001', 390), ('25.00', 500), ('0.00', 585), ('975.00', 670)],
        [('02-Apr-2026', 30), ('02-Apr-2026', 105), ('Client NEFT', 205), ('TXN002', 390), ('0.00', 500), ('100.00', 585), ('1075.00', 670)],
    ]
    for entries, y in zip(rows, (150, 175)):
        for text, x in entries:
            page.insert_text((x, y), text)
    page.insert_text((205, 157), 'monthly service')
    page2 = doc.new_page(width=800, height=600)
    page2.insert_text((30, 40), 'Continuation page - no additional transactions')
    data = doc.tobytes()
    doc.close()
    return SimpleUploadedFile('statement.pdf', data, content_type='application/pdf')


@override_settings(MEDIA_ROOT=PDF_MEDIA_ROOT)
class PDFImportReviewTests(TestCase):
    def setUp(self):
        self.bank = make_bank()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(PDF_MEDIA_ROOT, ignore_errors=True)

    def test_pdf_extracts_metadata_rows_and_csv_without_live_financial_records(self):
        from .pdf_import import extract_pdf
        extracted = extract_pdf(text_statement_pdf())
        self.assertEqual(extracted['metadata']['bank_name'], 'HDFC Bank')
        self.assertEqual(extracted['metadata']['masked_account'], 'XXXX5678')
        self.assertEqual(extracted['metadata']['start'], date(2026, 4, 1))
        self.assertEqual(extracted['metadata']['end'], date(2026, 4, 30))
        self.assertEqual(extracted['metadata']['page_count'], 2)
        transactions = [row for row in extracted['rows'] if not row.get('non_transaction')]
        self.assertEqual(len(transactions), 2)
        self.assertEqual(sum(1 for row in extracted['rows'] if row.get('non_transaction')), 2)
        self.assertEqual((transactions[0]['debit'], transactions[0]['credit']), (D('25.00'), D('0.00')))
        self.assertEqual((transactions[1]['debit'], transactions[1]['credit']), (D('0.00'), D('100.00')))
        self.assertEqual(transactions[0]['description'], 'Bank charges monthly service')
        self.assertEqual(transactions[1]['transaction_date'], date(2026, 4, 2))
        self.assertEqual(BankTransaction.objects.count(), 0)

    def test_pdf_upload_creates_separate_review_draft_and_downloadable_csv(self):
        response = self.client.post(reverse('bank:import_upload', args=[self.bank.pk]), {'file': text_statement_pdf()})
        batch = ImportBatch.objects.get()
        self.assertRedirects(response, reverse('bank:import_review', args=[batch.pk]))
        self.assertEqual(batch.status, 'draft_ready')
        self.assertEqual(batch.draft_rows.count(), 4)
        self.assertEqual(batch.extracted_transactions, 2)
        self.assertTrue(batch.original_file.name)
        self.assertTrue(batch.generated_csv.name)
        self.assertEqual(BankTransaction.objects.count(), 0)
        self.assertContains(self.client.get(reverse('bank:import_review', args=[batch.pk])), 'Nothing reaches the bank transactions or accounting tables')
        csv_response = self.client.get(reverse('bank:import_csv_download', args=[batch.pk]))
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn(b'Extraction Confidence', b''.join(csv_response.streaming_content))

    def test_exact_pdf_reupload_reopens_batch_without_creating_second_batch(self):
        upload = text_statement_pdf()
        data = upload.read()
        upload.seek(0)
        first = self.client.post(reverse('bank:import_upload', args=[self.bank.pk]), {'file': upload})
        batch = ImportBatch.objects.get()
        self.assertEqual(first.url, reverse('bank:import_review', args=[batch.pk]))
        second = self.client.post(reverse('bank:import_upload', args=[self.bank.pk]), {
            'file': SimpleUploadedFile('statement.pdf', data, content_type='application/pdf'),
        })
        self.assertEqual(ImportBatch.objects.count(), 1)
        self.assertEqual(second.url, reverse('bank:import_review', args=[batch.pk]))
        self.assertEqual(BankTransaction.objects.count(), 0)

    def test_only_approved_rows_are_imported_and_read_back_verified(self):
        response = self.client.post(reverse('bank:import_upload', args=[self.bank.pk]), {'file': text_statement_pdf()})
        batch = ImportBatch.objects.get()
        rows = list(batch.draft_rows.filter(transaction_date__isnull=False))
        before_journals = JournalEntry.objects.count()
        self.client.post(reverse('bank:import_review', args=[batch.pk]), {
            'action': 'confirm_period', 'from_date': '2026-04-01', 'to_date': '2026-04-30',
        })
        self.client.post(reverse('bank:import_review', args=[batch.pk]), {'action': 'approve_row', 'row_id': rows[0].pk})
        self.assertEqual(BankTransaction.objects.count(), 0)
        self.client.post(reverse('bank:import_review', args=[batch.pk]), {
            'action': 'approve_selected', 'row_ids': [str(rows[1].pk)],
        })
        self.client.post(reverse('bank:import_review', args=[batch.pk]), {'action': 'import_approved'})
        batch.refresh_from_db()
        self.assertEqual(BankTransaction.objects.count(), 2)
        self.assertEqual(batch.status, 'imported')
        self.assertEqual((batch.imported, batch.verified), (2, 2))
        self.assertEqual(batch.draft_rows.filter(verification_status='verified').count(), 2)
        self.assertEqual(JournalEntry.objects.count(), before_journals)

    def test_explicit_modification_is_logged_and_accounting_review_does_not_post(self):
        existing, _ = S.add_transaction(self.bank, date(2026, 4, 1), 'Old charges', debit=D('25'),
                                        reference='TXN001', balance=D('975'))
        before_journals = JournalEntry.objects.count()
        self.client.post(reverse('bank:import_upload', args=[self.bank.pk]), {'file': text_statement_pdf()})
        batch = ImportBatch.objects.get()
        row = batch.draft_rows.get(row_number=3)
        self.assertEqual(row.classification, ImportDraftRow.MODIFY)
        self.client.post(reverse('bank:import_review', args=[batch.pk]), {
            'action': 'confirm_period', 'from_date': '2026-04-01', 'to_date': '2026-04-30',
        })
        self.client.post(reverse('bank:import_review', args=[batch.pk]), {'action': 'approve_change', 'row_id': row.pk})
        self.client.post(reverse('bank:import_review', args=[batch.pk]), {'action': 'import_approved'})
        row.refresh_from_db()
        existing.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(existing.description, 'Bank charges monthly service')
        self.assertEqual(row.before_values['description'], 'Old charges')
        self.assertEqual((row.verification_status, batch.modified, batch.verified), ('verified', 1, 1))
        response = self.client.get(reverse('bank:import_migration', args=[batch.pk]))
        self.assertEqual(response.status_code, 200)
        self.client.post(reverse('bank:import_migration', args=[batch.pk]), {
            'action': 'save_classification', f'class_{row.pk}': 'expense',
        })
        row.refresh_from_db()
        from expense_management.models import CompanyExpense
        draft = CompanyExpense.objects.get(source_bank_transaction=row.actual_transaction)
        self.assertTrue(draft.is_draft)
        self.assertEqual(row.migration_status, 'draft')
        self.assertEqual(JournalEntry.objects.count(), before_journals)

    def test_date_outside_extracted_period_requires_acknowledgement(self):
        self.client.post(reverse('bank:import_upload', args=[self.bank.pk]), {'file': text_statement_pdf()})
        batch = ImportBatch.objects.get()
        self.client.post(reverse('bank:import_review', args=[batch.pk]), {
            'action': 'confirm_period', 'from_date': '2026-04-02', 'to_date': '2026-04-30',
        })
        batch.refresh_from_db()
        self.assertEqual(batch.confirmed_start_date, date(2026, 4, 1))
        response = self.client.post(reverse('bank:import_review', args=[batch.pk]), {
            'action': 'confirm_period', 'from_date': '2026-04-02', 'to_date': '2026-04-30',
            'acknowledge_period': '1',
        })
        self.assertEqual(response.status_code, 302)
        batch.refresh_from_db()
        self.assertEqual(batch.confirmed_start_date, date(2026, 4, 2))
        self.assertTrue(batch.extraction_metadata['date_range_acknowledged'])

    def test_invalid_pdf_is_rejected_with_fallback_guidance(self):
        upload = SimpleUploadedFile('bad.pdf', b'not a PDF', content_type='application/pdf')
        response = self.client.post(reverse('bank:import_upload', args=[self.bank.pk]), {'file': upload})
        self.assertContains(response, 'not a valid PDF')
        self.assertEqual(ImportBatch.objects.count(), 0)
        self.assertEqual(BankTransaction.objects.count(), 0)

    def test_scanned_pdf_without_local_ocr_gets_clear_manual_fallback(self):
        import pymupdf as fitz
        doc = fitz.open()
        doc.new_page()
        data = doc.tobytes()
        doc.close()
        upload = SimpleUploadedFile('scan.pdf', data, content_type='application/pdf')
        with self.assertRaisesRegex(ValueError, 'Tesseract OCR configured'):
            from .pdf_import import extract_pdf
            extract_pdf(upload)

    def test_password_protected_pdf_is_rejected(self):
        import pymupdf as fitz
        doc = fitz.open()
        doc.new_page().insert_text((30, 30), 'Private statement')
        data = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='locked')
        doc.close()
        from .pdf_import import extract_pdf
        with self.assertRaisesRegex(ValueError, 'password-protected'):
            extract_pdf(SimpleUploadedFile('locked.pdf', data, content_type='application/pdf'))

    def _verified_batch(self):
        self.client.post(reverse('bank:import_upload', args=[self.bank.pk]), {'file': text_statement_pdf()})
        batch = ImportBatch.objects.get()
        rows = list(batch.draft_rows.filter(transaction_date__isnull=False).order_by('row_number'))
        self.client.post(reverse('bank:import_review', args=[batch.pk]), {
            'action': 'confirm_period', 'from_date': '2026-04-01', 'to_date': '2026-04-30',
        })
        for row in rows:
            self.client.post(reverse('bank:import_review', args=[batch.pk]), {'action': 'approve_row', 'row_id': row.pk})
        self.client.post(reverse('bank:import_review', args=[batch.pk]), {'action': 'import_approved'})
        for row in rows:
            row.refresh_from_db()
        return batch, rows

    def test_accounting_review_links_only_a_draft_company_expense(self):
        from expense_management.models import CompanyExpense, CompanyExpensePayment, ExpenseCategory
        batch, rows = self._verified_batch()
        withdrawal = next(row for row in rows if row.actual_transaction and row.actual_transaction.debit > 0)
        journals = JournalEntry.objects.count()
        office = ExpenseCategory.for_company().get(name='Office')
        response = self.client.post(reverse('bank:import_migration', args=[batch.pk]), {
            'action': 'save_classification', f'class_{withdrawal.pk}': 'expense',
            f'expense_category_{withdrawal.pk}': office.pk,
        }, follow=True)
        withdrawal.refresh_from_db()
        expense = CompanyExpense.objects.get(source_bank_transaction=withdrawal.actual_transaction)
        self.assertTrue(expense.is_draft)
        self.assertEqual(expense.payment_status, CompanyExpense.UNPAID)
        self.assertEqual((expense.total_amount, expense.expense_date), (D('25.00'), date(2026, 4, 1)))
        self.assertEqual(expense.category, office)
        self.assertEqual(withdrawal.migration_status, 'draft')
        self.assertContains(response, f'Draft company expense {expense.expense_id}')
        self.assertContains(response, 'not posted or paid')
        self.assertEqual(JournalEntry.objects.count(), journals)
        self.assertFalse(CompanyExpensePayment.objects.filter(expense=expense).exists())
        self.client.post(reverse('bank:import_migration', args=[batch.pk]), {
            'action': 'save_classification', f'class_{withdrawal.pk}': 'expense',
            f'expense_category_{withdrawal.pk}': office.pk,
        })
        self.assertEqual(CompanyExpense.objects.filter(source_bank_transaction=withdrawal.actual_transaction).count(), 1)
        deposit = next(row for row in rows if row.actual_transaction and row.actual_transaction.credit > 0)
        self.client.post(reverse('bank:import_migration', args=[batch.pk]), {
            'action': 'save_classification', f'class_{deposit.pk}': 'expense',
        })
        self.assertFalse(CompanyExpense.objects.filter(source_bank_transaction=deposit.actual_transaction).exists())

    def test_accounting_review_links_only_a_draft_employee_claim(self):
        from employee_management.models import Department, Employee
        from expense_management.models import (EmployeeClaimReview, EmployeeExpenseClaim,
                                               EmployeeReimbursement)
        batch, rows = self._verified_batch()
        withdrawal = next(row for row in rows if row.actual_transaction and row.actual_transaction.debit > 0)
        band = Department.objects.create(name='Finance', code='FIN')
        employee = Employee.objects.create(first_name='Asha', last_name='Rao', department=band,
                                           designation='Analyst', date_of_joining=date(2025, 1, 1),
                                           status=Employee.CONFIRMED)
        journals = JournalEntry.objects.count()
        url = reverse('bank:import_migration', args=[batch.pk])
        self.client.post(url, {'action': 'save_classification', f'class_{withdrawal.pk}': 'reimbursement'})
        self.assertFalse(EmployeeExpenseClaim.objects.exists())
        response = self.client.post(url, {
            'action': 'save_classification', f'class_{withdrawal.pk}': 'reimbursement',
            f'claim_employee_{withdrawal.pk}': employee.pk,
        }, follow=True)
        withdrawal.refresh_from_db()
        claim = EmployeeExpenseClaim.objects.get(source_bank_transaction=withdrawal.actual_transaction)
        self.assertEqual(claim.status, EmployeeExpenseClaim.DRAFT)
        self.assertEqual((claim.employee, claim.amount_claimed), (employee, D('25.00')))
        self.assertEqual(claim.expense_date, date(2026, 4, 1))
        self.assertEqual(withdrawal.migration_status, 'draft')
        self.assertContains(response, f'Draft employee claim {claim.claim_id}')
        self.assertContains(response, 'not submitted or reimbursed')
        self.assertEqual(JournalEntry.objects.count(), journals)
        self.assertFalse(EmployeeClaimReview.objects.filter(claim=claim).exists())
        self.assertFalse(EmployeeReimbursement.objects.filter(claim=claim).exists())


@override_settings(MEDIA_ROOT=DRAFT_MEDIA_ROOT)
class DraftLifecycleTests(TestCase):
    def setUp(self):
        self.bank = make_bank()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(DRAFT_MEDIA_ROOT, ignore_errors=True)

    def _upload_pdf(self):
        response = self.client.post(reverse('bank:import_upload', args=[self.bank.pk]), {'file': text_statement_pdf()})
        self.assertEqual(response.status_code, 302)
        return ImportBatch.objects.get()

    def _create_csv_draft(self, upload):
        response = self.client.post(reverse('bank:import_upload', args=[self.bank.pk]), {'file': upload})
        batch = ImportBatch.objects.latest('id')
        self.assertEqual(response.url, reverse('bank:import_batch', args=[batch.pk]))
        response = self.client.post(reverse('bank:import_batch', args=[batch.pk]), dict(MAP, action='create_draft'))
        self.assertEqual(response.url, reverse('bank:import_review', args=[batch.pk]))
        batch.refresh_from_db()
        return batch

    def test_cancel_preserves_source_rows_approved_decision_and_audit(self):
        batch = self._upload_pdf()
        approved = batch.draft_rows.filter(classification=ImportDraftRow.NEW).first()
        self.client.post(reverse('bank:import_review', args=[batch.pk]), {'action': 'approve_row', 'row_id': approved.pk})
        batch.refresh_from_db()
        old_rows = list(batch.draft_rows.values_list('pk', 'review_status', 'decision'))
        source_name, csv_name = batch.original_file.name, batch.generated_csv.name
        before_transactions = BankTransaction.objects.count()
        response = self.client.post(reverse('bank:import_cancel', args=[batch.pk]), {'cancellation_reason': 'Statement uploaded to the wrong account.'})
        self.assertEqual(response.url, reverse('bank:import_review', args=[batch.pk]))
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'cancelled')
        self.assertEqual(batch.cancelled_from_status, 'partially_approved')
        self.assertIsNotNone(batch.cancelled_at)
        self.assertEqual(batch.cancelled_by_label, 'Local user')
        self.assertEqual(batch.cancellation_reason, 'Statement uploaded to the wrong account.')
        self.assertEqual(list(batch.draft_rows.values_list('pk', 'review_status', 'decision')), old_rows)
        self.assertTrue(batch.original_file.storage.exists(source_name))
        self.assertTrue(batch.generated_csv.storage.exists(csv_name))
        self.assertEqual(BankTransaction.objects.count(), before_transactions)
        self.assertEqual(self.client.get(reverse('bank:import_csv_download', args=[batch.pk])).status_code, 200)
        review = self.client.get(reverse('bank:import_review', args=[batch.pk]))
        self.assertContains(review, 'Draft cancelled')
        self.assertContains(review, 'wrong account')
        self.client.post(reverse('bank:import_review', args=[batch.pk]), {'action': 'import_approved'})
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'cancelled')
        self.assertEqual(BankTransaction.objects.count(), before_transactions)
        self.assertEqual(self.client.get(reverse('bank:import_migration', args=[batch.pk])).url, reverse('bank:import_review', args=[batch.pk]))

    def test_cancel_requires_a_reason_and_is_idempotently_blocked_after_cancel(self):
        batch = self._upload_pdf()
        self.client.post(reverse('bank:import_cancel', args=[batch.pk]), {'cancellation_reason': ''})
        batch.refresh_from_db()
        self.assertNotEqual(batch.status, 'cancelled')
        self.assertFalse(batch.cancelled_at)
        self.client.post(reverse('bank:import_cancel', args=[batch.pk]), {'cancellation_reason': 'Wrong test file.'})
        cancelled_at = ImportBatch.objects.get(pk=batch.pk).cancelled_at
        self.client.post(reverse('bank:import_cancel', args=[batch.pk]), {'cancellation_reason': 'Retry cancellation.'})
        batch.refresh_from_db()
        self.assertEqual(batch.cancelled_at, cancelled_at)
        self.assertEqual(batch.cancellation_reason, 'Wrong test file.')

    def test_imported_and_importing_batches_cannot_be_cancelled(self):
        batch = self._upload_pdf()
        txn, _ = S.add_transaction(self.bank, date(2026, 4, 1), 'Already imported', credit=D('10'), batch=batch)
        batch.status, batch.imported = 'imported', 1
        batch.save(update_fields=['status', 'imported'])
        self.client.post(reverse('bank:import_cancel', args=[batch.pk]), {'cancellation_reason': 'Mistake.'})
        batch.refresh_from_db()
        txn.refresh_from_db()
        self.assertEqual(batch.status, 'imported')
        self.assertTrue(BankTransaction.objects.filter(pk=txn.pk).exists())
        batch.status = 'importing'
        batch.save(update_fields=['status'])
        self.client.post(reverse('bank:import_cancel', args=[batch.pk]), {'cancellation_reason': 'Mistake.'})
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'importing')

    def test_pdf_regeneration_creates_a_new_version_and_rechecks_existing_rows(self):
        existing, _ = S.add_transaction(self.bank, date(2026, 4, 1), 'Bank charges monthly service',
                                        debit=D('25'), reference='TXN001', value_date=date(2026, 4, 1), balance=D('975'))
        batch = self._upload_pdf()
        previous_rows = list(batch.draft_rows.values_list('pk', 'classification', 'justification'))
        source_name, csv_name = batch.original_file.name, batch.generated_csv.name
        before_txns = BankTransaction.objects.count()
        response = self.client.post(reverse('bank:import_regenerate', args=[batch.pk]))
        version = ImportBatch.objects.latest('id')
        self.assertRedirects(response, reverse('bank:import_review', args=[version.pk]))
        batch.refresh_from_db()
        self.assertEqual(version.source_batch_id, batch.pk)
        self.assertEqual(version.version_number, 2)
        self.assertEqual(version.original_file.name, source_name)
        self.assertNotEqual(version.generated_csv.name, csv_name)
        self.assertTrue(version.original_file.storage.exists(source_name))
        self.assertTrue(batch.generated_csv.storage.exists(csv_name))
        self.assertEqual(list(batch.draft_rows.values_list('pk', 'classification', 'justification')), previous_rows)
        self.assertEqual(version.draft_rows.get(reference='TXN001').classification, ImportDraftRow.EXACT)
        self.assertEqual(version.regeneration_summary['existing_matches'], 1)
        self.assertGreaterEqual(version.regeneration_summary['newly_extracted_count'], 2)
        self.assertEqual(BankTransaction.objects.count(), before_txns)
        self.assertEqual(existing.pk, version.draft_rows.get(reference='TXN001').existing_transaction_id)
        self.assertContains(self.client.get(reverse('bank:import_review', args=[version.pk])), 'Regeneration comparison')
        self.assertContains(self.client.get(reverse('bank:import_review', args=[version.pk])), 'Draft v1')

    def test_csv_and_xlsx_regeneration_use_the_selected_retained_upload(self):
        csv_batch = self._create_csv_draft(csv_upload(name='source.csv'))
        self.client.post(reverse('bank:import_regenerate', args=[csv_batch.pk]))
        csv_version = ImportBatch.objects.latest('id')
        self.assertEqual(csv_version.version_number, 2)
        self.assertEqual(csv_version.source_kind, 'csv')
        self.assertEqual(csv_version.extracted_transactions, csv_batch.extracted_transactions)
        self.assertEqual(BankTransaction.objects.count(), 0)

        import openpyxl
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(['Date', 'Narration', 'Reference', 'Withdrawal', 'Deposit', 'Balance'])
        sheet.append(['01/04/2026', 'Office supplies', 'X-22', '50.00', '', '950.00'])
        content = io.BytesIO()
        workbook.save(content)
        workbook.close()
        xlsx_batch = self._create_csv_draft(SimpleUploadedFile('source.xlsx', content.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))
        self.client.post(reverse('bank:import_regenerate', args=[xlsx_batch.pk]))
        xlsx_version = ImportBatch.objects.latest('id')
        self.assertEqual(xlsx_version.version_number, 2)
        self.assertEqual(xlsx_version.source_kind, 'xlsx')
        self.assertEqual(xlsx_version.draft_rows.get().description, 'Office supplies')
        self.assertEqual(BankTransaction.objects.count(), 0)

    def test_cancelled_version_can_regenerate_without_changing_its_history(self):
        batch = self._upload_pdf()
        old_rows = list(batch.draft_rows.values_list('pk', 'review_status'))
        self.client.post(reverse('bank:import_cancel', args=[batch.pk]), {'cancellation_reason': 'Wrong period.'})
        self.client.post(reverse('bank:import_regenerate', args=[batch.pk]))
        batch.refresh_from_db()
        version = ImportBatch.objects.latest('id')
        self.assertEqual(batch.status, 'cancelled')
        self.assertEqual(list(batch.draft_rows.values_list('pk', 'review_status')), old_rows)
        self.assertEqual((version.source_batch_id, version.version_number), (batch.pk, 2))
        self.assertEqual(version.status, 'draft_ready')

    def test_regeneration_rejects_missing_or_modified_original_source(self):
        batch = ImportBatch.objects.create(bank_account=self.bank, file_name='missing.csv', source_kind='csv',
                                           status='draft_ready', file_sha256='expected')
        missing = self.client.post(reverse('bank:import_regenerate', args=[batch.pk]))
        self.assertEqual(missing.status_code, 302)
        self.assertEqual(ImportBatch.objects.count(), 1)
        batch.original_file.save('source.csv', ContentFile(b'Date,Narration,Amount\n01/04/2026,Test,1\n'), save=True)
        batch.file_sha256 = 'not-the-source-hash'
        batch.save(update_fields=['file_sha256'])
        self.client.post(reverse('bank:import_regenerate', args=[batch.pk]))
        self.assertEqual(ImportBatch.objects.count(), 1)

    def test_review_ui_has_cancel_dialog_full_text_and_scrolling_table(self):
        batch = self._upload_pdf()
        response = self.client.get(reverse('bank:import_review', args=[batch.pk]))
        self.assertContains(response, 'data-open-dialog="#bk-cancel-dialog"')
        self.assertContains(response, 'Cancellation reason')
        self.assertContains(response, 'Bank charges monthly service')
        self.assertContains(response, 'aria-label="Statement transaction comparison table"')
        self.assertContains(response, 'Draft v1')


class MatchingTests(TestCase):
    def setUp(self):
        self.b = make_bank()
        e = A.create_and_post(date(2026, 4, 10), 'Customer receipt', [
            {'account': self.b.ledger_account, 'debit': D('1000')}, {'account': acct('4900'), 'credit': D('1000')}])
        self.line = e.lines.get(account=self.b.ledger_account)
        self.txn, _ = S.add_transaction(self.b, date(2026, 4, 12), 'NEFT receipt', credit=D('1000'))

    def test_exact_match_and_suggestion(self):
        sug = S.suggestions(self.txn)
        self.assertEqual(sug[0]['line'], self.line)
        self.assertTrue(sug[0]['exact'])
        S.match(self.txn, self.line)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, 'matched')
        self.assertEqual(S.suggestions(self.txn), [])

    def test_window_and_direction(self):
        far, _ = S.add_transaction(self.b, date(2026, 4, 20), 'late', credit=D('1000'))
        self.assertEqual(S.suggestions(far), [])
        wd, _ = S.add_transaction(self.b, date(2026, 4, 10), 'out', debit=D('1000'))
        self.assertEqual(S.suggestions(wd), [])
        with self.assertRaises(S.BankError):
            S.match(wd, self.line)

    def test_partial_and_over_match(self):
        S.match(self.txn, self.line, D('400'))
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, 'partial')
        self.assertEqual(S.txn_remaining(self.txn), D('600.00'))
        with self.assertRaises(S.BankError):
            S.match(self.txn, self.line, D('700'))
        S.match(self.txn, self.line, D('600'))
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, 'matched')

    def test_other_account_line_rejected(self):
        other = self.line.entry.lines.exclude(pk=self.line.pk).get()
        with self.assertRaises(S.BankError):
            S.match(self.txn, other)

    def test_unmatch_requires_reason(self):
        m = S.match(self.txn, self.line)
        with self.assertRaises(S.BankError):
            S.unmatch(m, '  ')
        S.unmatch(m, 'wrong receipt')
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, 'unreconciled')
        m.refresh_from_db()
        self.assertFalse(m.active)
        self.assertEqual(m.unmatch_reason, 'wrong receipt')

    def test_ignore(self):
        with self.assertRaises(S.BankError):
            S.set_ignored(self.txn, True, '')
        S.set_ignored(self.txn, True, 'internal')
        self.assertEqual(self.txn.status, 'ignored')
        with self.assertRaises(S.BankError):
            S.match(self.txn, self.line)
        S.set_ignored(self.txn, False)
        self.assertEqual(self.txn.status, 'unreconciled')


class PostingTests(TestCase):
    def test_transfer_single_entry(self):
        a, b = make_bank('A', opening=D('1000'), od=date(2026, 4, 1)), make_bank('B', number='99991111')
        t = S.transfer(a, b, D('300'), date(2026, 4, 2), reference='IMPS1')
        self.assertEqual(t.entry.lines.count(), 2)
        self.assertEqual(A.balance(a.ledger_account), D('700.00'))
        self.assertEqual(A.balance(b.ledger_account), D('300.00'))
        with self.assertRaises(S.BankError):
            S.transfer(a, a, D('1'), date(2026, 4, 2))
        with self.assertRaises(S.BankError):
            S.transfer(a, b, D('0'), date(2026, 4, 2))

    def test_manual_entries(self):
        b = make_bank()
        S.manual_entry(b, date(2026, 4, 3), 'out', D('59'), acct('5700'), 'Charges')
        S.manual_entry(b, date(2026, 4, 3), 'in', D('12.34'), acct('4900'), 'Interest')
        self.assertEqual(A.balance(b.ledger_account), D('-46.66'))
        self.assertEqual(A.balance(acct('5700')), D('59.00'))
        with self.assertRaises(S.BankError):
            S.manual_entry(b, date(2026, 4, 3), 'in', D('1'), b.ledger_account, 'x')


class SummaryTests(TestCase):
    def test_reconciliation_math(self):
        b = make_bank(opening=D('1000.00'), od=date(2026, 4, 1))
        e = A.create_and_post(date(2026, 4, 5), 'Receipt', [{'account': b.ledger_account, 'debit': D('500.10')},
                                                              {'account': acct('4900'), 'credit': D('500.10')}])
        A.create_and_post(date(2026, 4, 6), 'Cheque issued not cleared', [{'account': acct('5700'), 'debit': D('200')},
                                                                           {'account': b.ledger_account, 'credit': D('200')}])
        t1, _ = S.add_transaction(b, date(2026, 4, 5), 'Receipt', credit=D('500.10'))
        S.add_transaction(b, date(2026, 4, 7), 'Charges', debit=D('0.30'))
        S.match(t1, e.lines.get(account=b.ledger_account))
        s = S.summary(b, date(2026, 4, 30))
        self.assertEqual(s['book_balance'], D('1300.10'))
        self.assertEqual(s['statement_balance'], D('1499.80'))
        self.assertEqual(s['difference'], D('199.70'))
        self.assertEqual(s['unreconciled_bank_total'], D('-0.30'))
        self.assertEqual(s['unreconciled_book_total'], D('-200.00'))
        self.assertEqual(s['unexplained'], D('0.00'))
        self.assertIsInstance(s['difference'], D)

    def test_statement_balance_column_used(self):
        b = make_bank()
        S.add_transaction(b, date(2026, 4, 5), 'x', credit=D('10'), balance=D('5010'))
        S.add_transaction(b, date(2026, 4, 6), 'y', debit=D('1'))
        self.assertEqual(S.summary(b, date(2026, 4, 30))['statement_balance'], D('5009.00'))


@override_settings(MEDIA_ROOT=UI_MEDIA_ROOT)
class UISmokeTests(TestCase):
    def setUp(self):
        self.b = make_bank(opening=D('100'), od=date(2026, 4, 1))
        self.t, _ = S.add_transaction(self.b, date(2026, 4, 2), 'NEFT', credit=D('5'))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(UI_MEDIA_ROOT, ignore_errors=True)

    def test_pages(self):
        for name, args in [('bank:accounts', []), ('bank:account_new', []), ('bank:account_detail', [self.b.pk]),
                           ('bank:account_edit', [self.b.pk]), ('bank:transactions', [self.b.pk]),
                           ('bank:import_upload', [self.b.pk]), ('bank:manual_entry', [self.b.pk]),
                           ('bank:reconciliation', [self.b.pk]), ('bank:imports', []), ('bank:txn_detail', [self.t.pk]),
                           ('bank:transfers', []), ('bank:book', ['bank']), ('bank:book', ['cash']),
                           ('bank:legacy_preview', [])]:
            r = self.client.get(reverse(name, args=args))
            self.assertEqual(r.status_code, 200, name)
        self.assertEqual(self.client.get('/bank/book/other/').status_code, 404)

    def test_book_shows_opening(self):
        self.assertContains(self.client.get(reverse('bank:book', args=['bank']) + '?period=current_fy'), 'Opening balance')

    def test_imports_page_links_to_statement_upload_for_each_account(self):
        response = self.client.get(reverse('bank:imports'))
        self.assertContains(response, 'Import a statement')
        self.assertContains(response, reverse('bank:import_upload', args=[self.b.pk]))

    def test_create_account_via_form(self):
        r = self.client.post(reverse('bank:account_new'), {'kind': 'cash', 'name': 'Petty', 'opening_balance': '50',
                                                           'opening_date': '2026-04-01', 'active': 'on'})
        self.assertEqual(r.status_code, 302)
        c = BankAccount.objects.get(name='Petty')
        self.assertEqual(A.balance(c.ledger_account), D('50.00'))

    def test_import_flow_via_ui(self):
        r = self.client.post(reverse('bank:import_upload', args=[self.b.pk]), {'file': csv_upload()})
        bt = ImportBatch.objects.get()
        self.assertRedirects(r, reverse('bank:import_batch', args=[bt.pk]))
        r = self.client.post(reverse('bank:import_batch', args=[bt.pk]), dict(MAP, action='preview'))
        self.assertContains(r, 'Duplicate - skip', count=0)
        self.assertContains(r, 'Error:')
        r = self.client.post(reverse('bank:import_batch', args=[bt.pk]), dict(MAP, action='create_draft'))
        self.assertRedirects(r, reverse('bank:import_review', args=[bt.pk]))
        bt.refresh_from_db()
        self.assertEqual(bt.status, 'draft_ready')
        self.assertEqual(BankTransaction.objects.count(), 1)  # The test fixture transaction remains untouched.
        self.client.post(reverse('bank:import_review', args=[bt.pk]), {
            'action': 'confirm_period', 'from_date': '2026-04-01', 'to_date': '2026-04-02',
        })
        straightforward = list(bt.draft_rows.filter(classification=ImportDraftRow.NEW).values_list('pk', flat=True))
        self.client.post(reverse('bank:import_review', args=[bt.pk]), {'action': 'approve_selected', 'row_ids': straightforward})
        possible = bt.draft_rows.get(classification=ImportDraftRow.POSSIBLE)
        self.client.post(reverse('bank:import_review', args=[bt.pk]), {'action': 'new_and_approve', 'row_id': possible.pk})
        self.client.post(reverse('bank:import_review', args=[bt.pk]), {'action': 'import_approved'})
        bt.refresh_from_db()
        self.assertEqual((bt.status, bt.imported), ('imported', 3))
        self.assertEqual(bt.draft_rows.filter(verification_status='verified').count(), 3)

    def test_match_unmatch_via_ui(self):
        e = A.create_and_post(date(2026, 4, 2), 'r', [{'account': self.b.ledger_account, 'debit': D('5')},
                                                      {'account': acct('4900'), 'credit': D('5')}])
        line = e.lines.get(account=self.b.ledger_account)
        self.assertContains(self.client.get(reverse('bank:txn_detail', args=[self.t.pk])), 'Exact')
        self.client.post(reverse('bank:txn_detail', args=[self.t.pk]), {'line': line.pk})
        self.t.refresh_from_db()
        self.assertEqual(self.t.status, 'matched')
        m = self.t.matches.get()
        self.client.post(reverse('bank:unmatch', args=[m.pk]), {'reason': ''})
        m.refresh_from_db()
        self.assertTrue(m.active)
        self.client.post(reverse('bank:unmatch', args=[m.pk]), {'reason': 'mistake'})
        m.refresh_from_db()
        self.assertFalse(m.active)

    def test_transfer_and_entry_via_ui(self):
        b2 = make_bank('Second', number='11112222')
        r = self.client.post(reverse('bank:transfers'), {'date': '2026-04-03', 'from_account': self.b.pk, 'to_account': b2.pk, 'amount': '40'})
        self.assertEqual(r.status_code, 302)
        r = self.client.post(reverse('bank:manual_entry', args=[self.b.pk]), {'date': '2026-04-03', 'direction': 'out', 'amount': '10',
                                                                               'counter_account': acct('5700').pk, 'narration': 'chg'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(A.balance(self.b.ledger_account), D('50.00'))

    def test_legacy_preview_is_read_only(self):
        from expense_management.models import CompanyExpensePayment, EmployeeReimbursement
        before = (CompanyExpensePayment.objects.count(), EmployeeReimbursement.objects.count())
        self.assertEqual(S.suggest_bank('HDFC a/c ending 5678', [self.b])[1], 'last 4 digits match')
        self.client.get(reverse('bank:legacy_preview'))
        self.assertEqual(before, (CompanyExpensePayment.objects.count(), EmployeeReimbursement.objects.count()))

    def test_local_only(self):
        r = self.client.get(reverse('bank:accounts'), REMOTE_ADDR='10.1.2.3')
        self.assertIn(r.status_code, (403, 404))


ROWS_MEDIA_ROOT = Path(tempfile.mkdtemp(prefix='qb-bank-draft-rows-tests-'))


@override_settings(MEDIA_ROOT=ROWS_MEDIA_ROOT)
class DraftRowListEditTests(TestCase):
    """The 'View draft rows' list and the per-row draft editor."""

    def setUp(self):
        self.bank = make_bank()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(ROWS_MEDIA_ROOT, ignore_errors=True)

    def _draft(self):
        self.client.post(reverse('bank:import_upload', args=[self.bank.pk]), {'file': csv_upload()})
        batch = ImportBatch.objects.latest('id')
        self.client.post(reverse('bank:import_batch', args=[batch.pk]), dict(MAP, action='create_draft'))
        batch.refresh_from_db()
        return batch

    def _list_url(self, batch):
        return reverse('bank:import_draft_rows', args=[batch.pk])

    def _edit_url(self, batch, row):
        return reverse('bank:import_draft_row_edit', args=[batch.pk, row.pk])

    def _post(self, batch, row, **overrides):
        data = {'transaction_date': '2026-04-02', 'value_date': '', 'description': 'Bank charges', 'reference': '',
                'debit': '59.00', 'credit': '', 'balance': ''}
        data.update(overrides)
        return self.client.post(self._edit_url(batch, row), data)

    def _snapshot(self, row):
        row.refresh_from_db()
        return (row.transaction_date, row.description, row.reference, row.debit, row.credit, row.balance,
                row.classification, row.review_status)

    def test_buttons_link_to_draft_rows_list(self):
        batch = self._draft()
        url = self._list_url(batch)
        self.assertContains(self.client.get(reverse('bank:imports')), url)
        self.assertContains(self.client.get(reverse('bank:import_review', args=[batch.pk])), url)

    def test_list_shows_draft_rows_with_edit_links_and_filters(self):
        batch = self._draft()
        response = self.client.get(self._list_url(batch))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Imported draft rows')
        for row in batch.draft_rows.all():
            self.assertContains(response, self._edit_url(batch, row))
        self.assertContains(response, 'Opening deposit')
        bad = batch.draft_rows.get(classification=ImportDraftRow.UNCERTAIN)
        uncertain = self.client.get(self._list_url(batch) + '?classification=uncertain')
        self.assertContains(uncertain, self._edit_url(batch, bad))
        self.assertNotContains(uncertain, 'Opening deposit')
        searched = self.client.get(self._list_url(batch) + '?q=charges')
        self.assertContains(searched, 'Bank charges')
        self.assertNotContains(searched, 'Opening deposit')
        self.assertEqual(self.client.get(self._list_url(batch) + '?show=all&status=nonsense').status_code, 200)

    def test_pending_upload_without_drafts_redirects_to_mapping(self):
        self.client.post(reverse('bank:import_upload', args=[self.bank.pk]), {'file': csv_upload()})
        batch = ImportBatch.objects.get()
        self.assertRedirects(self.client.get(self._list_url(batch)), reverse('bank:import_batch', args=[batch.pk]))

    def test_edit_fixes_uncertain_row_and_refreshes_derived_counts(self):
        batch = self._draft()
        bad = batch.draft_rows.get(classification=ImportDraftRow.UNCERTAIN)
        self.assertEqual((batch.failed_extractions, batch.new_transactions), (1, 2))
        page = self.client.get(self._edit_url(batch, bad))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Edit draft row')
        response = self._post(batch, bad, transaction_date='2026-04-02', description='Interest credit',
                              reference='INT1', debit='', credit='12.50')
        self.assertRedirects(response, self._list_url(batch) + f'#draft-row-{bad.pk}', fetch_redirect_response=False)
        bad.refresh_from_db()
        self.assertEqual((bad.transaction_date, bad.description, bad.reference, bad.debit, bad.credit),
                         (date(2026, 4, 2), 'Interest credit', 'INT1', D('0.00'), D('12.50')))
        self.assertEqual(bad.normalized_description, 'interest credit')
        self.assertEqual((bad.classification, bad.review_status, bad.extraction_error),
                         (ImportDraftRow.NEW, ImportDraftRow.PENDING, ''))
        batch.refresh_from_db()
        self.assertEqual((batch.failed_extractions, batch.new_transactions), (0, 3))
        self.assertEqual(batch.status, 'under_review')
        self.assertEqual(BankTransaction.objects.count(), 0)

    def test_validation_rejects_invalid_values_without_saving(self):
        batch = self._draft()
        row = batch.draft_rows.get(description='Opening deposit')
        before = self._snapshot(row)
        cases = [
            ({'debit': '10', 'credit': '10'}, 'Enter exactly one positive debit or credit amount.'),
            ({'debit': '', 'credit': ''}, 'Enter exactly one positive debit or credit amount.'),
            ({'debit': '-5', 'credit': ''}, 'Amount cannot be negative.'),
            ({'debit': '1.234'}, 'decimal place'),
            ({'description': '   '}, 'This field is required.'),
            ({'transaction_date': ''}, 'This field is required.'),
        ]
        for overrides, message in cases:
            response = self._post(batch, row, **overrides)
            self.assertEqual(response.status_code, 200, overrides)
            self.assertContains(response, message)
            self.assertEqual(self._snapshot(row), before, overrides)

    def test_approved_row_returns_to_pending_after_edit(self):
        batch = self._draft()
        row = batch.draft_rows.get(description='Opening deposit')
        self.client.post(reverse('bank:import_review', args=[batch.pk]), {'action': 'approve_row', 'row_id': row.pk})
        row.refresh_from_db()
        self.assertEqual((row.review_status, row.decision), (ImportDraftRow.APPROVED, 'new'))
        self._post(batch, row, transaction_date='2026-04-01', description='Opening deposit NEFT', reference='R1',
                   debit='', credit='10000.00', balance='10000.00')
        row.refresh_from_db()
        self.assertEqual((row.review_status, row.decision, row.reviewed_at), (ImportDraftRow.PENDING, '', None))
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'under_review')

    def test_in_statement_duplicate_flag_follows_edits(self):
        batch = self._draft()
        first, second = list(batch.draft_rows.filter(description='Bank charges').order_by('row_number'))
        self.assertEqual((second.classification, second.fingerprint_occurrence), (ImportDraftRow.POSSIBLE, 1))
        self._post(batch, second, description='Bank charges GST', debit='60.00')
        second.refresh_from_db()
        self.assertEqual((second.classification, second.fingerprint_occurrence), (ImportDraftRow.NEW, 0))
        self._post(batch, second)  # identical to the first charge again
        second.refresh_from_db()
        self.assertEqual((second.classification, second.fingerprint_occurrence), (ImportDraftRow.POSSIBLE, 1))
        first.refresh_from_db()
        self.assertEqual(first.fingerprint_occurrence, 0)

    def test_statement_warnings_follow_edits_and_withdraw_acknowledgement(self):
        batch = self._draft()
        first = batch.draft_rows.filter(description='Bank charges').order_by('row_number').first()
        ImportBatch.objects.filter(pk=batch.pk).update(extraction_metadata={'statement_warnings_acknowledged': True})
        self._post(batch, first, debit='60.00', balance='9941.00')
        batch.refresh_from_db()
        self.assertTrue(any(w.startswith('Running balance does not reconcile') for w in batch.statement_warnings))
        self.assertFalse(batch.extraction_metadata['statement_warnings_acknowledged'])
        self._post(batch, first, debit='59.00', balance='9941.00')
        batch.refresh_from_db()
        self.assertFalse(any(w.startswith('Running balance does not reconcile') for w in batch.statement_warnings))

    def test_imported_and_verified_rows_are_rejected(self):
        batch = self._draft()
        review = reverse('bank:import_review', args=[batch.pk])
        self.client.post(review, {'action': 'confirm_period', 'from_date': '2026-04-01', 'to_date': '2026-04-02'})
        new_ids = list(batch.draft_rows.filter(classification=ImportDraftRow.NEW).values_list('pk', flat=True))
        self.client.post(review, {'action': 'approve_selected', 'row_ids': new_ids})
        self.client.post(review, {'action': 'import_approved'})
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'imported')
        verified = batch.draft_rows.filter(review_status=ImportDraftRow.VERIFIED).first()
        pending = batch.draft_rows.filter(review_status=ImportDraftRow.PENDING).first()
        txn_count = BankTransaction.objects.count()
        # Closed batch: no row is editable, including still-pending ones.
        for row in (verified, pending):
            before = self._snapshot(row)
            self.assertRedirects(self.client.get(self._edit_url(batch, row)), self._list_url(batch))
            self.assertRedirects(self._post(batch, row, description='Changed'), self._list_url(batch))
            self.assertEqual(self._snapshot(row), before)
        listing = self.client.get(self._list_url(batch) + '?show=all')
        self.assertContains(listing, 'Imported - locked')
        self.assertNotContains(listing, self._edit_url(batch, verified))
        # Row-level guard: even if the batch were reopened, an imported/verified row stays locked.
        ImportBatch.objects.filter(pk=batch.pk).update(status='verification_failed')
        before = self._snapshot(verified)
        response = self._post(batch, verified, description='Changed')
        self.assertRedirects(response, self._list_url(batch))
        self.assertEqual(self._snapshot(verified), before)
        self.assertEqual(BankTransaction.objects.count(), txn_count)
        # ...while an open draft row in that reopened batch can still be corrected.
        self.assertContains(self.client.get(self._list_url(batch)), self._edit_url(batch, pending))

    def test_cancelled_batch_rows_are_read_only(self):
        batch = self._draft()
        self.client.post(reverse('bank:import_cancel', args=[batch.pk]), {'cancellation_reason': 'Wrong account.'})
        row = batch.draft_rows.first()
        before = self._snapshot(row)
        self.assertRedirects(self._post(batch, row, description='Changed'), self._list_url(batch))
        self.assertEqual(self._snapshot(row), before)
        listing = self.client.get(self._list_url(batch))
        self.assertContains(listing, 'Rows are shown read-only')
        self.assertNotContains(listing, self._edit_url(batch, row))

    def test_row_from_another_batch_is_not_found(self):
        batch = self._draft()
        other = ImportBatch.objects.create(bank_account=self.bank, file_name='other.csv', status='draft_ready')
        row = batch.draft_rows.first()
        self.assertEqual(self.client.get(reverse('bank:import_draft_row_edit', args=[other.pk, row.pk])).status_code, 404)


DELETE_MEDIA_ROOT = Path(tempfile.mkdtemp(prefix='qb-bank-draft-delete-tests-'))


@override_settings(MEDIA_ROOT=DELETE_MEDIA_ROOT)
class DraftDeleteTests(TestCase):
    """The 'Delete draft' button next to 'View draft rows'."""

    def setUp(self):
        self.bank = make_bank()
        self.txn, _ = S.add_transaction(self.bank, date(2026, 3, 1), 'Existing NEFT', credit=D('5'))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(DELETE_MEDIA_ROOT, ignore_errors=True)

    def _draft(self, text=CSV, name='stmt.csv'):
        self.client.post(reverse('bank:import_upload', args=[self.bank.pk]), {'file': csv_upload(text, name)})
        batch = ImportBatch.objects.latest('id')
        self.client.post(reverse('bank:import_batch', args=[batch.pk]), dict(MAP, action='create_draft'))
        batch.refresh_from_db()
        return batch

    def _delete(self, batch, reason='Uploaded the wrong statement.', **extra):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse('bank:import_delete', args=[batch.pk]), dict(delete_reason=reason, **extra))

    def _import_new_rows(self, batch):
        review = reverse('bank:import_review', args=[batch.pk])
        self.client.post(review, {'action': 'confirm_period', 'from_date': '2026-04-01', 'to_date': '2026-04-02'})
        ids = list(batch.draft_rows.filter(classification=ImportDraftRow.NEW).values_list('pk', flat=True))
        self.client.post(review, {'action': 'approve_selected', 'row_ids': ids})
        self.client.post(review, {'action': 'import_approved'})
        batch.refresh_from_db()

    def test_buttons_shown_next_to_view_draft_rows(self):
        batch = self._draft()
        url = reverse('bank:import_delete', args=[batch.pk])
        imports = self.client.get(reverse('bank:imports'))
        self.assertContains(imports, url)
        self.assertContains(imports, 'Delete draft')
        review = self.client.get(reverse('bank:import_review', args=[batch.pk]))
        self.assertContains(review, url)
        self.assertContains(review, 'name="return_to" value="review"')

    def test_delete_removes_batch_rows_and_draft_files_only(self):
        from audit.models import AuditEvent
        batch = self._draft()
        other = self._draft(CSV.replace('oops', 'oops two'), 'other.csv')
        storage = batch.original_file.storage
        source, generated = batch.original_file.name, batch.generated_csv.name
        self.assertTrue(storage.exists(source) and storage.exists(generated))
        before_txns = list(BankTransaction.objects.values_list('pk', 'date', 'debit', 'credit', 'description'))
        other_rows = list(other.draft_rows.values_list('pk', 'description', 'review_status'))
        response = self._delete(batch)
        self.assertRedirects(response, reverse('bank:imports'))
        self.assertFalse(ImportBatch.objects.filter(pk=batch.pk).exists())
        self.assertFalse(ImportDraftRow.objects.filter(batch_id=batch.pk).exists())
        self.assertFalse(storage.exists(source))
        self.assertFalse(storage.exists(generated))
        self.assertEqual(list(BankTransaction.objects.values_list('pk', 'date', 'debit', 'credit', 'description')), before_txns)
        self.assertEqual(list(other.draft_rows.values_list('pk', 'description', 'review_status')), other_rows)
        self.assertTrue(other.original_file.storage.exists(other.original_file.name))
        self.assertTrue(AuditEvent.objects.filter(model='ImportBatch', object_id=str(batch.pk), action='delete').exists())
        manual = AuditEvent.objects.get(model='ImportDraftRow', object_id=str(batch.pk), action='delete')
        self.assertEqual(manual.reason, 'Uploaded the wrong statement.')
        # The same file can now be uploaded again.
        self.client.post(reverse('bank:import_upload', args=[self.bank.pk]), {'file': csv_upload()})
        self.assertEqual(ImportBatch.objects.count(), 2)

    def test_pending_and_cancelled_drafts_can_be_deleted(self):
        self.client.post(reverse('bank:import_upload', args=[self.bank.pk]), {'file': csv_upload()})
        pending = ImportBatch.objects.get()
        self.assertEqual(pending.status, ImportBatch.PENDING)
        self._delete(pending)
        self.assertFalse(ImportBatch.objects.exists())
        batch = self._draft()
        self.client.post(reverse('bank:import_cancel', args=[batch.pk]), {'cancellation_reason': 'Wrong account.'})
        self._delete(batch)
        self.assertFalse(ImportBatch.objects.filter(pk=batch.pk).exists())

    def test_get_and_missing_reason_do_not_delete(self):
        batch = self._draft()
        self.assertEqual(self.client.get(reverse('bank:import_delete', args=[batch.pk])).status_code, 405)
        response = self._delete(batch, reason='  ', return_to='review')
        self.assertRedirects(response, reverse('bank:import_review', args=[batch.pk]))
        self.assertTrue(ImportBatch.objects.filter(pk=batch.pk).exists())
        self.assertEqual(batch.draft_rows.count(), 4)

    def test_imported_batch_cannot_be_deleted(self):
        batch = self._draft()
        self._import_new_rows(batch)
        self.assertEqual(batch.status, 'imported')
        rows, txns = batch.draft_rows.count(), BankTransaction.objects.count()
        self._delete(batch)
        self.assertTrue(ImportBatch.objects.filter(pk=batch.pk).exists())
        self.assertEqual((batch.draft_rows.count(), BankTransaction.objects.count()), (rows, txns))
        imports = self.client.get(reverse('bank:imports'))
        self.assertNotContains(imports, reverse('bank:import_delete', args=[batch.pk]))
        self.assertContains(imports, 'disabled title="Rows from this batch have already been imported')
        self.assertNotContains(self.client.get(reverse('bank:import_review', args=[batch.pk])),
                               reverse('bank:import_delete', args=[batch.pk]))

    def test_partially_imported_batch_cannot_be_deleted_even_if_reopened(self):
        batch = self._draft()
        self._import_new_rows(batch)
        ImportBatch.objects.filter(pk=batch.pk).update(status='under_review', imported=0, verified=0, duplicates=0)
        txns = BankTransaction.objects.count()
        self._delete(batch)
        self.assertTrue(ImportBatch.objects.filter(pk=batch.pk).exists())
        self.assertEqual(BankTransaction.objects.count(), txns)

    def test_original_with_versions_is_blocked_and_version_keeps_shared_upload(self):
        root = self._draft()
        version = ImportBatch.objects.create(
            bank_account=self.bank, source_batch=root, version_number=2, file_name=root.file_name,
            source_kind=root.source_kind, original_file=root.original_file.name, file_sha256=root.file_sha256,
            status='draft_ready')
        self._delete(root)
        self.assertTrue(ImportBatch.objects.filter(pk=root.pk).exists())
        self._delete(version)
        self.assertFalse(ImportBatch.objects.filter(pk=version.pk).exists())
        self.assertTrue(root.original_file.storage.exists(root.original_file.name))
        self._delete(root)
        self.assertFalse(ImportBatch.objects.filter(pk=root.pk).exists())
