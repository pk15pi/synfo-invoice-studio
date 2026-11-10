from django.conf import settings
from django.db import models


class UserProfile(models.Model):
    """Role assignment for a Django user. Superusers are always treated as Admin."""

    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        DIRECTOR = 'director', 'Director'
        CA = 'ca', 'CA'
        HEAD_HR = 'head_hr', 'Head HR'
        HEAD_ACCOUNTANT = 'head_accountant', 'Head Accountant'
        ACCOUNTANT = 'accountant', 'Accountant'
        HR = 'hr', 'HR'
        RECRUITER = 'recruiter', 'Recruiter'
        SENIOR_RECRUITER = 'senior_recruiter', 'Senior Recruiter'
        PROJECT_MANAGER = 'project_manager', 'Project Manager'
        AUDITOR = 'auditor', 'Auditor'
        IT_ADMIN = 'it_admin', 'IT / System Administrator'
        PAYROLL_EXECUTIVE = 'payroll_executive', 'Payroll Executive'
        BILLING = 'billing', 'Billing / Accounts Receivable'
        COMPLIANCE_OFFICER = 'compliance_officer', 'Compliance Officer'
        DOCUMENT_CONTROLLER = 'document_controller', 'Document Controller / Legal'
        ASSET_MANAGER = 'asset_manager', 'Asset / Admin Manager'
        MANAGEMENT = 'management', 'Management / Executive Viewer'
        EMPLOYEE = 'employee', 'Employee Self-Service'

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='access_profile')
    role = models.CharField(max_length=32, choices=Role.choices, blank=True, default='',
                            help_text='Controls which business modules this user can open.')

    def __str__(self):
        label = self.get_role_display() or 'No role'
        return f'{self.user.get_username()} · {label}'


class RoleDefinition(models.Model):
    """A custom role created in the app, with its own set of modules."""

    code = models.SlugField(max_length=32, unique=True,
                            help_text='Generated from the role name; used on user profiles.')
    name = models.CharField(max_length=80, unique=True)
    modules = models.JSONField(default=list, help_text='Module codes this role can open.')
    read_only = models.BooleanField(default=False,
                                    help_text='Read-only roles cannot add, change or delete records.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name
