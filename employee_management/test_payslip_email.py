"""Email salary slips page, SMTP settings, send log, bulk send."""
import json
import tempfile
from datetime import date
from pathlib import Path
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from employee_management import email_config as EC
from employee_management import payroll as engine
from employee_management import payslip_email as PE
from employee_management.models import EmployeePayroll, PayslipEmailLog, PayrollPeriod
from employee_management.tests import Base, TMP, D
from reporting import periods as report_periods


class QuarterMappingTests(TestCase):
    def test_fy_quarters_and_custom_range(self):
        q1 = report_periods.resolve_standard({
            'period_mode': 'financial', 'fy': '2026', 'period_type': 'quarterly', 'period_value': 'Q1',
        })
        self.assertEqual(q1.months(), [(2026, 4), (2026, 5), (2026, 6)])
        q4 = report_periods.resolve_standard({
            'period_mode': 'financial', 'fy': '2026', 'period_type': 'quarterly', 'period_value': 'Q4',
        })
        self.assertEqual(q4.months(), [(2027, 1), (2027, 2), (2027, 3)])
        custom = report_periods.resolve_standard({
            'period_mode': 'custom', 'date_from': '2026-05-01', 'date_to': '2026-05-31',
        })
        self.assertEqual(custom.months(), [(2026, 5)])
        year = report_periods.resolve_standard({
            'period_mode': 'calendar', 'calendar_year': '2026', 'calendar_month': '11',
        })
        self.assertEqual(year.months(), [(2026, 11)])


class PreviousMonthDefaultTests(TestCase):
    def test_previous_month_params(self):
        self.assertEqual(
            PE.previous_month_params(date(2026, 11, 9)),
            {'period_mode': 'calendar', 'calendar_year': '2026', 'calendar_month': '10'},
        )
        self.assertEqual(
            PE.previous_month_params(date(2026, 1, 1)),
            {'period_mode': 'calendar', 'calendar_year': '2025', 'calendar_month': '12'},
        )


