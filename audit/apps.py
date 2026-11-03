from django.apps import AppConfig

# Financial / sensitive models whose saves and deletes are written to the audit log.
AUDITED_MODELS = [
    'invoice_app.Invoice', 'invoice_app.InvoicePayment', 'invoice_app.CreditNote',
    'expense_management.CompanyExpense', 'expense_management.CompanyExpensePayment',
    'expense_management.EmployeeExpenseClaim', 'expense_management.EmployeeReimbursement',
    'expense_management.Vendor',
    'employee_management.PayrollPeriod', 'employee_management.EmployeePayroll',
    'employee_management.SalaryPayment', 'employee_management.EmployeeAdvance',
    'employee_management.EmployeeBankAccount', 'employee_management.StatutoryRule',
    'accounting.JournalEntry', 'accounting.Account', 'accounting.AccountMapping', 'accounting.FinancialPeriod',
    'bank_management.BankAccount', 'bank_management.BankTransaction', 'bank_management.ImportBatch',
    'compliance.StatutoryRule', 'compliance.StatutoryPayment', 'compliance.GstInputClaim',
    'document_management.Document',
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
