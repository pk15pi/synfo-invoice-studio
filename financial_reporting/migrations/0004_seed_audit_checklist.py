"""
Seed the built-in AuditChecklist items that the audit_service.py evaluates automatically.

Each item maps to a check code that audit_service._RUNNERS or _PERIOD_RUNNERS handles.
Items with no automated runner are still seeded so manual review results can be recorded.
"""
from django.db import migrations


CHECKLISTS = [
    # (code, name, category, description, display_order)
    ('unposted-journals', 'No Unposted Draft Journals',
     'Journals',
     'Checks that no draft journal entries remain unposted for the period.',
     10),
    ('tb-balanced', 'Trial Balance Is Balanced',
     'Journals',
     'Verifies that total debits equal total credits in the period trial balance.',
     20),
    ('ar-reconciled', 'AR Management vs Ledger',
     'Reconciliation',
     'Accounts-receivable outstanding per management reports ties to the ledger AR account balance.',
     30),
    ('ap-reconciled', 'AP Management vs Ledger',
     'Reconciliation',
     'Accounts-payable outstanding per management reports ties to the ledger AP account balance.',
     40),
    ('payroll-reconciled', 'Salary Payable vs Ledger',
     'Reconciliation',
     'Salary payable per management reports ties to the ledger salary-payable account balance.',
     50),
    ('invoices-finalised', 'No Draft Invoices in Period',
     'Revenue',
     'All invoices dated within the period have been finalised (no drafts).',
     60),
    ('expenses-approved', 'No Pending Expenses in Period',
     'Expenses',
     'All company expenses dated within the period have been approved.',
     70),
    ('compliance-filed', 'All Compliance Submissions Filed',
     'Compliance',
     'Every compliance submission linked to this reporting period has been filed externally.',
     80),
    ('tds-deposited', 'TDS Deposited',
     'Compliance',
     'Manual check: TDS deducted for the period has been deposited with the government.',
     90),
    ('gst-filed', 'GST Returns Filed',
     'Compliance',
     'Manual check: GST returns (GSTR-1, GSTR-3B) for the period have been filed.',
     100),
    ('income-tax-advance', 'Advance Tax Paid',
     'Compliance',
     'Manual check: advance income-tax instalment for the period has been paid.',
     110),
    ('bank-reconciled', 'Bank Accounts Reconciled',
     'BankReconciliation',
     'Manual check: all bank accounts have been reconciled against statements for the period.',
     120),
    ('fixed-assets-verified', 'Fixed Assets Verified',
     'Assets',
     'Manual check: fixed-asset register has been reviewed and depreciation posted for the period.',
     130),
    ('provisions-booked', 'Provisions and Accruals Booked',
     'ClosingEntries',
     'Manual check: period-end provisions, accruals, and prepayments have been journalised.',
     140),
]


def seed_checklists(apps, schema_editor):
    AuditChecklist = apps.get_model('financial_reporting', 'AuditChecklist')
    db = schema_editor.connection.alias
    existing = set(AuditChecklist.objects.using(db).values_list('code', flat=True))
    to_create = [
        AuditChecklist(
            code=code,
            name=name,
            category=category,
            description=description,
            display_order=order,
            active_status=True,
        )
        for code, name, category, description, order in CHECKLISTS
        if code not in existing
    ]
    AuditChecklist.objects.using(db).bulk_create(to_create, ignore_conflicts=True)


def unseed_checklists(apps, schema_editor):
    AuditChecklist = apps.get_model('financial_reporting', 'AuditChecklist')
    db = schema_editor.connection.alias
    codes = [c[0] for c in CHECKLISTS]
    AuditChecklist.objects.using(db).filter(code__in=codes).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('financial_reporting',
         '0003_remove_compliancetemplate_fin_compliance_template_version_uniq_and_more'),
    ]

    operations = [migrations.RunPython(seed_checklists, unseed_checklists)]
