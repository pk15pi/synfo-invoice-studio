from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import UserProfile
from .permissions import HR, ACCOUNTING, BANK, READ_ONLY_ROLES, module_for_path, role_for

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


class ModuleMappingTests(TestCase):
    def test_bank_details_is_hr_while_bank_accounts_is_bank(self):
        self.assertEqual(module_for_path('/bank-details/'), HR)
        self.assertEqual(module_for_path('/bank/accounts/'), BANK)
        self.assertEqual(module_for_path('/accounting/'), ACCOUNTING)

    def test_home_is_shared(self):
        self.assertEqual(module_for_path('/'), 'shared')

    def test_read_only_roles_are_auditor_only(self):
        self.assertEqual(READ_ONLY_ROLES, frozenset({Role.AUDITOR}))

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
