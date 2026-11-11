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

from .forms import AssetForm
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

    def test_dashboard_and_workflow_pages_are_local_only(self):
        self.assertEqual(self.client.get(reverse('assets:home')).status_code, 200)
        response = self.client.get(reverse('assets:register'))
        self.assertContains(response, self.asset.asset_code)
        detail = self.client.get(reverse('assets:asset_detail', args=(self.asset.pk,)))
        self.assertContains(detail, self.asset.asset_code)
        for name in ('asset_new', 'assignments', 'assignment_new', 'leases', 'lease_new',
                     'maintenance', 'maintenance_new'):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(f'assets:{name}')).status_code, 200)
        for name in ('assets:home', 'assets:assignments', 'assets:asset_new'):
            with self.subTest(page=name):
                self.assertEqual(
                    self.client.get(reverse(name), REMOTE_ADDR='10.1.1.1').status_code, 403,
                )

    def test_register_asset_allocates_code_and_records_event(self):
        settings = AssetSettings.current()
        settings.next_asset_sequence = 1
        settings.asset_prefix = 'QB'
        settings.save()
        response = self.client.post(reverse('assets:asset_new'), {
            'name': 'Dell monitor', 'category': self.category.pk, 'brand': 'Dell',
            'model_number': 'P2422H', 'serial_number': 'CN-001', 'ownership_type': 'owned',
            'usage_type': 'company', 'condition': 'good', 'current_location': '',
            'purchase_date': '2026-04-01', 'purchase_price': '12000.00', 'vendor_name': 'Dell',
            'invoice_number': 'INV-1', 'warranty_expiry': '2027-04-01', 'description': '',
        })
        asset = Asset.objects.get(name='Dell monitor')
        self.assertRedirects(response, reverse('assets:asset_detail', args=(asset.pk,)))
        self.assertEqual(asset.asset_code, 'QB-LAP-002')
        self.assertEqual(asset.current_status, Asset.COMPANY_STATUS)
        self.assertTrue(asset.events.filter(event_type='created').exists())

    def test_register_form_offers_seeded_categories_and_saves_with_one(self):
        form = AssetForm()
        seeded_codes = set(form.fields['category'].queryset.values_list('code', flat=True))
        self.assertTrue(
            {'LAPTOP', 'DESKTOP', 'MONITOR', 'OTHER', 'FRIDGE', 'AC', 'TV', 'COFFEE'} <= seeded_codes,
        )
        settings = AssetSettings.current()
        settings.asset_prefix = 'QB'
        settings.next_asset_sequence = 1
        settings.save()
        category = AssetCategory.objects.get(code='MONITOR')
        response = self.client.post(reverse('assets:asset_new'), {
            'name': 'Seeded category monitor', 'category': category.pk, 'ownership_type': 'owned',
            'usage_type': 'company', 'condition': 'good', 'current_location': '',
        })
        asset = Asset.objects.get(name='Seeded category monitor')
        self.assertRedirects(response, reverse('assets:asset_detail', args=(asset.pk,)))
        self.assertEqual(asset.category, category)
        self.assertEqual(asset.asset_code, 'QB-MONITOR-001')

    def test_assign_and_return_workflow_updates_custody_and_events(self):
        asset = Asset.objects.create(
            asset_code='QB-LAP-002', name='Assigned laptop', category=self.category,
        )
        response = self.client.post(reverse('assets:assignment_new'), {
            'asset': asset.pk, 'employee': self.employee.pk, 'issue_date': '2026-05-01',
            'expected_return_date': '2026-06-01', 'issue_condition': 'good',
            'issue_notes': 'New joiner kit', 'accessories': 'charger, bag',
            'acknowledgement_type': 'digital', 'acknowledgement_name': 'Asha Rao',
            'confirmation_text': 'I confirm receipt of this asset.',
        })
        self.assertRedirects(response, reverse('assets:assignments'))
        assignment = AssetAssignment.objects.get(asset=asset)
        asset.refresh_from_db()
        self.assertEqual(assignment.status, AssetAssignment.ACTIVE)
        self.assertEqual(assignment.accessories_issued, ['charger', 'bag'])
        self.assertEqual(asset.current_status, Asset.ASSIGNED)
        self.assertEqual(asset.current_employee, self.employee)
        acknowledgement = assignment.acknowledgements.get()
        self.assertEqual(acknowledgement.acknowledgement_type, AssetAcknowledgement.DIGITAL)
        self.assertTrue(asset.events.filter(event_type='assigned').exists())

        response = self.client.post(reverse('assets:assignment_return', args=(assignment.pk,)), {
            'actual_return_date': '2026-06-05', 'return_condition': 'fair',
            'return_notes': 'Minor scratches', 'returned_accessories': 'charger, bag',
        })
        self.assertRedirects(response, reverse('assets:assignments'))
        assignment.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(assignment.status, AssetAssignment.RETURNED)
        self.assertEqual(assignment.accessories_returned, ['charger', 'bag'])
        self.assertEqual(asset.current_status, Asset.AVAILABLE)
        self.assertIsNone(asset.current_employee)
        self.assertTrue(asset.events.filter(event_type='returned').exists())

    def test_lease_workflow_records_ownership_and_status_history(self):
        asset = Asset.objects.create(
            asset_code='QB-LAP-003', name='Leased laptop', category=self.category,
        )
        response = self.client.post(reverse('assets:lease_new'), {
            'asset': asset.pk, 'lessor_name': 'RentCo', 'agreement_number': 'L-1',
            'start_date': '2026-01-01', 'end_date': '2026-12-31', 'monthly_rent': '5000.00',
            'security_deposit': '10000.00', 'billing_frequency': 'monthly',
        })
        self.assertRedirects(response, reverse('assets:leases'))
        lease = AssetLease.objects.get(asset=asset)
        asset.refresh_from_db()
        self.assertEqual(lease.status, AssetLease.ACTIVE)
        self.assertEqual(asset.ownership_type, Asset.LEASED)
        self.assertTrue(asset.events.filter(event_type='lease').exists())

        response = self.client.post(reverse('assets:lease_update', args=(lease.pk,)), {
            'status': 'terminated', 'remarks': 'Equipment returned early.',
        })
        self.assertRedirects(response, reverse('assets:leases'))
        lease.refresh_from_db()
        self.assertEqual(lease.status, AssetLease.TERMINATED)
        self.assertTrue(
            asset.events.filter(event_type='lease', remarks__icontains='Terminated').exists(),
        )

    def test_maintenance_workflow_updates_asset_status(self):
        asset = Asset.objects.create(
            asset_code='QB-LAP-004', name='Repair laptop', category=self.category,
        )
        response = self.client.post(reverse('assets:maintenance_new'), {
            'asset': asset.pk, 'service_date': '2026-03-01', 'service_provider': 'FixIt',
            'issue_description': 'Battery does not hold charge.', 'warranty_claim': '',
        })
        self.assertRedirects(response, reverse('assets:maintenance'))
        record = AssetMaintenance.objects.get(asset=asset)
        asset.refresh_from_db()
        self.assertEqual(record.status, 'reported')
        self.assertEqual(asset.current_status, Asset.UNDER_MAINTENANCE)
        self.assertTrue(asset.events.filter(event_type='maintenance').exists())

        response = self.client.post(reverse('assets:maintenance_update', args=(record.pk,)), {
            'status': 'completed', 'completion_date': '2026-03-05',
            'work_performed': 'Battery replaced under warranty.', 'repair_cost': '0.00',
            'remarks': 'Returned to store.',
        })
        self.assertRedirects(response, reverse('assets:maintenance'))
        record.refresh_from_db()
        asset.refresh_from_db()
        self.assertEqual(record.status, 'completed')
        self.assertEqual(asset.current_status, Asset.AVAILABLE)
        self.assertTrue(
            asset.events.filter(event_type='maintenance', remarks__icontains='Completed').exists(),
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
