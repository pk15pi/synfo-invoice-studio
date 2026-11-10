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

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='access_profile')
    role = models.CharField(max_length=32, choices=Role.choices, blank=True, default='',
                            help_text='Controls which business modules this user can open.')

    def __str__(self):
        label = self.get_role_display() or 'No role'
        return f'{self.user.get_username()} · {label}'
