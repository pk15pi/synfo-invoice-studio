import re
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail import get_connection as mail_connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from employee_management.models import Department, Employee

from .models import PasswordResetOTP, UserProfile
from .tests import make_user

User = get_user_model()
Role = UserProfile.Role


@override_settings(ACCESS_CONTROL_ENABLED=True)
class PasswordResetTests(TestCase):
    def setUp(self):
        mail.outbox.clear()
        self.department = Department.objects.create(name='Operations', code='OPS')
        self.employee = Employee.objects.create(
            employee_id='EMP-R1', first_name='Asha', last_name='Rao', department=self.department,
            designation='Analyst', date_of_joining=date(2024, 1, 1),
            work_email='asha.work@quantbud.test', personal_email='asha.rao@gmail.test')
        self.user = make_user('asha', Role.EMPLOYEE, email='asha.work@quantbud.test',
                              first_name='Asha')

    def _mail_patch(self):
        return patch(
            'access_control.password_reset._mail_connection',
            return_value=(mail_connection(
                'django.core.mail.backends.locmem.EmailBackend'), 'noreply@quantbud.test'))

    def _identify(self, identifier='asha'):
        return self.client.post(reverse('access_control:password_reset'), {'identifier': identifier})

    def _choose(self, channel=PasswordResetOTP.WORK):
        return self.client.post(reverse('access_control:password_reset_choose'), {'channel': channel})

    def _code_from_mail(self):
        return re.search(r'\b(\d{6})\b', mail.outbox[-1].body).group(1)

    def _start_flow(self, channel=PasswordResetOTP.WORK):
        self._identify()
        with self._mail_patch():
            self._choose(channel)
        return self._code_from_mail()

    def _submit_code(self, code, password='brand-new-pass-1'):
        return self.client.post(reverse('access_control:password_reset_verify'), {
            'code': code, 'password': password, 'password_confirm': password,
        })

    def test_login_page_links_to_password_reset(self):
        response = self.client.get(reverse('access_control:login'))
        self.assertContains(response, 'Forgot password?')
        self.assertContains(response, reverse('access_control:password_reset'))

    def test_identify_accepts_username_and_both_emails(self):
        for identifier in ('asha', 'asha.work@quantbud.test', 'asha.rao@gmail.test'):
            with self.subTest(identifier=identifier):
                self.client.get(reverse('access_control:password_reset'))
                response = self._identify(identifier)
                self.assertRedirects(response, reverse('access_control:password_reset_choose'),
                                     fetch_redirect_response=False)

    def test_choose_lists_masked_work_and_personal_emails(self):
        self._identify()
        response = self.client.get(reverse('access_control:password_reset_choose'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Work email')
        self.assertContains(response, 'Personal email')
        self.assertContains(response, 'a***@quantbud.test')
        self.assertContains(response, 'a***@gmail.test')
        self.assertNotContains(response, 'asha.work@quantbud.test')

    def test_code_is_sent_only_to_the_selected_address(self):
        self._identify()
        with self._mail_patch():
            response = self._choose(PasswordResetOTP.PERSONAL)
        self.assertRedirects(response, reverse('access_control:password_reset_verify'),
                             fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['asha.rao@gmail.test'])
        self.assertIn('Your verification code is', mail.outbox[0].body)
        otp = PasswordResetOTP.objects.get()
        self.assertEqual(otp.channel, PasswordResetOTP.PERSONAL)
        self.assertEqual(otp.email, 'asha.rao@gmail.test')

    def test_only_available_channel_is_offered(self):
        self.employee.personal_email = ''
        self.employee.save(update_fields=['personal_email'])
        self._identify()
        response = self.client.get(reverse('access_control:password_reset_choose'))
        self.assertContains(response, 'Work email')
        self.assertNotContains(response, 'Personal email')

    def test_unknown_unlinked_inactive_and_left_employees_are_rejected(self):
        make_user('plain', Role.EMPLOYEE)
        left_employee = Employee.objects.create(
            employee_id='EMP-R2', first_name='Left', last_name='Person', department=self.department,
            designation='Analyst', date_of_joining=date(2020, 1, 1), status=Employee.LEFT,
            work_email='left@quantbud.test')
        make_user('left-person', Role.EMPLOYEE, email=left_employee.work_email)
        inactive = make_user('inactive', Role.EMPLOYEE, email='inactive@quantbud.test')
        Employee.objects.create(
            employee_id='EMP-R3', first_name='Inactive', last_name='Person',
            department=self.department, designation='Analyst', date_of_joining=date(2020, 1, 1),
            work_email=inactive.email)
        inactive.is_active = False
        inactive.save(update_fields=['is_active'])
        for identifier in ('nobody', 'plain', 'left-person', 'inactive'):
            with self.subTest(identifier=identifier):
                self.client.get(reverse('access_control:password_reset'))
                response = self._identify(identifier)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'No active employee record matches')
                self.assertFalse(PasswordResetOTP.objects.exists())

    def test_full_reset_flow_changes_password_and_consumes_code(self):
        code = self._start_flow()
        response = self.client.get(reverse('access_control:password_reset_verify'))
        self.assertContains(response, 'a***@quantbud.test')
        response = self._submit_code(code)
        self.assertRedirects(response, reverse('access_control:login'), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('brand-new-pass-1'))
        self.assertIsNotNone(PasswordResetOTP.objects.get().used_at)
        login = self.client.post(reverse('access_control:login'),
                                 {'username': 'asha', 'password': 'brand-new-pass-1'})
        self.assertEqual(login.status_code, 302)

    def test_code_cannot_be_reused(self):
        code = self._start_flow()
        self._submit_code(code)
        response = self._submit_code(code, password='another-pass-2')
        self.assertRedirects(response, reverse('access_control:password_reset'),
                             fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('brand-new-pass-1'))

    def test_wrong_code_locks_after_five_attempts(self):
        code = self._start_flow()
        wrong = '000000' if code != '000000' else '111111'
        for attempt in range(1, 5):
            response = self._submit_code(wrong)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(PasswordResetOTP.objects.get().attempts, attempt)
        response = self._submit_code(wrong)
        self.assertRedirects(response, reverse('access_control:password_reset'),
                             fetch_redirect_response=False)
        otp = PasswordResetOTP.objects.get()
        self.assertIsNotNone(otp.used_at)
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password('brand-new-pass-1'))

    def test_expired_code_is_rejected(self):
        code = self._start_flow()
        PasswordResetOTP.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        response = self._submit_code(code)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'expired')
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password('brand-new-pass-1'))

    def test_short_password_keeps_code_usable(self):
        code = self._start_flow()
        response = self._submit_code(code, password='short')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '8 characters')
        self.assertIsNone(PasswordResetOTP.objects.get().used_at)
        response = self._submit_code(code, password='long-enough-pass')
        self.assertRedirects(response, reverse('access_control:login'), fetch_redirect_response=False)

    def test_verify_requires_step_one(self):
        response = self.client.get(reverse('access_control:password_reset_verify'))
        self.assertRedirects(response, reverse('access_control:password_reset'),
                             fetch_redirect_response=False)
        response = self.client.get(reverse('access_control:password_reset_choose'))
        self.assertRedirects(response, reverse('access_control:password_reset'),
                             fetch_redirect_response=False)

    def test_resend_respects_cooldown_then_sends_again(self):
        first_code = self._start_flow()
        with self._mail_patch():
            response = self.client.post(reverse('access_control:password_reset_verify'),
                                        {'action': 'resend'})
        self.assertRedirects(response, reverse('access_control:password_reset_verify'),
                             fetch_redirect_response=False)
        self.assertEqual(PasswordResetOTP.objects.count(), 1)
        PasswordResetOTP.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        with self._mail_patch():
            self.client.post(reverse('access_control:password_reset_verify'), {'action': 'resend'})
        self.assertEqual(PasswordResetOTP.objects.count(), 2)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[-1].to, ['asha.work@quantbud.test'])
        new_code = self._code_from_mail()
        self.assertNotEqual(new_code, first_code)
        self.assertEqual(PasswordResetOTP.objects.filter(used_at__isnull=True).count(), 1)

    def test_email_failure_is_reported_and_no_code_is_stored(self):
        self._identify()
        with patch('access_control.password_reset._mail_connection',
                   side_effect=RuntimeError('SMTP unavailable')):
            response = self._choose(PasswordResetOTP.WORK)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'could not be emailed')
        self.assertFalse(PasswordResetOTP.objects.exists())
