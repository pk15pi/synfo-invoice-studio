from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse

from . import services as S
from .models import Account, AccountMapping, FinancialPeriod, ImmutableError, JournalEntry, JournalLine
from .money import q2
from .seed import ACCOUNTS, MAPPINGS

D1 = date(2026, 10, 15)


def acc(code):
    return Account.objects.get(code=code)


def lines(amount='1000', dr='1120', cr='4100'):
    return [{'account': acc(dr), 'debit': amount}, {'account': acc(cr), 'credit': amount}]


class RoundingTests(TestCase):
    def test_half_up(self):
        self.assertEqual(q2('2.345'), Decimal('2.35'))
        self.assertEqual(q2('2.335'), Decimal('2.34'))  # HALF_EVEN would give 2.34 too; next one differs
        self.assertEqual(q2('0.125'), Decimal('0.13'))
        self.assertEqual(q2('-0.125'), Decimal('-0.13'))
        self.assertEqual(q2(1.005), Decimal('1.01'))
        self.assertEqual(q2(None), Decimal('0.00'))
        self.assertEqual(q2('1,234.5'), Decimal('1234.50'))
        with self.assertRaises(ValueError):
            q2('abc')


class SeedTests(TestCase):
    def test_coa_seeded(self):
        self.assertEqual(Account.objects.count(), len(ACCOUNTS))
        for code, name in [('1110', 'Cash in Hand'), ('1200', 'Accounts Receivable (Debtors)'), ('1410', 'Input CGST'),
                           ('1500', 'TDS Receivable'), ('2200', 'Salary Payable'), ('2420', 'PF Payable'),
                           ('2610', 'Loans Payable'), ('3900', 'Retained Earnings'), ('4100', 'Service Revenue'),
                           ('5700', 'Bank Charges')]:
            self.assertEqual(acc(code).name, name)
        self.assertTrue(acc('1100').is_group)
        self.assertEqual(acc('1120').parent.code, '1100')
        for a in Account.objects.filter(parent__isnull=False):
            self.assertEqual(a.parent.account_type, a.account_type)
        self.assertEqual(AccountMapping.objects.exclude(key='bank.opening_equity').exclude(key__startswith='expense.category.').count(), len(MAPPINGS))
        self.assertEqual(AccountMapping.objects.get(key='invoice.ar').account.code, '1200')
        self.assertFalse(AccountMapping.objects.filter(account=None).exists())

    def test_seed_idempotent(self):
        from .seed import seed
        seed(Account, AccountMapping)
        self.assertEqual(Account.objects.count(), len(ACCOUNTS))

    def test_used_account_cannot_be_deleted(self):
        S.create_and_post(D1, 'x', lines(dr='5800', cr='1110'))
        a = acc('5800')
        with self.assertRaises((ImmutableError, ProtectedError)):
            a.delete()
        a.active = False
        a.full_clean()
        a.save()
        unused = Account.objects.create(code='5990', name='Temp', account_type='expense')
        unused.delete()

    def test_system_account_locked(self):
        a = acc('1200')
        a.active = False
        with self.assertRaises(ValidationError):
            a.full_clean()
        with self.assertRaises(ImmutableError):
            acc('1200').delete()


class PostingTests(TestCase):
    def test_balanced_post(self):
        e = S.create_and_post(D1, 'Sale', lines('1180.555'))
        self.assertEqual(e.status, 'posted')
        self.assertTrue(e.number.startswith('JV/2026-27/'))
        self.assertEqual(e.total_debit, Decimal('1180.56'))
        self.assertEqual(S.balance(acc('1120')), Decimal('1180.56'))
        self.assertEqual(S.balance(acc('4100')), Decimal('1180.56'))
        e2 = S.create_and_post(D1, 'Sale 2', lines('1'))
        self.assertNotEqual(e.number, e2.number)

    def test_unbalanced_rejected(self):
        with self.assertRaisesMessage(S.PostingError, 'must equal'):
            S.create_and_post(D1, 'x', [{'account': acc('1120'), 'debit': '100'}, {'account': acc('4100'), 'credit': '99.99'}])
        self.assertFalse(JournalEntry.objects.exists())

    def test_line_rules(self):
        bad = [
            [{'account': acc('1120'), 'debit': '10', 'credit': '10'}, {'account': acc('4100'), 'credit': '0'}],
            [{'account': acc('1120'), 'debit': '0'}, {'account': acc('4100'), 'credit': '0'}],
            [{'account': acc('1120'), 'debit': '-5'}, {'account': acc('4100'), 'credit': '-5'}],
            [{'account': acc('1120'), 'debit': '5'}],
            [{'account': acc('1100'), 'debit': '5'}, {'account': acc('4100'), 'credit': '5'}],  # group
        ]
        for ls in bad:
            with self.assertRaises(S.PostingError):
                S.create_and_post(D1, 'x', ls)

    def test_inactive_account_rejected(self):
        a = acc('5800')
        a.active = False
        a.save()
        with self.assertRaisesMessage(S.PostingError, 'inactive'):
            S.create_and_post(D1, 'x', lines(dr='5800', cr='1110'))

    def test_draft_then_post(self):
        e = S.create_draft(D1, 'draft', lines('50'))
        self.assertIsNone(e.number)
        e.narration = 'edited draft'
        e.save()
        S.post(e)
        with self.assertRaises(S.PostingError):
            S.post(e)


