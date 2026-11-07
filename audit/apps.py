from django.apps import AppConfig

# Financial / sensitive models whose saves and deletes are written to the audit log.
AUDITED_MODELS = [
    'invoice_app.Invoice', 'invoice_app.InvoicePayment', 'invoice_app.CreditNote',
    'invoice_app.RecruitmentCandidate', 'invoice_app.RecruitmentRequisition',
    'invoice_app.RecruitmentAgreement', 'invoice_app.RecruitmentPlacement',
    'expense_management.CompanyExpense', 'expense_management.CompanyExpensePayment',
    'expense_management.EmployeeExpenseClaim', 'expense_management.EmployeeReimbursement',
    'expense_management.Vendor',
    'employee_management.PayrollPeriod', 'employee_management.EmployeePayroll',
    'employee_management.SalaryPayment', 'employee_management.EmployeeAdvance',
    'employee_management.EmployeeBankAccount', 'employee_management.StatutoryRule',
    'employee_management.Employee', 'employee_management.Department',
    'employee_management.EmployeeSalaryStructure', 'employee_management.SalaryComponent',
    'accounting.JournalEntry', 'accounting.Account', 'accounting.AccountMapping', 'accounting.FinancialPeriod',
    'bank_management.BankAccount', 'bank_management.BankTransaction', 'bank_management.ImportBatch',
    'compliance.StatutoryRule', 'compliance.StatutoryPayment', 'compliance.GstInputClaim',
    'document_management.Document',
    'asset_management.AssetCategory', 'asset_management.AssetLocation', 'asset_management.Asset',
    'asset_management.AssetLease', 'asset_management.AssetAssignment', 'asset_management.AssetAcknowledgement',
    'asset_management.AssetEvent', 'asset_management.AssetMaintenance', 'asset_management.AssetSettings',
    'financial_reporting.FinancialPeriod', 'financial_reporting.ReportCategory',
    'financial_reporting.ReportDefinition', 'financial_reporting.ReportRun',
    'financial_reporting.ExportPackage', 'financial_reporting.ExportPackageItem',
    'financial_reporting.ReconciliationRun', 'financial_reporting.ReconciliationIssue',
    'financial_reporting.AuditChecklist', 'financial_reporting.AuditChecklistResult',
    'financial_reporting.ComplianceTemplate', 'financial_reporting.ComplianceSubmission',
    'financial_reporting.ExportManifest',
    'document_generation.CompanyDocumentProfile',
    'document_generation.CompanyDocumentSignatory',
    'document_generation.DocumentCategory',
    'document_generation.DocumentType',
    'document_generation.DocumentTemplate',
    'document_generation.DocumentTemplateVersion',
    'document_generation.DocumentClause',
    'document_generation.DocumentTemplateClause',
    'document_generation.DocumentFieldDefinition',
    'document_generation.GeneratedDocument',
    'document_generation.DocumentParty',
    'document_generation.DocumentApproval',
    'document_generation.DocumentAcknowledgement',
    'document_generation.DocumentSignature',
    'document_generation.DocumentNumberSequence',
    'document_generation.DocumentActivityLog',
]


class AuditConfig(AppConfig):
    name = 'audit'
    verbose_name = 'Audit log'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        from django.apps import apps
        from django.db.models import signals

        from . import recorder
        recorder.CREATE_ACTIONS['bank_management.ImportBatch'] = 'import'
        self.registered = []
        for label in AUDITED_MODELS:
            try:
                m = apps.get_model(label)
            except LookupError:
                continue
            uid = f'audit:{label}'
            signals.pre_save.connect(recorder.pre_save, sender=m, dispatch_uid=uid + ':pre')
            signals.post_save.connect(recorder.post_save, sender=m, dispatch_uid=uid + ':post')
            signals.post_delete.connect(recorder.post_delete, sender=m, dispatch_uid=uid + ':del')
            self.registered.append(label)
