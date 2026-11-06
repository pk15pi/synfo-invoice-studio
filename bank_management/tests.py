import io
from datetime import date
from decimal import Decimal as D

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounting import services as A
from accounting.models import Account, JournalEntry

from . import importer, services as S
from .models import BankAccount, BankTransaction, ImportBatch, mask_number


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


class UISmokeTests(TestCase):
    def setUp(self):
        self.b = make_bank(opening=D('100'), od=date(2026, 4, 1))
        self.t, _ = S.add_transaction(self.b, date(2026, 4, 2), 'NEFT', credit=D('5'))

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
        self.client.post(reverse('bank:import_batch', args=[bt.pk]), dict(MAP, action='confirm'))
        bt.refresh_from_db()
        self.assertEqual((bt.status, bt.imported), ('imported', 3))

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