@override_settings(EMPLOYEE_PRIVATE_ROOT=TMP, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PayslipEmailPageTests(Base):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / 'email.json'
        self.env = mock.patch.dict(
            'os.environ',
            {'QB_EMAIL_CONFIG_FILE': str(self.cfg), 'QB_EMAIL_PASSWORD': ''},
            clear=False,
        )
        self.env.start()
        EC.save_config({
            'host': 'smtp.office365.com', 'port': 587, 'user': 'support@synfowebs.com',
            'from_email': 'support@synfowebs.com', 'password': 'test-secret-pw',
            'use_tls': True, 'use_ssl': False, 'send_gap_seconds': 30,
        })

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
        super().tearDown()

    def _slip(self, year=2026, month=4, email='asha@example.com', pan='ABCDE1234F', emp=None):
        emp = emp or self.emp
        emp.work_email = email
        emp.pan_number = pan
        emp.save()
        if not emp.salary_structures.exists():
            self.structure(emp=emp)
        period, _ = PayrollPeriod.objects.get_or_create(year=year, month=month, defaults={'divisor': 'actual'})
        engine.generate(period)
        slip = EmployeePayroll.objects.get(period=period, employee=emp, is_current=True)
        if slip.status != EmployeePayroll.FINALIZED:
            engine.finalize([slip])
            slip.refresh_from_db()
        return slip

    def test_default_redirects_to_previous_month(self):
        with mock.patch('employee_management.payslip_email.timezone.localdate', return_value=date(2026, 11, 9)):
            r = self.client.get(reverse('em:payslip_email'))
        self.assertEqual(r.status_code, 302)
        self.assertIn('period_mode=calendar', r['Location'])
        self.assertIn('calendar_year=2026', r['Location'])
        self.assertIn('calendar_month=10', r['Location'])

    def test_explicit_period_params_kept(self):
        self._slip(2026, 4)
        r = self.client.get(reverse('em:payslip_email'), {
            'period_mode': 'financial', 'fy': '2026', 'period_type': 'quarterly', 'period_value': 'Q1',
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'April 2026')
        self.assertContains(r, 'Q1')

    def test_page_renders_filters_buttons_and_rows(self):
        self._slip(2026, 4)
        self._slip(2026, 5)
        r = self.client.get(reverse('em:payslip_email'), {
            'period_mode': 'financial', 'fy': '2026', 'period_type': 'quarterly', 'period_value': 'Q1',
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Filter by')
        self.assertContains(r, 'Financial year')
        self.assertContains(r, 'Resend')
        self.assertContains(r, 'Send to all')
        self.assertContains(r, 'Bulk send')
        self.assertContains(r, 'data-gap="30"')
        self.assertContains(r, 'data-ids="')
        self.assertContains(r, reverse('em:payslip_email_send_json', args=[0]).replace('/0/', '/__ID__/').split('__ID__')[0])
        self.assertContains(r, self.emp.employee_id)
        self.assertContains(r, 'Not sent')
        self.assertNotContains(r, 'ABCDE1234F')
        self.assertContains(r, 'page_size')

    def test_period_filters_slips(self):
        self._slip(2026, 4)
        self._slip(2026, 8)
        r = self.client.get(reverse('em:payslip_email'), {
            'period_mode': 'financial', 'fy': '2026', 'period_type': 'quarterly', 'period_value': 'Q1',
        })
        self.assertContains(r, 'April 2026')
        self.assertNotContains(r, 'August 2026')

    def test_send_success_updates_status_and_allows_resend(self):
        slip = self._slip(2026, 4)
        mail.outbox.clear()
        next_url = reverse('em:payslip_email') + '?period_mode=financial&fy=2026&period_type=monthly&period_value=4'
        r = self.client.post(reverse('em:payslip_email_send', args=[slip.pk]), {'next': next_url})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(PayslipEmailLog.objects.filter(payroll=slip, success=True).exists())
        page = self.client.get(next_url)
        self.assertContains(page, 'Sent')
        self.assertContains(page, 'Resend')
        mail.outbox.clear()
        self.client.post(reverse('em:payslip_email_send', args=[slip.pk]), {'next': next_url})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(PayslipEmailLog.objects.filter(payroll=slip, success=True).count(), 2)

    def test_failed_send_stays_not_sent_and_masks_password(self):
        slip = self._slip(2026, 4)
        with mock.patch('employee_management.payslip_email.EmailMessage.send', side_effect=RuntimeError('auth failed test-secret-pw')):
            self.client.post(reverse('em:payslip_email_send', args=[slip.pk]), {'next': reverse('em:payslip_email')})
        log = PayslipEmailLog.objects.get(payroll=slip)
        self.assertFalse(log.success)
        self.assertNotIn('test-secret-pw', log.error)
        self.assertIn('********', log.error)
        page = self.client.get(reverse('em:payslip_email'), {
            'period_mode': 'financial', 'fy': '2026', 'period_type': 'monthly', 'period_value': '4',
        })
        self.assertContains(page, 'Not sent')

    def test_missing_email_does_not_send(self):
        slip = self._slip(2026, 4, email='')
        self.emp.work_email = ''
        self.emp.personal_email = ''
        self.emp.save()
        mail.outbox.clear()
        self.client.post(reverse('em:payslip_email_send', args=[slip.pk]), {'next': reverse('em:payslip_email')})
        self.assertEqual(len(mail.outbox), 0)
        log = PayslipEmailLog.objects.get(payroll=slip)
        self.assertFalse(log.success)

    def test_settings_save_gap_and_hides_password(self):
        self.assertEqual(EC.public_config()['send_gap_seconds'], 30)
        r = self.client.post(reverse('em:settings'), {
            'save_email': '1',
            'host': 'smtp.office365.com', 'port': '587',
            'user': 'support@synfowebs.com', 'from_email': 'support@synfowebs.com',
            'password': 'new-secret-value', 'use_tls': 'on', 'send_gap_seconds': '45',
        })
        self.assertEqual(r.status_code, 302)
        get = self.client.get(reverse('em:settings'))
        self.assertNotContains(get, 'new-secret-value')
        self.assertContains(get, 'Password saved')
        self.assertContains(get, 'Gap between salary-slip emails')
        cfg = EC.load_config()
        self.assertEqual(cfg['password'], 'new-secret-value')
        self.assertEqual(EC.public_config()['send_gap_seconds'], 45)
        # unset defaults to 30
        EC.save_config({'send_gap_seconds': '', 'password': ''})
        # empty string in file -> default
        stored = json.loads(Path(self.cfg).read_text(encoding='utf-8'))
        stored['send_gap_seconds'] = ''
        Path(self.cfg).write_text(json.dumps(stored), encoding='utf-8')
        self.assertEqual(EC.public_config()['send_gap_seconds'], 30)

    def test_bulk_queue_skips_sent_and_missing_email_across_full_set(self):
        a = self._slip(2026, 10, email='a@example.com')
        other = self.make_emp(first_name='Bina')
        b = self._slip(2026, 10, email='b@example.com', emp=other)
        no_mail = self.make_emp(first_name='Cora')
        c = self._slip(2026, 10, email='', emp=no_mail)
        no_mail.work_email = ''
        no_mail.personal_email = ''
        no_mail.save()
        PE.send_payslip(a)
        period = report_periods.resolve_standard({
            'period_mode': 'calendar', 'calendar_year': '2026', 'calendar_month': '10',
        })
        queue = PE.bulk_queue(list(PE.slips_for_period(period)))
        self.assertEqual(queue['skipped_already_sent'], 1)
        self.assertEqual(queue['skipped_no_email'], 1)
        self.assertEqual(queue['pending_ids'], [b.pk])
        page = self.client.get(reverse('em:payslip_email'), {
            'period_mode': 'calendar', 'calendar_year': '2026', 'calendar_month': '10', 'page_size': '5',
        })
        self.assertContains(page, f'data-ids="{b.pk}"')
        self.assertContains(page, 'data-gap="30"')
        # Send to all uses gap; Bulk send documented as 0 via separate button (no data-gap override)
        self.assertContains(page, 'id="qb-send-to-all"')
        self.assertContains(page, 'id="qb-bulk-send"')
        self.assertIn('run(gapSec * 1000', page.content.decode())
        self.assertIn('run(0, \'Bulk send\')', page.content.decode())

    def test_json_send_skip_if_sent_and_failure_not_marked(self):
        slip = self._slip(2026, 4)
        mail.outbox.clear()
        r = self.client.post(reverse('em:payslip_email_send_json', args=[slip.pk]), {'skip_if_sent': '1'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'sent')
        self.assertEqual(len(mail.outbox), 1)
        mail.outbox.clear()
        r2 = self.client.post(reverse('em:payslip_email_send_json', args=[slip.pk]), {'skip_if_sent': '1'})
        self.assertEqual(r2.json()['status'], 'skipped')
        self.assertEqual(r2.json()['reason'], 'already_sent')
        self.assertEqual(len(mail.outbox), 0)
        with mock.patch('employee_management.payslip_email.EmailMessage.send', side_effect=RuntimeError('boom')):
            other = self.make_emp(first_name='Dee')
            slip2 = self._slip(2026, 5, email='dee@example.com', emp=other)
            mail.outbox.clear()
            r3 = self.client.post(reverse('em:payslip_email_send_json', args=[slip2.pk]), {'skip_if_sent': '1'})
        self.assertEqual(r3.json()['status'], 'failed')
        self.assertFalse(PayslipEmailLog.objects.filter(payroll=slip2, success=True).exists())