class ImmutabilityTests(TestCase):
    def setUp(self):
        self.e = S.create_and_post(D1, 'Sale', lines('100'))

    def test_save_blocked(self):
        e = JournalEntry.objects.get(pk=self.e.pk)
        e.narration = 'tamper'
        with self.assertRaises(ImmutableError):
            e.save()
        l = e.lines.first()
        l.debit = Decimal('5')
        with self.assertRaises(ImmutableError):
            l.save()
        with self.assertRaises(ImmutableError):
            JournalLine.objects.create(entry=e, account=acc('1110'), debit=1)

    def test_delete_blocked(self):
        with self.assertRaises(ImmutableError):
            self.e.delete()
        with self.assertRaises(ImmutableError):
            JournalEntry.objects.filter(pk=self.e.pk).delete()
        with self.assertRaises(ImmutableError):
            self.e.lines.first().delete()
        with self.assertRaises(ImmutableError):
            JournalLine.objects.filter(entry=self.e).delete()
        self.assertEqual(JournalLine.objects.filter(entry=self.e).count(), 2)

    def test_draft_can_be_deleted(self):
        d = S.create_draft(D1, 'd', lines('1'))
        d.delete()
        self.assertFalse(JournalEntry.objects.filter(pk=d.pk).exists())


class ReversalTests(TestCase):
    def test_reverse_mirrors(self):
        e = S.create_and_post(D1, 'Sale', lines('250.50'))
        rev = S.reverse(e, 'Wrong customer', date(2026, 10, 20))
        e.refresh_from_db()
        self.assertEqual(e.status, 'reversed')
        self.assertEqual(rev.reversal_of, e)
        self.assertEqual(rev.status, 'posted')
        self.assertEqual(rev.source_key, f'reversal:{e.pk}')
        for l in rev.lines.all():
            orig = e.lines.get(account=l.account)
            self.assertEqual((l.debit, l.credit), (orig.credit, orig.debit))
        self.assertEqual(S.balance(acc('1120')), 0)
        self.assertEqual(S.balance(acc('1120'), end=date(2026, 10, 19)), Decimal('250.50'))

    def test_reverse_rules(self):
        e = S.create_and_post(D1, 'Sale', lines('10'))
        with self.assertRaises(S.PostingError):
            S.reverse(e, '')
        with self.assertRaises(S.PostingError):
            S.reverse(e, 'early', date(2026, 10, 1))
        rev = S.reverse(e, 'ok', D1)
        with self.assertRaises(S.PostingError):
            S.reverse(e, 'again', D1)
        with self.assertRaises(S.PostingError):
            S.reverse(rev, 'rev of rev', D1)
        with self.assertRaises(S.PostingError):
            S.reverse(S.create_draft(D1, 'd', lines('1')), 'draft', D1)

    def test_reverse_into_closed_period_blocked(self):
        e = S.create_and_post(D1, 'Sale', lines('10'))
        S.close_period(S.period_for(date(2026, 11, 1)), 'Nov closed')
        with self.assertRaises(S.PostingError):
            S.reverse(e, 'x', date(2026, 11, 2))
        S.reverse(e, 'x', date(2026, 10, 31))


