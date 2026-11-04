from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounting.models import FinancialYear
from document_management.models import Document
from employee_management.storage import private_storage

from financial_reporting.models import (
    AuditChecklist,
    AuditChecklistResult,
    ComplianceSubmission,
    ComplianceTemplate,
    ExportManifest,
    ExportPackage,
    ExportPackageItem,
    FinancialPeriod,
    ReconciliationIssue,
    ReconciliationRun,
    ReportCategory,
    ReportDefinition,
    ReportRun,
)
from financial_reporting.services.period_service import get_or_create_period, resolve_period


class FinancialReportingModelTests(TestCase):
    def setUp(self):
        self.year = FinancialYear.objects.create(
            start_year=2026, start_date=date(2026, 4, 1), end_date=date(2027, 3, 31),
        )
        self.period = FinancialPeriod.objects.create(
            name='FY 2026-27', period_type='financial_year', financial_year=self.year,
            start_date=date(2026, 4, 1), end_date=date(2027, 3, 31),
        )
        self.category = ReportCategory.objects.get(code='accounting-reports')
        self.definition = ReportDefinition.objects.create(
            report_code='ledger-tb', report_name='Trial Balance', category=self.category,
            source_application='accounting', source_model_or_service='reporting.ledger_reports',
            supported_period_types=['financial_year'], supported_formats=['pdf', 'xlsx', 'csv'],
            applicability_rules={'legacy_report_key': 'ledger_tb'},
        )

    def test_persisted_period_references_existing_accounting_financial_year(self):
        period = get_or_create_period(resolve_period({'period': 'fy', 'fy': '2026'}))
        self.assertEqual(period.financial_year, self.year)
        self.assertEqual(FinancialPeriod.objects.filter(period_type='financial_year').count(), 1)

    def test_period_validation_and_unique_range_constraint(self):
        invalid = FinancialPeriod(
            name='Invalid', period_type='custom', start_date=date(2026, 5, 2), end_date=date(2026, 5, 1),
        )
        with self.assertRaises(ValidationError):
            invalid.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            FinancialPeriod.objects.create(
                name='Duplicate', period_type='financial_year', financial_year=self.year,
                start_date=date(2026, 4, 1), end_date=date(2027, 3, 31),
            )

    def test_report_definition_validates_configurable_periods_and_formats(self):
        self.definition.supported_formats = ['exe']
        with self.assertRaises(ValidationError):
            self.definition.full_clean()

    def test_run_package_reconciliation_checklist_and_compliance_records(self):
        run = ReportRun.objects.create(
            report_definition=self.definition, financial_period=self.period,
            from_date=date(2026, 4, 1), to_date=date(2027, 3, 31),
            output_format='xlsx', record_count=0, status=ReportRun.SUCCEEDED,
            source_data_as_of=None,
        )
        package = ExportPackage.objects.create(
            package_number='FY26-27-001', package_type=ExportPackage.ANNUAL,
            financial_period=self.period, from_date=date(2026, 4, 1), to_date=date(2027, 3, 31),
        )
        ExportPackageItem.objects.create(
            package=package, item_type=ExportPackageItem.REPORT, report_run=run, file_name='trial-balance.xlsx',
        )
        manifest = ExportManifest.objects.create(
            package=package, version=1, payload={'files': ['trial-balance.xlsx']},
        )
        self.assertEqual(manifest.package, package)
        self.assertEqual(package.items.get().report_run, run)

        reconciliation = ReconciliationRun.objects.create(
            reconciliation_type='trial_balance', financial_period=self.period,
            start_date=date(2026, 4, 1), end_date=date(2027, 3, 31),
            total_records=1, matched_records=0, unmatched_records=1,
            difference_amount=Decimal('0.00'),
        )
        issue = ReconciliationIssue.objects.create(
            reconciliation_run=reconciliation, source_reference='JV/2026-27/00001',
            issue_category='unmatched', expected_value=Decimal('0.00'),
        )
        self.assertIsNone(issue.actual_value)
        self.assertIsNone(issue.difference)

        checklist = AuditChecklist.objects.create(code='trial-balance', name='Trial balance balanced')
        result = AuditChecklistResult.objects.create(
            checklist=checklist, financial_period=self.period, status=AuditChecklistResult.PENDING,
        )
        self.assertEqual(result.status, AuditChecklistResult.PENDING)

        template = ComplianceTemplate.objects.create(
            template_code='llp-annual', template_name='LLP Annual Working',
            compliance_type='llp', applicable_financial_year=self.year,
            legal_framework='Configured by reviewer', form_version='1',
            configuration={'review_required': True},
        )
        submission = ComplianceSubmission.objects.create(
            compliance_template=template, reporting_period=self.period,
            status=ComplianceSubmission.DRAFT,
        )
        self.assertEqual(submission.compliance_template, template)

    def test_source_documents_and_generated_files_use_private_storage(self):
        self.assertEqual(ReportRun._meta.get_field('generated_file').storage, private_storage)
        self.assertEqual(ExportPackage._meta.get_field('zip_file').storage, private_storage)
        self.assertEqual(ComplianceSubmission._meta.get_field('uploaded_acknowledgement').storage, private_storage)
        self.assertEqual(ExportPackageItem._meta.get_field('source_document').remote_field.model, Document)
