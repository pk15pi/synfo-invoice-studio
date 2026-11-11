import os
import shutil
import tempfile
from datetime import date
from decimal import Decimal
from unittest import mock, skipIf

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from document_management.models import Document, full_path
from document_management import services as document_services
from employee_management.models import Department, Employee

from . import receipt_email, receipt_pdf, services
from .forms import AssetForm
from .models import (
    Asset, AssetAcknowledgement, AssetAssignment, AssetCategory, AssetEvent, AssetLease, AssetLocation,
    AssetMaintenance, AssetSettings,
)
from .services import next_asset_code, next_handover_code

PDF = b'%PDF-1.4\n%asset receipt test\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
RECEIPT_TMP = tempfile.mkdtemp(prefix='qb-asset-test-')
try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - optional dependency
    try:
        import fitz
    except ImportError:
        fitz = None


def receipt_text(pdf):
    return '\n'.join(page.get_text() for page in fitz.open(stream=pdf, filetype='pdf'))


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

    def test_serialized_assets_allow_only_one_active_assignment(self):
        serialized = Asset.objects.create(
            asset_code='QB-LAP-SER', name='Serialized laptop', category=self.category,
            serial_number='SN-001',
        )
        services.assign_asset(AssetAssignment(
            asset=serialized, employee=self.employee, issue_date=date(2026, 5, 1),
        ))
        other = self.make_employee('AST002', 'Ravi')
        with self.assertRaises(ValidationError):
            services.assign_asset(AssetAssignment(
                asset=serialized, employee=other, issue_date=date(2026, 5, 1),
            ))
        returned = AssetAssignment.objects.get(asset=serialized)
        services.return_assignment(returned, actual_return_date=date(2026, 5, 10))
        services.assign_asset(AssetAssignment(
            asset=serialized, employee=other, issue_date=date(2026, 5, 11),
        ))
        self.assertEqual(serialized.assignments.filter(status=AssetAssignment.ACTIVE).count(), 1)

    def test_non_serialized_asset_tracks_quantity_and_available_units(self):
        bulk = Asset.objects.create(
            asset_code='QB-CHAIR-001', name='Office chair', category=self.category, quantity=3,
        )
        other = self.make_employee('AST002', 'Ravi')
        for employee in (self.employee, other, self.employee):
            services.assign_asset(AssetAssignment(
                asset=bulk, employee=employee, issue_date=date(2026, 5, 1),
            ))
            bulk.refresh_from_db()
            self.assertEqual(bulk.available_quantity, bulk.quantity - bulk.assignments.filter(
                status=AssetAssignment.ACTIVE,
            ).count())
        self.assertEqual(bulk.available_quantity, 0)
        self.assertEqual(bulk.current_status, Asset.ASSIGNED)
        self.assertIsNone(bulk.current_employee)
        with self.assertRaises(ValidationError):
            services.assign_asset(AssetAssignment(
                asset=bulk, employee=self.employee, issue_date=date(2026, 5, 2),
            ))

        first = AssetAssignment.objects.filter(asset=bulk, employee=self.employee).first()
        services.return_assignment(first, actual_return_date=date(2026, 6, 1))
        bulk.refresh_from_db()
        self.assertEqual(bulk.available_quantity, 1)
        self.assertEqual(bulk.current_status, Asset.AVAILABLE)

    def test_register_shows_quantity_columns(self):
        bulk = Asset.objects.create(
            asset_code='QB-CHAIR-002', name='Visitor chair', category=self.category, quantity=4,
        )
        services.assign_asset(AssetAssignment(
            asset=bulk, employee=self.employee, issue_date=date(2026, 5, 1),
        ))
        response = self.client.get(reverse('assets:register'))
        self.assertContains(response, 'Number of items')
        self.assertContains(response, 'Items available')
        rows = {asset.pk: asset for asset in response.context['assets']}
        self.assertEqual(rows[bulk.pk].quantity, 4)
        self.assertEqual(rows[bulk.pk].available_quantity, 3)
        self.assertEqual(rows[self.asset.pk].quantity, 1)
        self.assertEqual(rows[self.asset.pk].available_quantity, 1)

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
                     'maintenance', 'maintenance_new', 'locations', 'location_new'):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(f'assets:{name}')).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('assets:asset_edit', args=(self.asset.pk,))).status_code, 200,
        )
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
        assignment = AssetAssignment.objects.get(asset=asset)
        self.assertRedirects(
            response, reverse('assets:assignment_receipt', args=(assignment.pk,)) + '?confirm=1',
            fetch_redirect_response=False,
        )
        asset.refresh_from_db()
        self.assertEqual(assignment.status, AssetAssignment.ACTIVE)
        self.assertTrue(assignment.handover_number.startswith('QB-AH-'))
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


