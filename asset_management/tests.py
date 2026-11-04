from datetime import date
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from document_management.models import Document
from employee_management.models import Department, Employee

from .models import (
    Asset, AssetAcknowledgement, AssetAssignment, AssetCategory, AssetEvent, AssetLease, AssetLocation,
    AssetMaintenance, AssetSettings,
)
from .services import next_asset_code, next_handover_code


class AssetManagementTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name='Asset Test Department', code='AST')
        self.employee = self.make_employee('AST001')
        self.category = AssetCategory.objects.create(name='Laptop', code='LAP')
        self.asset = Asset.objects.create(
            asset_code='QB-LAP-001', name='Test laptop', category=self.category,
            purchase_price=Decimal('42500.00'),
        )

    def make_employee(self, employee_id, first_name='Asha'):
        return Employee.objects.create(
            employee_id=employee_id, first_name=first_name, last_name='Rao', department=self.department,
            designation='Engineer', date_of_joining=date(2025, 1, 1),
        )

    def test_asset_category_relationship_unique_code_and_decimal_price(self):
        self.assertEqual(self.asset.category, self.category)
        self.assertEqual(self.asset.purchase_price, Decimal('42500.00'))
        self.assertEqual(Asset._meta.get_field('purchase_price').get_internal_type(), 'DecimalField')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Asset.objects.create(asset_code='QB-LAP-001', name='Duplicate', category=self.category)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Asset.objects.create(
                asset_code='QB-LAP-NEG', name='Invalid price', category=self.category,
                purchase_price=Decimal('-1.00'),
            )

    def test_employee_relationship_uses_existing_employee_model(self):
        self.asset.current_employee = self.employee
        self.asset.save()
        self.assertEqual(self.asset.current_employee._meta.label, 'employee_management.Employee')
        self.assertEqual(self.employee.assets_in_custody.get(), self.asset)

    def test_hierarchical_locations(self):
        office = AssetLocation.objects.create(name='Office', location_type='office')
        floor = AssetLocation.objects.create(name='Floor 2', location_type='floor', parent=office)
        room = AssetLocation.objects.create(name='Room 205', location_type='room', parent=floor)
        self.assertEqual(room.parent.parent, office)
        room.parent = room
        with self.assertRaises(ValidationError):
            room.full_clean()

    def test_lease_history_and_decimal_financial_fields(self):
        first = AssetLease.objects.create(
            asset=self.asset, lessor_name='Lease Co', agreement_number='LEASE-1',
            start_date=date(2025, 1, 1), end_date=date(2025, 12, 31),
            monthly_rent=Decimal('1800.50'), security_deposit=Decimal('5000.00'),
        )
        second = AssetLease.objects.create(
            asset=self.asset, lessor_name='Lease Co', agreement_number='LEASE-2',
            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
            monthly_rent=Decimal('1950.00'),
        )
        self.assertEqual(self.asset.leases.count(), 2)
        self.assertEqual(first.monthly_rent, Decimal('1800.50'))
        self.assertEqual(second.security_deposit, Decimal('0.00'))
        self.assertEqual(AssetLease._meta.get_field('monthly_rent').get_internal_type(), 'DecimalField')

    def test_agreement_and_acknowledgement_reuse_document_index(self):
        self.assertEqual(AssetLease._meta.get_field('agreement_document').remote_field.model, Document)
        self.assertEqual(AssetAcknowledgement._meta.get_field('document').remote_field.model, Document)
        assignment = AssetAssignment.objects.create(asset=self.asset, employee=self.employee)
        acknowledgement = AssetAcknowledgement(
            assignment=assignment, acknowledgement_type=AssetAcknowledgement.DIGITAL,
            acknowledgement_name='Asha Rao', acknowledged_at=timezone.now(),
        )
        with self.assertRaises(ValidationError):
            acknowledgement.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AssetAcknowledgement.objects.create(
                assignment=assignment, acknowledgement_type=AssetAcknowledgement.DIGITAL,
                acknowledgement_name='Asha Rao', confirmation_text='',
            )
        acknowledgement.confirmation_text = 'I confirm receipt of this asset.'
        acknowledgement.save()
        self.assertIsNotNone(acknowledgement.acknowledged_at)

    def test_only_one_active_assignment_per_asset(self):
        AssetAssignment.objects.create(asset=self.asset, employee=self.employee)
        other = self.make_employee('AST002', 'Ravi')
        with self.assertRaises(IntegrityError), transaction.atomic():
            AssetAssignment.objects.create(asset=self.asset, employee=other)
        returned = AssetAssignment.objects.get(asset=self.asset)
        returned.status = AssetAssignment.RETURNED
        returned.save()
        AssetAssignment.objects.create(asset=self.asset, employee=other)
        self.assertEqual(self.asset.assignments.filter(status=AssetAssignment.ACTIVE).count(), 1)

    def test_maintenance_cost_is_decimal(self):
        maintenance = AssetMaintenance.objects.create(
            asset=self.asset, service_date=date(2026, 3, 10), issue_description='Battery replacement',
            repair_cost=Decimal('1200.25'),
        )
        self.assertEqual(maintenance.repair_cost, Decimal('1200.25'))
        self.assertEqual(AssetMaintenance._meta.get_field('repair_cost').get_internal_type(), 'DecimalField')

    def test_event_history_is_append_only(self):
        event = AssetEvent.objects.create(asset=self.asset, event_type='created')
        event.remarks = 'changed'
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            AssetEvent.objects.filter(pk=event.pk).update(remarks='changed')
        with self.assertRaises(ValidationError):
            event.delete()

    def test_asset_and_handover_sequences_are_allocated(self):
        settings = AssetSettings.current()
        settings.asset_prefix = 'QB'
        settings.next_asset_sequence = 1
        settings.handover_prefix = 'QB-AH'
        settings.next_handover_sequence = 12
        settings.save()
        self.assertEqual(next_asset_code(self.category), 'QB-LAP-001')
        self.assertEqual(next_asset_code(self.category), 'QB-LAP-002')
        self.assertEqual(next_handover_code(), 'QB-AH-012')
        settings.refresh_from_db()
        self.assertEqual(settings.next_asset_sequence, 3)
        self.assertEqual(settings.next_handover_sequence, 13)

    def test_dashboard_register_and_phase_one_placeholders_are_local_only(self):
        self.assertEqual(self.client.get(reverse('assets:home')).status_code, 200)
        response = self.client.get(reverse('assets:register'))
        self.assertContains(response, self.asset.asset_code)
        self.assertContains(self.client.get(reverse('assets:asset_new')), 'Register an asset')
        self.assertContains(self.client.get(reverse('assets:assignments')), 'later phase')
        self.assertContains(self.client.get(reverse('assets:leases')), 'later phase')
        self.assertContains(self.client.get(reverse('assets:maintenance')), 'later phase')
        self.assertEqual(
            self.client.get(reverse('assets:home'), REMOTE_ADDR='10.1.1.1').status_code,
            403,
        )

    def test_all_asset_models_are_available_in_admin(self):
        models = (
            AssetCategory, AssetLocation, Asset, AssetLease, AssetAssignment,
            AssetAcknowledgement, AssetEvent, AssetMaintenance, AssetSettings,
        )
        user = get_user_model().objects.create_superuser(
            username='asset-admin', email='asset-admin@example.com', password='test-password',
        )
        self.client.force_login(user)

        for model in models:
            with self.subTest(model=model.__name__):
                response = self.client.get(reverse(f'admin:asset_management_{model._meta.model_name}_changelist'))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(admin.site.is_registered(model))

        event_admin = admin.site._registry[AssetEvent]
        request = RequestFactory().get('/admin/')
        self.assertFalse(event_admin.has_add_permission(request))
        self.assertFalse(event_admin.has_change_permission(request))
        self.assertFalse(event_admin.has_delete_permission(request))
        event = AssetEvent.objects.create(asset=self.asset, event_type='created')
        response = self.client.get(reverse(
            'admin:asset_management_assetevent_change', args=(event.pk,),
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/admin/', REMOTE_ADDR='10.1.1.1').status_code, 403)
