"""Every project model must be in Django admin (t65u), and admin must not reveal full sensitive numbers."""
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from expense_management.models import Vendor
from invoice_app.admin_auto import mask

BASE = Path(settings.BASE_DIR).resolve()


def local_models():
    for cfg in apps.get_app_configs():
        path = Path(cfg.path).resolve()
        if BASE in path.parents and not {'.venv', 'site-packages'} & set(path.parts):
            for m in cfg.get_models():
                if not m._meta.abstract:
                    yield m


class AdminCoverageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('admin-t', 'a@example.com', 'x')
        self.client.force_login(self.user)

    def test_every_local_model_is_registered(self):
        missing = [m._meta.label for m in local_models() if not admin.site.is_registered(m)]
        self.assertEqual(missing, [], 'Register these models in their app admin.py')

    def test_every_changelist_and_first_record_open(self):
        for m in local_models():
            info = (m._meta.app_label, m._meta.model_name)
            r = self.client.get(reverse('admin:%s_%s_changelist' % info))
            self.assertEqual(r.status_code, 200, m._meta.label)
            obj = m._default_manager.order_by('pk').first()
            if obj is not None:
                r = self.client.get(reverse('admin:%s_%s_change' % info, args=[obj.pk]))
                self.assertEqual(r.status_code, 200, m._meta.label)

    def test_sensitive_numbers_masked(self):
        v = Vendor.objects.create(name='Mask Co', pan='ABCDE1234F', bank_account_number='123456789012')
        for url in (reverse('admin:expense_management_vendor_changelist'),
                    reverse('admin:expense_management_vendor_change', args=[v.pk])):
            html = self.client.get(url).content.decode()
            self.assertNotIn('123456789012', html)
            self.assertNotIn('ABCDE1234F', html)
        self.assertIn(mask('123456789012'), html)  # change page shows the masked value
        self.assertEqual(mask('123456789012'), '\u2022' * 8 + '9012')

    def test_history_models_are_read_only(self):
        from audit.models import AuditEvent
        from accounting.models import JournalEntry
        for m in (AuditEvent, JournalEntry):
            ma = admin.site._registry[m]
            self.assertFalse(ma.has_add_permission(None))
            self.assertFalse(ma.has_delete_permission(None))