@override_settings(EMPLOYEE_PRIVATE_ROOT=RECEIPT_TMP)
class AssetFeatureTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(RECEIPT_TMP, ignore_errors=True)

    def setUp(self):
        self.department = Department.objects.create(name='Asset Feature Dept', code='AFD')
        self.employee = Employee.objects.create(
            employee_id='AFD001', first_name='Asha', last_name='Rao', department=self.department,
            designation='Engineer', date_of_joining=date(2025, 1, 1), work_email='asha@example.com',
        )
        self.category = AssetCategory.objects.create(name='Laptop', code='LAPX')
        self.location = AssetLocation.objects.create(name='Gurugram HQ', location_type='office')
        self.asset = Asset.objects.create(
            asset_code='QB-LAP-100', name='Feature laptop', category=self.category,
            current_location=self.location,
        )

    def assignment(self, **kwargs):
        assignment = AssetAssignment.objects.create(asset=self.asset, employee=self.employee, **kwargs)
        self.asset.current_status = Asset.ASSIGNED
        self.asset.current_employee = self.employee
        self.asset.save()
        return assignment

    def asset_payload(self, **overrides):
        data = {
            'name': 'Renamed laptop', 'category': self.category.pk, 'brand': 'Dell', 'model_number': 'P2422H',
            'serial_number': 'SN-100', 'ownership_type': 'owned', 'usage_type': 'company',
            'condition': 'good', 'current_location': self.location.pk, 'purchase_date': '2026-04-01',
            'purchase_price': '45000.00', 'vendor_name': 'Dell', 'invoice_number': 'INV-9',
            'warranty_expiry': '2027-04-01', 'description': '',
        }
        data.update(overrides)
        return data

    def test_locations_are_seeded_offered_and_manageable(self):
        self.assertTrue(AssetLocation.objects.filter(name='Head Office', is_active=True).exists())
        form = AssetForm()
        self.assertIn(
            AssetLocation.objects.get(name='Head Office'), form.fields['current_location'].queryset,
        )
        response = self.client.get(reverse('assets:locations'))
        self.assertContains(response, 'Head Office')
        response = self.client.post(reverse('assets:location_new'), {
            'name': 'Manesar Store', 'location_type': 'department', 'parent': '',
            'address': 'Plant 2', 'is_active': 'on',
        })
        self.assertRedirects(response, reverse('assets:locations'))
        location = AssetLocation.objects.get(name='Manesar Store')
        response = self.client.post(reverse('assets:location_edit', args=(location.pk,)), {
            'name': 'Manesar Warehouse', 'location_type': 'building', 'parent': '',
            'address': 'Plant 2',
        })
        self.assertRedirects(response, reverse('assets:locations'))
        location.refresh_from_db()
        self.assertEqual(location.name, 'Manesar Warehouse')
        self.assertFalse(location.is_active)

    def test_asset_edit_saves_while_with_company(self):
        response = self.client.post(
            reverse('assets:asset_edit', args=(self.asset.pk,)), self.asset_payload(),
        )
        self.assertRedirects(response, reverse('assets:asset_detail', args=(self.asset.pk,)))
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.name, 'Renamed laptop')
        self.assertEqual(self.asset.current_location, self.location)
        self.assertEqual(self.asset.current_status, Asset.AVAILABLE)
        self.assertTrue(self.asset.events.filter(event_type='updated').exists())

    def test_asset_form_forces_one_item_for_serialized_assets(self):
        response = self.client.post(reverse('assets:asset_new'), {
            'name': 'Serialized monitor', 'category': self.category.pk, 'serial_number': 'SN-QTY-1',
            'quantity': '5', 'ownership_type': 'owned', 'usage_type': 'company', 'condition': 'good',
            'current_location': '',
        })
        asset = Asset.objects.get(name='Serialized monitor')
        self.assertRedirects(response, reverse('assets:asset_detail', args=(asset.pk,)))
        self.assertEqual(asset.quantity, 1)

    def test_asset_form_saves_quantity_for_items_without_serial_number(self):
        response = self.client.post(reverse('assets:asset_new'), {
            'name': 'Stock chairs', 'category': self.category.pk, 'serial_number': '',
            'quantity': '6', 'ownership_type': 'owned', 'usage_type': 'employee', 'condition': 'good',
            'current_location': '',
        })
        asset = Asset.objects.get(name='Stock chairs')
        self.assertRedirects(response, reverse('assets:asset_detail', args=(asset.pk,)))
        self.assertEqual(asset.quantity, 6)
        self.assertEqual(asset.available_quantity, 6)
        detail = self.client.get(reverse('assets:asset_detail', args=(asset.pk,)))
        self.assertContains(detail, 'Quantity of items')
        self.assertContains(detail, 'Number of items')

    def test_bulk_assignment_reduces_available_count(self):
        bulk = Asset.objects.create(
            asset_code='QB-CHAIR-200', name='Training chair', category=self.category, quantity=2,
        )
        response = self.client.post(reverse('assets:assignment_new'), {
            'asset': bulk.pk, 'employee': self.employee.pk, 'issue_date': '2026-05-01',
            'issue_condition': 'good',
        })
        self.assertEqual(response.status_code, 302)
        bulk.refresh_from_db()
        self.assertEqual(bulk.available_quantity, 1)
        self.assertEqual(bulk.current_status, Asset.AVAILABLE)
        detail = self.client.get(reverse('assets:asset_detail', args=(bulk.pk,)))
        self.assertContains(detail, '1 of 2 items are currently issued')

    def test_asset_edit_locked_once_issued(self):
        assignment = self.assignment()
        response = self.client.get(reverse('assets:asset_edit', args=(self.asset.pk,)))
        self.assertRedirects(response, reverse('assets:asset_detail', args=(self.asset.pk,)))
        response = self.client.post(
            reverse('assets:asset_edit', args=(self.asset.pk,)), self.asset_payload(),
        )
        self.assertRedirects(response, reverse('assets:asset_detail', args=(self.asset.pk,)))
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.name, 'Feature laptop')
        form = AssetForm(self.asset_payload(), instance=self.asset)
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(ValidationError):
            services.update_asset(form)
        self.assertFalse(self.asset.events.filter(event_type='updated').exists())
        self.assertEqual(assignment.status, AssetAssignment.ACTIVE)

    def test_assignment_redirects_to_receipt_confirmation(self):
        response = self.client.post(reverse('assets:assignment_new'), {
            'asset': self.asset.pk, 'employee': self.employee.pk, 'issue_date': '2026-05-01',
            'issue_condition': 'good', 'accessories': 'charger',
        })
        assignment = AssetAssignment.objects.get(asset=self.asset)
        url = reverse('assets:assignment_receipt', args=(assignment.pk,))
        self.assertRedirects(response, url + '?confirm=1', fetch_redirect_response=False)
        page = self.client.get(url + '?confirm=1')
        self.assertContains(page, 'Assignment complete')
        self.assertContains(page, 'asha@example.com')
        self.assertContains(page, 'Send receipt email')
        self.assertContains(page, '<dialog')

    def test_register_accessories_are_assigned_with_the_same_details(self):
        mouse = Asset.objects.create(asset_code='QB-MOUSE-101', name='Wireless mouse', category=self.category)
        bag = Asset.objects.create(asset_code='QB-BAG-102', name='Laptop bag', category=self.category)
        response = self.client.post(reverse('assets:assignment_new'), {
            'asset': self.asset.pk, 'employee': self.employee.pk, 'issue_date': '2026-05-01',
            'expected_return_date': '2026-06-01', 'issue_condition': 'fair',
            'issue_notes': 'New joiner kit', 'accessories': 'sleeve',
            'accessory_assets': [mouse.pk, bag.pk],
            'acknowledgement_type': 'digital', 'acknowledgement_name': 'Asha Rao',
            'confirmation_text': 'I confirm receipt of these items.',
        })
        primary = AssetAssignment.objects.get(asset=self.asset, parent_assignment__isnull=True)
        self.assertRedirects(
            response, reverse('assets:assignment_receipt', args=(primary.pk,)) + '?confirm=1',
            fetch_redirect_response=False,
        )
        self.assertEqual(primary.accessories_issued, ['sleeve'])
        accessory_assignments = list(
            AssetAssignment.objects.filter(parent_assignment=primary).select_related('asset'),
        )
        self.assertEqual(
            {assignment.asset_id for assignment in accessory_assignments}, {mouse.pk, bag.pk},
        )
        for assignment in accessory_assignments:
            with self.subTest(asset=assignment.asset.asset_code):
                self.assertEqual(assignment.employee, self.employee)
                self.assertEqual(assignment.issue_date, date(2026, 5, 1))
                self.assertEqual(assignment.expected_return_date, date(2026, 6, 1))
                self.assertEqual(assignment.issue_condition, 'fair')
                self.assertEqual(assignment.issue_notes, 'New joiner kit')
                self.assertEqual(assignment.status, AssetAssignment.ACTIVE)
                self.assertTrue(assignment.handover_number.startswith('QB-AH-'))
                assignment.asset.refresh_from_db()
                self.assertEqual(assignment.asset.current_status, Asset.ASSIGNED)
                self.assertEqual(assignment.asset.current_employee, self.employee)
                self.assertTrue(assignment.asset.events.filter(event_type='assigned').exists())
                self.assertEqual(assignment.acknowledgements.get().acknowledgement_type, 'digital')

        labels = primary.accessories_handed_over()
        self.assertIn('sleeve', labels)
        self.assertIn('QB-MOUSE-101 \u2013 Wireless mouse', labels)
        self.assertIn('QB-BAG-102 \u2013 Laptop bag', labels)
        self.assertIn('QB-BAG-102', receipt_email.compose_message(primary)[1])
        detail = self.client.get(reverse('assets:asset_detail', args=(self.asset.pk,)))
        self.assertContains(detail, 'Registered accessories')
        self.assertContains(detail, 'QB-MOUSE-101')
        accessory_detail = self.client.get(reverse('assets:asset_detail', args=(mouse.pk,)))
        self.assertContains(accessory_detail, 'Issued as an accessory with')
        assignments_page = self.client.get(reverse('assets:assignments'))
        self.assertContains(assignments_page, 'Accessory of QB-LAP-100')

        accessory_assignment = accessory_assignments[0]
        response = self.client.post(
            reverse('assets:assignment_return', args=(accessory_assignment.pk,)),
            {'actual_return_date': '2026-06-05', 'return_condition': 'good'},
        )
        self.assertRedirects(response, reverse('assets:assignments'))
        accessory_assignment.refresh_from_db()
        primary.refresh_from_db()
        self.assertEqual(accessory_assignment.status, AssetAssignment.RETURNED)
        self.assertEqual(primary.status, AssetAssignment.ACTIVE)

    def test_assignment_form_uses_one_searchable_accessory_field(self):
        Asset.objects.create(asset_code='QB-MOUSE-104', name='Spare mouse', category=self.category)
        response = self.client.get(reverse('assets:assignment_new'))
        self.assertContains(response, 'Accessories issued')
        self.assertNotContains(response, 'Accessories from asset register')
        self.assertContains(response, 'data-accessory-picker="1"')
        self.assertContains(response, 'asset_management/accessory_picker.js')
        self.assertContains(response, 'QB-MOUSE-104')
        self.assertContains(response, 'input type="hidden" name="accessory_assets"', count=0)

    def test_accessory_selection_rejects_main_and_unavailable_assets(self):
        response = self.client.post(reverse('assets:assignment_new'), {
            'asset': self.asset.pk, 'employee': self.employee.pk, 'issue_date': '2026-05-01',
            'issue_condition': 'good', 'accessory_assets': [self.asset.pk],
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'main asset cannot also be listed as an accessory')
        self.assertRegex(
            response.content.decode(),
            rf'name="accessory_assets"[^>]*value="{self.asset.pk}"',
        )
        self.assertFalse(AssetAssignment.objects.filter(asset=self.asset).exists())

        busy = Asset.objects.create(asset_code='QB-MOUSE-103', name='Busy mouse', category=self.category)
        AssetAssignment.objects.create(asset=busy, employee=self.employee)
        response = self.client.post(reverse('assets:assignment_new'), {
            'asset': self.asset.pk, 'employee': self.employee.pk, 'issue_date': '2026-05-01',
            'issue_condition': 'good', 'accessory_assets': [busy.pk],
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(AssetAssignment.objects.filter(asset=self.asset).exists())
        self.assertEqual(AssetAssignment.objects.filter(asset=busy).count(), 1)

        with self.assertRaises(ValidationError):
            services.assign_asset(
                AssetAssignment(asset=self.asset, employee=self.employee, issue_date=date(2026, 5, 1)),
                accessory_assets=[busy],
            )
        self.assertFalse(AssetAssignment.objects.filter(asset=self.asset).exists())
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.current_status, Asset.AVAILABLE)

    def test_receipt_upload_indexes_document_and_acknowledgement(self):
        assignment = self.assignment()
        url = reverse('assets:assignment_receipt', args=(assignment.pk,))
        response = self.client.post(url, {
            'action': 'upload', 'note': 'Signed copy received',
            'file': SimpleUploadedFile('signed.pdf', PDF),
        })
        self.assertRedirects(response, url)
        doc = Document.objects.get(source_type='asset_receipt')
        self.assertEqual(doc.category, 'asset')
        self.assertEqual((doc.content_type.model, doc.object_id), ('assetassignment', str(assignment.pk)))
        self.assertTrue(doc.file_path.startswith('asset_receipts/'))
        self.assertFalse(doc.missing)
        with open(full_path(doc.file_path), 'rb') as fh:
            self.assertTrue(fh.read().startswith(b'%PDF-'))
        acknowledgement = assignment.acknowledgements.get()
        self.assertEqual(acknowledgement.acknowledgement_type, AssetAcknowledgement.PHYSICAL)
        self.assertEqual(acknowledgement.document, doc)
        self.assertEqual(acknowledgement.confirmation_text, 'Signed copy received')
        self.assertEqual(self.client.get(reverse('docs:serve', args=[doc.pk])).status_code, 200)
        document_services.sync_index()
        doc.refresh_from_db()
        self.assertFalse(doc.missing)
        detail = self.client.get(reverse('assets:asset_detail', args=(self.asset.pk,)))
        self.assertContains(detail, 'Signed receipt')

    def test_generated_handover_receipt_pdf(self):
        assignment = self.assignment()
        doc = receipt_email.ensure_receipt_document(assignment)
        self.assertEqual(doc.source_type, 'asset_handover_receipt')
        self.assertEqual(doc.category, 'asset')
        self.assertEqual(doc.object_id, str(assignment.pk))
        with open(full_path(doc.file_path), 'rb') as fh:
            self.assertTrue(fh.read().startswith(b'%PDF-'))
        self.assertIn(doc, services.receipts_for(assignment))

    @skipIf(fitz is None, 'PyMuPDF not installed')
    def test_generated_receipt_lists_items_with_quantity_inside_margins(self):
        mouse = Asset.objects.create(
            asset_code='QB-MOUSE-777', name='Wireless mouse', category=self.category,
            serial_number='MS-777',
        )
        assignment = services.assign_asset(
            AssetAssignment(
                asset=self.asset, employee=self.employee, issue_date=date(2026, 5, 1),
                accessories_issued=['sleeve'],
            ),
            accessory_assets=[mouse],
        )
        pdf = receipt_pdf.render_handover_receipt(assignment)
        text = receipt_text(pdf)
        self.assertIn('Items issued', text)
        self.assertIn('Qty', text)
        self.assertIn('QB-LAP-100', text)
        self.assertIn('QB-MOUSE-777', text)
        self.assertIn('MS-777', text)
        self.assertIn('Other accessories: sleeve', text)
        self.assertIn('Total items received: 3', text)

        page = fitz.open(stream=pdf, filetype='pdf')[0]
        right_limit = page.rect.width - receipt_pdf.MARGIN
        for block in page.get_text('dict')['blocks']:
            for line in block.get('lines', []):
                for span in line['spans']:
                    self.assertLessEqual(span['bbox'][2], right_limit + 0.5, span['text'])

    def test_generated_receipt_is_replaced_when_sent_again(self):
        assignment = self.assignment()
        first = receipt_email.ensure_receipt_document(assignment)
        first_path = first.file_path
        self.assertTrue(os.path.exists(full_path(first_path)))
        second = receipt_email.ensure_receipt_document(assignment)
        self.assertEqual(
            Document.objects.filter(
                source_type='asset_handover_receipt', source_id=str(assignment.pk),
            ).count(),
            1,
        )
        self.assertNotEqual(second.file_path, first_path)
        self.assertFalse(os.path.exists(full_path(first_path)))
        self.assertTrue(os.path.exists(full_path(second.file_path)))

    def test_receipt_email_body_lists_items_and_quantities(self):
        mouse = Asset.objects.create(
            asset_code='QB-MOUSE-778', name='Wireless mouse', category=self.category,
            serial_number='MS-778',
        )
        assignment = services.assign_asset(
            AssetAssignment(
                asset=self.asset, employee=self.employee, issue_date=date(2026, 5, 1),
                accessories_issued=['sleeve'],
            ),
            accessory_assets=[mouse],
        )
        subject, body = receipt_email.compose_message(assignment)
        self.assertIn('Asset handover receipt', subject)
        self.assertIn('Items issued:', body)
        self.assertIn('1. QB-LAP-100 - Feature laptop | Qty: 1', body)
        self.assertIn('2. QB-MOUSE-778 - Wireless mouse (Serial: MS-778) | Qty: 1', body)
        self.assertIn('Other accessories: sleeve', body)
        self.assertIn('Total items received: 3', body)

    def test_email_receipt_sends_pdf_and_records_success(self):
        assignment = self.assignment()
        mapped = {
            'HOST': 'smtp.test', 'PORT': 587, 'HOST_USER': 'user', 'HOST_PASSWORD': 'secret',
            'USE_TLS': True, 'USE_SSL': False, 'FROM': 'from@test',
        }
        with mock.patch.object(receipt_email.EC, 'load_config', return_value={}), \
                mock.patch.object(receipt_email.EC, 'require_ready', return_value=mapped), \
                mock.patch.object(receipt_email, 'get_connection', return_value=object()), \
                mock.patch.object(receipt_email.EmailMessage, 'send') as send:
            response = self.client.post(
                reverse('assets:assignment_receipt', args=(assignment.pk,)), {'action': 'send'},
            )
        self.assertRedirects(response, reverse('assets:assignments'))
        send.assert_called_once()
        assignment.refresh_from_db()
        self.assertEqual(assignment.receipt_email_to, 'asha@example.com')
        self.assertIsNotNone(assignment.receipt_emailed_at)
        self.assertEqual(assignment.receipt_email_error, '')
        doc = Document.objects.get(source_type='asset_handover_receipt')
        with open(full_path(doc.file_path), 'rb') as fh:
            self.assertTrue(fh.read().startswith(b'%PDF-'))

    def test_email_without_address_and_skip(self):
        self.employee.work_email = ''
        self.employee.personal_email = ''
        self.employee.save()
        assignment = self.assignment()
        url = reverse('assets:assignment_receipt', args=(assignment.pk,))
        response = self.client.post(url, {'action': 'send'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'no work or personal email address on file')
        assignment.refresh_from_db()
        self.assertEqual(assignment.receipt_email_to, '')
        self.assertIn('no work or personal email', assignment.receipt_email_error)
        self.assertFalse(Document.objects.filter(source_type='asset_handover_receipt').exists())

        response = self.client.post(url, {'action': 'skip'})
        self.assertRedirects(response, reverse('assets:assignments'))
        assignment.refresh_from_db()
        self.assertIsNone(assignment.receipt_emailed_at)

    def test_email_failure_is_recorded(self):
        assignment = self.assignment()
        mapped = {
            'HOST': 'smtp.test', 'PORT': 587, 'HOST_USER': 'user', 'HOST_PASSWORD': 'secret',
            'USE_TLS': True, 'USE_SSL': False, 'FROM': 'from@test',
        }
        with mock.patch.object(receipt_email.EC, 'load_config', return_value={}), \
                mock.patch.object(receipt_email.EC, 'require_ready', return_value=mapped), \
                mock.patch.object(receipt_email, 'get_connection', return_value=object()), \
                mock.patch.object(receipt_email.EmailMessage, 'send', side_effect=RuntimeError('smtp down')):
            response = self.client.post(
                reverse('assets:assignment_receipt', args=(assignment.pk,)), {'action': 'send'},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'smtp down')
        assignment.refresh_from_db()
        self.assertIsNone(assignment.receipt_emailed_at)
        self.assertIn('smtp down', assignment.receipt_email_error)

    def test_receipt_workflow_is_local_only(self):
        assignment = self.assignment()
        url = reverse('assets:assignment_receipt', args=(assignment.pk,))
        self.assertEqual(self.client.get(url, REMOTE_ADDR='10.1.1.1').status_code, 403)
        self.assertEqual(
            self.client.get(reverse('assets:locations'), REMOTE_ADDR='10.1.1.1').status_code, 403,
        )
        self.assertEqual(
            self.client.get(reverse('assets:asset_edit', args=(self.asset.pk,)),
                            REMOTE_ADDR='10.1.1.1').status_code, 403,
        )