class IdempotencyTests(TestCase):
    def test_post_from_source_once(self):
        e1, c1 = S.post_from_source('invoice:7:issue', D1, 'Invoice 7', lines('500'), source_id=7)
        e2, c2 = S.post_from_source('invoice:7:issue', D1, 'Invoice 7 again', lines('999'), source_id=7)
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(e1.pk, e2.pk)
        self.assertEqual(JournalEntry.objects.filter(source_key='invoice:7:issue').count(), 1)
        self.assertEqual(e1.source_type, 'invoice')
        self.assertEqual(S.balance(acc('4100')), Decimal('500.00'))

    def test_failed_post_leaves_nothing(self):
        with self.assertRaises(S.PostingError):
            S.post_from_source('invoice:8:issue', D1, 'x', [{'account': acc('1120'), 'debit': 1}, {'account': acc('4100'), 'credit': 2}])
        self.assertFalse(JournalEntry.objects.filter(source_key='invoice:8:issue').exists())
        e, created = S.post_from_source('invoice:8:issue', D1, 'x', lines('2'))
        self.assertTrue(created)

    def test_key_required(self):
        with self.assertRaises(S.PostingError):
            S.post_from_source('', D1, 'x', lines())

    def test_mapping_lookup(self):
        self.assertEqual(S.mapped_account('invoice.revenue').code, '4100')
        AccountMapping.objects.filter(key='invoice.revenue').update(account=None)
        with self.assertRaises(S.PostingError):
            S.mapped_account('invoice.revenue')


class PeriodTests(TestCase):
    def test_fy_periods(self):
        fy = S.ensure_financial_year(2026)
        ps = list(fy.periods.all())
        self.assertEqual(len(ps), 12)
        self.assertEqual((ps[0].start_date, ps[-1].end_date), (date(2026, 4, 1), date(2027, 3, 31)))
        self.assertEqual(fy.name, 'FY 2026-27')
        self.assertEqual(S.period_for(date(2027, 2, 28)).end_date, date(2027, 2, 28))
        S.ensure_financial_year(2026)
        self.assertEqual(FinancialPeriod.objects.filter(financial_year=fy).count(), 12)

    def test_closed_period_blocks_posting(self):
        p = S.period_for(D1)
        with self.assertRaises(ValidationError):
            S.close_period(p, '  ')
        S.close_period(p, 'October filed')
        p.refresh_from_db()
        self.assertEqual(p.status, 'closed')
        self.assertIsNotNone(p.closed_at)
        with self.assertRaisesMessage(S.PostingError, 'closed'):
            S.create_and_post(D1, 'x', lines())
        with self.assertRaises(S.PostingError):
            S.assert_period_open(D1)
        d = S.create_draft(D1, 'draft allowed', lines())
        with self.assertRaises(S.PostingError):
            S.post(d)
        with self.assertRaises(S.PostingError):
            S.post_from_source('k:1', D1, 'x', lines())
        with self.assertRaises(ValidationError):
            S.reopen_period(p, '')
        S.reopen_period(p, 'Late bill')
        p.refresh_from_db()
        self.assertEqual((p.status, p.reopen_reason), ('open', 'Late bill'))
        self.assertEqual(list(p.events.values_list('action', flat=True)), ['reopen', 'close'])
        S.post(d)


