import re
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail import get_connection as mail_connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from attendance.models import LeaveApplication, LeaveType
from employee_management.models import Department, Employee, EmployeePayroll, PayrollPeriod

from . import password_reset as reset
from .models import PasswordResetOTP, RoleDefinition, UserProfile
from .permissions import (HR, ACCOUNTING, BANK, READ_ONLY_ROLES, SELF_SERVICE, USERS,
                          module_for_path, role_for)

User = get_user_model()
Role = UserProfile.Role
PASSWORD = 'test-pass-123'


def make_user(username, role=Role.HR, **extra):
    user = User.objects.create_user(username, password=PASSWORD, **extra)
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.role = role
    profile.save()
    return user


@override_settings(ACCESS_CONTROL_ENABLED=True)
class LoginTests(TestCase):
    def test_anonymous_redirected_to_login_with_next(self):
        response = self.client.get(reverse('home'))
        self.assertRedirects(response, '/login/?next=/', fetch_redirect_response=False)

    def test_login_page_is_public(self):
        response = self.client.get(reverse('access_control:login'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sign in')

    def test_business_logo_is_public_for_login_branding(self):
        response = self.client.get(reverse('business_logo'))
        self.assertNotEqual(response.status_code, 302)

    def test_anonymous_user_management_redirects_to_login(self):
        response = self.client.get(reverse('access_control:users'))
        self.assertRedirects(response, '/login/?next=/users/', fetch_redirect_response=False)

    def test_login_then_logout(self):
        make_user('hr-login')
        response = self.client.post(reverse('access_control:login'), {
            'username': 'hr-login', 'password': PASSWORD,
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request['PATH_INFO'], '/')
        self.assertContains(response, 'HR')
        response = self.client.post(reverse('access_control:logout'))
        self.assertRedirects(response, '/login/', fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_invalid_password_stays_on_login_page(self):
        make_user('hr-bad')
        response = self.client.post(reverse('access_control:login'), {
            'username': 'hr-bad', 'password': 'wrong-password',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Username or password is incorrect.')

    def test_inactive_user_cannot_sign_in(self):
        make_user('hr-inactive', is_active=False)
        response = self.client.post(reverse('access_control:login'), {
            'username': 'hr-inactive', 'password': PASSWORD,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'inactive')

    def test_authenticated_user_is_redirected_away_from_login(self):
        user = make_user('hr-start')
        self.client.force_login(user)
        response = self.client.get(reverse('access_control:login'))
        self.assertRedirects(response, '/', fetch_redirect_response=False)


@override_settings(ACCESS_CONTROL_ENABLED=True)
class RoleAccessTests(TestCase):
    def test_hr_opens_hr_but_not_accounting(self):
        self.client.force_login(make_user('hr-1', Role.HR))
        self.assertEqual(self.client.get(reverse('em:home')).status_code, 200)
        self.assertEqual(self.client.get(reverse('acc:home')).status_code, 403)

    def test_accountant_opens_accounting_but_not_hr(self):
        self.client.force_login(make_user('acc-1', Role.ACCOUNTANT))
        self.assertEqual(self.client.get(reverse('acc:home')).status_code, 200)
        self.assertEqual(self.client.get(reverse('em:home')).status_code, 403)

    def test_recruiter_sees_recruitment_not_invoices(self):
        self.client.force_login(make_user('rec-1', Role.RECRUITER))
        self.assertEqual(self.client.get(reverse('recruitment_dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('invoice_list')).status_code, 403)

    def test_project_manager_sees_invoicing_and_recruitment(self):
        self.client.force_login(make_user('pm-1', Role.PROJECT_MANAGER))
        self.assertEqual(self.client.get(reverse('invoice_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('recruitment_dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('acc:home')).status_code, 403)

    def test_director_sees_every_module(self):
        self.client.force_login(make_user('dir-1', Role.DIRECTOR))
        for name in ('em:home', 'acc:home', 'invoice_list', 'att:daily', 'exp:dashboard',
                     'document_generation:dashboard', 'audit:log', 'backup:home'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_user_without_role_sees_only_shared_pages(self):
        self.client.force_login(make_user('norole', ''))
        self.assertEqual(self.client.get(reverse('home')).status_code, 200)
        self.assertEqual(self.client.get(reverse('profile_edit')).status_code, 200)
        self.assertEqual(self.client.get(reverse('em:home')).status_code, 403)

    def test_superuser_is_admin_without_profile(self):
        user = User.objects.create_superuser('root-1', 'root@example.com', PASSWORD)
        UserProfile.objects.filter(user=user).delete()
        self.assertEqual(role_for(user), Role.ADMIN)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('acc:home')).status_code, 200)
        self.assertEqual(self.client.get(reverse('access_control:users')).status_code, 200)

    def test_auditor_read_only(self):
        self.client.force_login(make_user('auditor-1', Role.AUDITOR))
        self.assertEqual(self.client.get(reverse('reporting:dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('audit:log')).status_code, 200)
        self.assertEqual(self.client.post(reverse('profile_edit'), {}).status_code, 403)
        self.assertEqual(self.client.get(reverse('em:home')).status_code, 403)


@override_settings(ACCESS_CONTROL_ENABLED=True)
class NewRoleAccessTests(TestCase):
    def assert_module_access(self, username, role, allowed, denied):
        self.client.force_login(make_user(username, role))
        for name in allowed:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        for name in denied:
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_it_admin_manages_accounts_not_business_modules(self):
        self.assert_module_access(
            'it-1', Role.IT_ADMIN,
            ('access_control:users', 'backup:home', 'audit:log'),
            ('em:home', 'acc:home', 'invoice_list'))

    def test_payroll_executive_opens_payroll_not_invoices(self):
        self.assert_module_access(
            'payroll-exec', Role.PAYROLL_EXECUTIVE,
            ('em:payroll', 'att:daily', 'document_generation:dashboard'),
            ('invoice_list', 'acc:home'))

    def test_billing_opens_receivables_not_hr(self):
        self.assert_module_access(
            'billing-1', Role.BILLING,
            ('invoice_list', 'receipts', 'bank:accounts', 'reporting:dashboard'),
            ('em:home', 'acc:home'))

    def test_compliance_officer_opens_compliance_not_payroll(self):
        self.assert_module_access(
            'compliance-1', Role.COMPLIANCE_OFFICER,
            ('compliance:home', 'reporting:dashboard', 'audit:log'),
            ('em:payroll', 'invoice_list'))

    def test_document_controller_opens_documents_only(self):
        self.assert_module_access(
            'doc-ctrl', Role.DOCUMENT_CONTROLLER,
            ('document_generation:dashboard',),
            ('invoice_list', 'em:home', 'acc:home'))

    def test_asset_manager_opens_assets_and_expenses(self):
        self.assert_module_access(
            'asset-mgr', Role.ASSET_MANAGER,
            ('assets:home', 'exp:dashboard', 'reporting:dashboard'),
            ('em:payroll', 'invoice_list'))

    def test_management_viewer_is_read_only(self):
        self.client.force_login(make_user('exec-1', Role.MANAGEMENT))
        self.assertEqual(self.client.get(reverse('reporting:dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('financial_reporting:dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('em:home')).status_code, 403)
        self.assertEqual(self.client.post(reverse('profile_edit'), {}).status_code, 403)

    def test_employee_role_sees_only_self_service_and_shared(self):
        self.client.force_login(make_user('employee-1', Role.EMPLOYEE, email='employee-1@example.com'))
        self.assertEqual(self.client.get(reverse('access_control:me')).status_code, 200)
        self.assertEqual(self.client.get(reverse('profile_edit')).status_code, 200)
        self.assertEqual(self.client.get(reverse('em:home')).status_code, 403)
        self.assertEqual(self.client.post(reverse('access_control:me')).status_code, 403)


class ModuleMappingTests(TestCase):
    def test_bank_details_is_hr_while_bank_accounts_is_bank(self):
        self.assertEqual(module_for_path('/bank-details/'), HR)
        self.assertEqual(module_for_path('/bank/accounts/'), BANK)
        self.assertEqual(module_for_path('/accounting/'), ACCOUNTING)

    def test_home_is_shared(self):
        self.assertEqual(module_for_path('/'), 'shared')

    def test_self_service_paths_map_to_my_workspace(self):
        self.assertEqual(module_for_path('/me/'), SELF_SERVICE)
        self.assertEqual(module_for_path('/me/payslips/'), SELF_SERVICE)

    def test_role_management_paths_map_to_users_module(self):
        self.assertEqual(module_for_path('/roles/'), USERS)
        self.assertEqual(module_for_path('/roles/new/'), USERS)

    def test_read_only_roles_are_viewers_only(self):
        self.assertEqual(READ_ONLY_ROLES, frozenset({Role.AUDITOR, Role.MANAGEMENT, Role.EMPLOYEE}))

    def test_middleware_is_present(self):
        from django.conf import settings
        self.assertIn('access_control.middleware.AccessControlMiddleware', settings.MIDDLEWARE)


class UserManagementTests(TestCase):
    def setUp(self):
        self.admin = make_user('manager', Role.ADMIN)

    def test_hr_cannot_open_user_management(self):
        self.client.force_login(make_user('hr-denied', Role.HR))
        self.assertEqual(self.client.get(reverse('access_control:users')).status_code, 403)
        self.assertEqual(self.client.get(reverse('access_control:user_create')).status_code, 403)

    def test_admin_can_list_users(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('access_control:users'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'manager')
        self.assertContains(response, 'Admin')

    def test_create_user_assigns_role_and_password(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('access_control:user_create'), {
            'username': 'new-hr', 'first_name': 'New', 'last_name': 'Hire', 'email': 'new@example.com',
            'role': Role.HR, 'is_active': 'on',
            'password': 'new-password-1', 'password_confirm': 'new-password-1',
        })
        self.assertRedirects(response, reverse('access_control:users'), fetch_redirect_response=False)
        user = User.objects.get(username='new-hr')
        self.assertEqual(user.access_profile.role, Role.HR)
        self.assertTrue(user.check_password('new-password-1'))
        self.assertTrue(user.is_active)

    def test_edit_user_changes_role(self):
        target = make_user('change-me', Role.RECRUITER)
        self.client.force_login(self.admin)
        response = self.client.post(reverse('access_control:user_edit', args=[target.pk]), {
            'username': 'change-me', 'first_name': '', 'last_name': '', 'email': '',
            'role': Role.ACCOUNTANT, 'is_active': 'on',
            'new_password': '', 'new_password_confirm': '',
        })
        self.assertRedirects(response, reverse('access_control:users'), fetch_redirect_response=False)
        target.refresh_from_db()
        self.assertEqual(target.access_profile.role, Role.ACCOUNTANT)

    def test_cannot_deactivate_own_account(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('access_control:user_edit', args=[self.admin.pk]), {
            'username': 'manager', 'first_name': '', 'last_name': '', 'email': '',
            'role': Role.ADMIN, 'new_password': '', 'new_password_confirm': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'cannot deactivate your own account')
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_cannot_remove_last_admin(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('access_control:user_edit', args=[self.admin.pk]), {
            'username': 'manager', 'first_name': '', 'last_name': '', 'email': '',
            'role': Role.HR, 'is_active': 'on', 'new_password': '', 'new_password_confirm': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'At least one active Admin must remain.')
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.access_profile.role, Role.ADMIN)

    def test_it_admin_can_open_user_management(self):
        self.client.force_login(make_user('it-manager', Role.IT_ADMIN))
        self.assertEqual(self.client.get(reverse('access_control:users')).status_code, 200)
        self.assertEqual(self.client.get(reverse('access_control:user_create')).status_code, 200)

    def test_it_admin_cannot_edit_admin_director_or_superuser(self):
        self.client.force_login(make_user('it-manager-2', Role.IT_ADMIN))
        peer_admin = make_user('peer-admin', Role.ADMIN)
        director = make_user('peer-director', Role.DIRECTOR)
        root = User.objects.create_superuser('root-2', 'root2@example.com', PASSWORD)
        for target in (peer_admin, director, root):
            self.assertEqual(
                self.client.get(reverse('access_control:user_edit', args=[target.pk])).status_code,
                403, target.username)

    def test_it_admin_cannot_grant_senior_roles(self):
        self.client.force_login(make_user('it-manager-3', Role.IT_ADMIN))
        response = self.client.post(reverse('access_control:user_create'), {
            'username': 'new-admin', 'first_name': '', 'last_name': '', 'email': '',
            'role': Role.ADMIN, 'is_active': 'on',
            'password': 'new-password-1', 'password_confirm': 'new-password-1',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='new-admin').exists())

    def test_it_admin_can_create_standard_user(self):
        self.client.force_login(make_user('it-manager-4', Role.IT_ADMIN))
        response = self.client.post(reverse('access_control:user_create'), {
            'username': 'new-billing', 'first_name': '', 'last_name': '', 'email': '',
            'role': Role.BILLING, 'is_active': 'on',
            'password': 'new-password-1', 'password_confirm': 'new-password-1',
        })
        self.assertRedirects(response, reverse('access_control:users'), fetch_redirect_response=False)
        self.assertEqual(User.objects.get(username='new-billing').access_profile.role, Role.BILLING)


@override_settings(ACCESS_CONTROL_ENABLED=True)
class SelfServiceTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name='Operations', code='OPS')
        self.employee = Employee.objects.create(
            employee_id='EMP-1', first_name='Asha', last_name='Rao', department=self.department,
            designation='Analyst', date_of_joining=date(2024, 1, 1), work_email='asha@example.com')
        self.other = Employee.objects.create(
            employee_id='EMP-2', first_name='Ravi', last_name='Kumar', department=self.department,
            designation='Analyst', date_of_joining=date(2024, 1, 1), work_email='ravi@example.com')
        self.user = make_user('asha', Role.EMPLOYEE, email='asha@example.com')
        self.client.force_login(self.user)

    def test_unlinked_account_gets_guidance(self):
        self.client.force_login(make_user('unlinked', Role.EMPLOYEE))
        response = self.client.get(reverse('access_control:me'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Employee record not linked')

    def test_workspace_shows_own_employee(self):
        response = self.client.get(reverse('access_control:me'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Asha Rao')
        self.assertNotContains(response, 'Ravi Kumar')

    def test_leave_page_lists_only_own_applications(self):
        leave_type, _ = LeaveType.objects.get_or_create(code='CL', defaults={'name': 'Casual Leave'})
        LeaveApplication.objects.create(
            employee=self.employee, leave_type=leave_type, start_date=date(2026, 10, 5),
            end_date=date(2026, 10, 5), days=1, reason='Own reason')
        LeaveApplication.objects.create(
            employee=self.other, leave_type=leave_type, start_date=date(2026, 10, 6),
            end_date=date(2026, 10, 6), days=1, reason='Other reason')
        response = self.client.get(reverse('access_control:me_leave'))
        self.assertContains(response, 'Own reason')
        self.assertNotContains(response, 'Other reason')

    def test_payslip_list_and_download_are_owner_scoped(self):
        period = PayrollPeriod.objects.create(year=2026, month=10, status='finalized')
        own = EmployeePayroll.objects.create(
            period=period, employee=self.employee, status='finalized', finalized_at=timezone.now())
        other = EmployeePayroll.objects.create(
            period=period, employee=self.other, status='finalized', finalized_at=timezone.now())
        response = self.client.get(reverse('access_control:me_payslips'))
        self.assertContains(response, 'October 2026')
        with patch('access_control.views_self_service.payslip_for',
                   return_value=(b'%PDF-1.4 test', 'payslip.pdf')):
            self.assertEqual(
                self.client.get(reverse('access_control:me_payslip', args=[own.pk])).status_code, 200)
            self.assertEqual(
                self.client.get(reverse('access_control:me_payslip', args=[other.pk])).status_code, 404)

    def test_draft_payslips_are_hidden(self):
        period = PayrollPeriod.objects.create(year=2026, month=9, status='draft')
        draft = EmployeePayroll.objects.create(period=period, employee=self.employee)
        response = self.client.get(reverse('access_control:me_payslips'))
        self.assertNotContains(response, 'September 2026')
        self.assertEqual(
            self.client.get(reverse('access_control:me_payslip', args=[draft.pk])).status_code, 404)


@override_settings(ACCESS_CONTROL_ENABLED=True)
class CustomRoleTests(TestCase):
    def setUp(self):
        self.admin = make_user('role-admin', Role.ADMIN)

    def test_admin_creates_custom_role(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('access_control:role_create'), {
            'name': 'Audit Viewer', 'modules': ['reports', 'documents'], 'read_only': 'on',
        })
        self.assertRedirects(response, reverse('access_control:roles'), fetch_redirect_response=False)
        role = RoleDefinition.objects.get(name='Audit Viewer')
        self.assertEqual(role.code, 'audit_viewer')
        self.assertEqual(role.modules, ['reports', 'documents'])
        self.assertTrue(role.read_only)

    def test_custom_role_grants_its_modules(self):
        role = RoleDefinition.objects.create(
            code='audit_viewer', name='Audit Viewer', modules=['reports'])
        self.client.force_login(make_user('custom-reader', role.code))
        self.assertEqual(self.client.get(reverse('reporting:dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('home')).status_code, 200)
        self.assertEqual(self.client.get(reverse('em:home')).status_code, 403)

    def test_custom_read_only_role_blocks_changes(self):
        role = RoleDefinition.objects.create(
            code='report_reader', name='Report Reader', modules=['reports'], read_only=True)
        self.client.force_login(make_user('custom-viewer', role.code))
        self.assertEqual(self.client.get(reverse('reporting:dashboard')).status_code, 200)
        self.assertEqual(self.client.post(reverse('profile_edit'), {}).status_code, 403)

    def test_custom_role_appears_in_user_form(self):
        RoleDefinition.objects.create(code='audit_viewer', name='Audit Viewer', modules=['reports'])
        self.client.force_login(self.admin)
        response = self.client.get(reverse('access_control:user_create'))
        self.assertContains(response, 'Audit Viewer')
        self.assertContains(response, 'value="audit_viewer"')

    def test_only_admin_and_director_manage_roles(self):
        for username, role in (('roles-hr', Role.HR), ('roles-it', Role.IT_ADMIN)):
            with self.subTest(role=role):
                self.client.force_login(make_user(username, role))
                self.assertEqual(self.client.get(reverse('access_control:roles')).status_code, 403)
                self.assertEqual(self.client.get(reverse('access_control:role_create')).status_code, 403)

    def test_reserved_role_name_is_rejected(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('access_control:role_create'), {
            'name': 'Admin', 'modules': ['reports'],
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'reserved')
        self.assertFalse(RoleDefinition.objects.exists())

    def test_role_requires_at_least_one_module(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('access_control:role_create'), {'name': 'Empty Role'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'at least one module')
        self.assertFalse(RoleDefinition.objects.exists())

    def test_director_edits_custom_role(self):
        role = RoleDefinition.objects.create(
            code='audit_viewer', name='Audit Viewer', modules=['reports'])
        self.client.force_login(make_user('role-director', Role.DIRECTOR))
        response = self.client.post(reverse('access_control:role_edit', args=[role.pk]), {
            'name': 'Audit Viewer', 'modules': ['reports', 'documents'], 'read_only': 'on',
        })
        self.assertRedirects(response, reverse('access_control:roles'), fetch_redirect_response=False)
        role.refresh_from_db()
        self.assertEqual(role.modules, ['reports', 'documents'])
        self.assertTrue(role.read_only)

    def test_it_admin_can_assign_custom_role(self):
        RoleDefinition.objects.create(code='audit_viewer', name='Audit Viewer', modules=['reports'])
        self.client.force_login(make_user('roles-it-assign', Role.IT_ADMIN))
        response = self.client.post(reverse('access_control:user_create'), {
            'username': 'new-viewer', 'first_name': '', 'last_name': '', 'email': '',
            'role': 'audit_viewer', 'is_active': 'on',
            'password': 'new-password-1', 'password_confirm': 'new-password-1',
        })
        self.assertRedirects(response, reverse('access_control:users'), fetch_redirect_response=False)
        self.assertEqual(User.objects.get(username='new-viewer').access_profile.role, 'audit_viewer')
