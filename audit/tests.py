from datetime import date

from django.test import TestCase
from django.urls import reverse

from accounting import services as AS
from accounting.models import Account, FinancialPeriod
from employee_management.models import Department, Employee, EmployeeBankAccount

from .context import audit_context
from .masking import last4, mask_value, scrub_text
from .models import OPERATOR_TEXT, AppendOnlyError, AuditEvent
from .recorder import log_manual

D1 = date(2026, 10, 15)


def ev(model, **kw):
    return AuditEvent.objects.filter(model=model, **kw)


def lines(amount='1000', dr='1120', cr='4100'):
    return [{'account': Account.objects.get(code=dr), 'debit': amount},
            {'account': Account.objects.get(code=cr), 'credit': amount}]


class AuditEventTests(TestCase):
    def test_create_update_on_account(self):
        a = Account.objects.get(code='1120')
        a.name = 'Renamed for audit'
        a.save()
        e = ev('Account', object_id=str(a.pk), action='update').get()
        self.assertEqual(e.changes['name'][1], 'Renamed for audit')
        self.assertNotEqual(e.changes['name'][0], 'Renamed for audit')
        self.assertEqual(len(e.changes), 1)
        a.save()  # no change -> no event
        self.assertEqual(ev('Account', object_id=str(a.pk)).count(), 1)

    def test_post_and_reverse(self):
        with audit_context(source='ui', path='/accounting/new/', reason=''):
            e = AS.create_and_post(D1, 'Sale', lines('100'))
        self.assertTrue(ev('JournalEntry', object_id=str(e.pk), action='create').exists())
        p = ev('JournalEntry', object_id=str(e.pk), action='post').get()
        self.assertEqual(p.changes['status'], ['draft', 'posted'])
        self.assertEqual((p.source, p.request_path), ('ui', '/accounting/new/'))
        rev = AS.reverse(e, 'Wrong customer')
        r = ev('JournalEntry', object_id=str(rev.pk), action='reverse').get()
        self.assertEqual(r.reason, 'Wrong customer')

    def test_period_close_reopen(self):
        AS.ensure_financial_year(2026)
        p = FinancialPeriod.objects.get(year=2026, month=10)
        AS.close_period(p, 'Month done')
        AS.reopen_period(p, 'Late bill')
        c = ev('FinancialPeriod', object_id=str(p.pk), action='close').get()
        r = ev('FinancialPeriod', object_id=str(p.pk), action='reopen').get()
        self.assertEqual((c.reason, r.reason), ('Month done', 'Late bill'))

    def test_middleware_captures_path_and_reason(self):
        AS.ensure_financial_year(2026)
        p = FinancialPeriod.objects.get(year=2026, month=10)
        from audit.context import current
        seen = {}
        from django.test import RequestFactory
        from .middleware import AuditContextMiddleware
        mw = AuditContextMiddleware(lambda r: seen.update(current()) or __import__('django.http').http.HttpResponse())
        mw(RequestFactory().post('/accounting/periods/1/close/', {'reason': 'From the form'}))
        self.assertEqual(seen, {'path': '/accounting/periods/1/close/', 'reason': 'From the form', 'source': 'ui'})
        self.assertEqual(current()['source'] in ('system', 'command'), True)
        self.assertTrue(p)


class MaskingTests(TestCase):
    def test_bank_account_and_ids_masked(self):
        dept = Department.objects.create(name='Eng', code='ENG')
        emp = Employee.objects.create(first_name='Asha', last_name='R', department=dept, designation='Dev',
                                      date_of_joining=date(2024, 1, 1))
        b = EmployeeBankAccount.objects.create(employee=emp, bank_name='HDFC', account_holder_name='Asha R',
                                               account_number='50100012345678', ifsc_code='HDFC0001234')
        e = ev('EmployeeBankAccount', object_id=str(b.pk)).get()
        self.assertEqual(e.changes['account_number'][1], 'XXXXXXXXXX5678')
        self.assertNotIn('50100012345678', str(e.changes) + e.object_repr)
        self.assertEqual(mask_value('aadhaar_number', '1234 5678 9012'), 'XXXXXXXX9012')
        self.assertEqual(mask_value('pan_number', 'ABCDE1234F'), 'XXXXXX234F')
        self.assertEqual(last4('12'), 'XXXX12')
        self.assertEqual(scrub_text('Acct 50100012345678 PAN ABCDE1234F'), 'Acct XXXXXXXXXX5678 PAN XXXXXX234F')

    def test_passwords_never_logged(self):
        e = log_manual('x', 'Y', 1, 'obj', 'update', changes={'password': ['a', 'b'], 'api_secret': ['c', 'd'],
                                                             'name': ['o', 'n']})
        self.assertEqual(list(e.changes), ['name'])


class AppendOnlyTests(TestCase):
    def test_update_and_delete_blocked(self):
        e = log_manual('x', 'Y', 1, 'obj', 'create')
        e.reason = 'tamper'
        with self.assertRaises(AppendOnlyError):
            e.save()
        with self.assertRaises(AppendOnlyError):
            e.delete()
        with self.assertRaises(AppendOnlyError):
            AuditEvent.objects.filter(pk=e.pk).update(reason='x')
        with self.assertRaises(AppendOnlyError):
            AuditEvent.objects.all().delete()
        self.assertEqual(AuditEvent.objects.get(pk=e.pk).reason, '')


class UiTests(TestCase):
    def test_operator_text_list_history_csv(self):
        a = Account.objects.get(code='1120')
        a.name = 'X1'
        a.save()
        r = self.client.get(reverse('audit:log'), {'model': 'Account'})
        self.assertContains(r, OPERATOR_TEXT)
        self.assertContains(r, 'X1')
        self.assertEqual(AuditEvent.objects.first().operator, OPERATOR_TEXT)
        h = self.client.get(reverse('audit:history', args=['accounting', 'Account', a.pk]))
        self.assertContains(h, 'X1')
        c = self.client.get(reverse('audit:export'), {'model': 'Account'})
        body = c.content.decode()
        self.assertIn('attachment', c['Content-Disposition'])
        self.assertIn(OPERATOR_TEXT, body)
        self.assertIn('X1', body)
        self.assertEqual(self.client.get(reverse('audit:log'), REMOTE_ADDR='10.0.0.5').status_code, 403)