class LedgerTests(TestCase):
    def test_running_balance(self):
        S.create_and_post(date(2026, 9, 30), 'Opening', lines('100'))
        S.create_and_post(date(2026, 10, 2), 'In', lines('50'))
        S.create_and_post(date(2026, 10, 3), 'Out', lines('30', dr='5800', cr='1120'))
        data = S.ledger(acc('1120'), date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(data['opening'], Decimal('100.00'))
        self.assertEqual([r['balance'] for r in data['rows']], [Decimal('150.00'), Decimal('120.00')])
        self.assertEqual(data['closing'], Decimal('120.00'))
        inc = S.ledger(acc('4100'), date(2026, 4, 1), date(2027, 3, 31))
        self.assertEqual(inc['closing'], Decimal('150.00'))


class UISmokeTests(TestCase):
    def test_pages_render(self):
        e = S.create_and_post(D1, 'Smoke sale', lines('10'))
        for name, args in [('acc:home', []), ('acc:accounts', []), ('acc:account_new', []), ('acc:account_edit', [acc('1120').pk]),
                           ('acc:journal', []), ('acc:journal_new', []), ('acc:journal_detail', [e.pk]),
                           ('acc:ledger', [acc('1120').pk]), ('acc:ledger_pick', []), ('acc:periods', []), ('acc:mappings', [])]:
            r = self.client.get(reverse(name, args=args), {'period': 'fy', 'fy': '2026'} if 'ledger' in name or name == 'acc:journal' else {})
            self.assertEqual(r.status_code, 200, name)
        self.assertContains(self.client.get(reverse('acc:ledger', args=[acc('1120').pk]), {'period': 'fy', 'fy': '2026'}), 'Smoke sale')
        self.assertContains(self.client.get('/'), 'Accounting')

    def test_remote_blocked(self):
        r = self.client.get(reverse('acc:accounts'), REMOTE_ADDR='10.0.0.5')
        self.assertEqual(r.status_code, 403)

    def test_manual_journal_flow(self):
        r = self.client.post(reverse('acc:journal_new'), {
            'date': '2026-10-10', 'narration': 'Rent Oct', 'action': 'post',
            'account_0': acc('5200').pk, 'debit_0': '25000', 'account_1': acc('1120').pk, 'credit_1': '25000'})
        e = JournalEntry.objects.get(narration='Rent Oct')
        self.assertRedirects(r, reverse('acc:journal_detail', args=[e.pk]))
        self.assertEqual(e.status, 'posted')
        r = self.client.post(reverse('acc:journal_new'), {
            'date': '2026-10-10', 'narration': 'Bad', 'action': 'post',
            'account_0': acc('5200').pk, 'debit_0': '10', 'account_1': acc('1120').pk, 'credit_1': '9'})
        self.assertContains(r, 'must equal')
        self.assertFalse(JournalEntry.objects.filter(narration='Bad').exists())
        self.client.post(reverse('acc:journal_reverse', args=[e.pk]), {'date': '2026-10-11', 'reason': ''})
        e.refresh_from_db()
        self.assertEqual(e.status, 'posted')
        self.client.post(reverse('acc:journal_reverse', args=[e.pk]), {'date': '2026-10-11', 'reason': 'duplicate'})
        e.refresh_from_db()
        self.assertEqual(e.status, 'reversed')
        r = self.client.post(reverse('acc:journal_delete', args=[e.pk]))
        self.assertTrue(JournalEntry.objects.filter(pk=e.pk).exists())

    def test_draft_post_and_delete_views(self):
        r = self.client.post(reverse('acc:journal_new'), {'date': '2026-10-10', 'narration': 'D', 'action': 'draft',
                             'account_0': acc('5200').pk, 'debit_0': '5', 'account_1': acc('1120').pk, 'credit_1': '5'})
        d = JournalEntry.objects.get(narration='D')
        self.assertEqual(d.status, 'draft')
        self.client.post(reverse('acc:journal_post', args=[d.pk]))
        d.refresh_from_db()
        self.assertEqual(d.status, 'posted')
        d2 = S.create_draft(D1, 'D2', lines('1'))
        self.client.post(reverse('acc:journal_delete', args=[d2.pk]))
        self.assertFalse(JournalEntry.objects.filter(pk=d2.pk).exists())

    def test_period_close_reopen_views(self):
        p = S.period_for(D1)
        self.client.post(reverse('acc:period_action', args=[p.pk, 'close']), {'reason': ''})
        p.refresh_from_db()
        self.assertTrue(p.is_open)
        self.client.post(reverse('acc:period_action', args=[p.pk, 'close']), {'reason': 'Filed'})
        p.refresh_from_db()
        self.assertFalse(p.is_open)
        self.client.post(reverse('acc:period_action', args=[p.pk, 'reopen']), {'reason': 'Fix'})
        p.refresh_from_db()
        self.assertTrue(p.is_open)
        self.client.post(reverse('acc:periods'), {'start_year': '2025'})
        self.assertEqual(FinancialPeriod.objects.filter(financial_year__start_year=2025).count(), 12)

    def test_account_and_mapping_edit(self):
        r = self.client.post(reverse('acc:account_new'), {'code': '5310', 'name': 'Cloud hosting', 'account_type': 'expense',
                             'parent': acc('5000').pk, 'active': 'on'})
        self.assertRedirects(r, reverse('acc:accounts'))
        new = acc('5310')
        r = self.client.post(reverse('acc:account_edit', args=[acc('1200').pk]), {'code': '1201', 'name': 'AR', 'account_type': 'asset',
                             'parent': acc('1000').pk, 'active': 'on'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(Account.objects.filter(code='1200').exists())
        m = AccountMapping.objects.get(key='expense.default')
        data = {f'm_{x.pk}': x.account_id or '' for x in AccountMapping.objects.all()}
        data[f'm_{m.pk}'] = new.pk
        self.client.post(reverse('acc:mappings'), data)
        m.refresh_from_db()
        self.assertEqual(m.account, new)
