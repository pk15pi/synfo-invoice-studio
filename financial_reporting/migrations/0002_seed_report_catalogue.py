from django.db import migrations


CATEGORIES = [
    ('financial-statements', 'Financial Statements', 1),
    ('accounting-reports', 'Accounting Reports', 2),
    ('sales-reports', 'Sales Reports', 3),
    ('purchase-reports', 'Purchase Reports', 4),
    ('gst-reports', 'GST Reports', 5),
    ('tds-reports', 'TDS Reports', 6),
    ('income-tax-reports', 'Income Tax Reports', 7),
    ('llp-compliance-reports', 'LLP Compliance Reports', 8),
    ('bank-reports', 'Bank Reports', 9),
    ('payroll-reports', 'Payroll Reports', 10),
    ('asset-reports', 'Asset Reports', 11),
    ('audit-readiness', 'Audit Readiness', 12),
    ('reconciliation-centre', 'Reconciliation Centre', 13),
    ('document-export-centre', 'Document Export Centre', 14),
    ('export-history', 'Export History', 15),
    ('reporting-settings', 'Reporting Settings', 16),
]

REPORTS = [
    ('rev_summary', 'Revenue Summary', 'sales-reports', 'invoice_app'),
    ('rev_monthly', 'Monthly Revenue', 'sales-reports', 'invoice_app'),
    ('rev_customer', 'Customer-wise Revenue', 'sales-reports', 'invoice_app'),
    ('rev_project', 'Project-wise Revenue', 'sales-reports', 'invoice_app'),
    ('invoice_register', 'Invoice Register', 'sales-reports', 'invoice_app'),
    ('receivables', 'Outstanding Receivables', 'sales-reports', 'invoice_app'),
    ('receipts_register', 'Receipts Register', 'sales-reports', 'invoice_app'),
    ('emp_master', 'Employee Master', 'payroll-reports', 'employee_management'),
    ('emp_earnings', 'Employee Earnings', 'payroll-reports', 'employee_management'),
    ('emp_department', 'Department-wise Employees', 'payroll-reports', 'employee_management'),
    ('emp_joining', 'Joining and Leaving', 'payroll-reports', 'employee_management'),
    ('salary_history', 'Salary History', 'payroll-reports', 'employee_management'),
    ('emp_cost', 'Employee Cost', 'payroll-reports', 'employee_management'),
    ('leave_dept', 'Department-wise Leave', 'payroll-reports', 'employee_management'),
    ('payroll_summary', 'Monthly Payroll Summary', 'payroll-reports', 'employee_management'),
    ('payroll_register', 'Payroll Register', 'payroll-reports', 'employee_management'),
    ('payroll_payment', 'Payroll Payment', 'payroll-reports', 'employee_management'),
    ('payroll_department', 'Department-wise Payroll Cost', 'payroll-reports', 'employee_management'),
    ('increments', 'Salary Increments', 'payroll-reports', 'employee_management'),
    ('advances_outstanding', 'Advances & Loans Outstanding', 'payroll-reports', 'employee_management'),
    ('advance_schedule', 'Advance Recovery Schedule', 'payroll-reports', 'employee_management'),
    ('exp_summary', 'Expense Summary', 'purchase-reports', 'expense_management'),
    ('company_expenses', 'Company Expenses', 'purchase-reports', 'expense_management'),
    ('employee_claims', 'Employee Expense Claims', 'purchase-reports', 'expense_management'),
    ('exp_category', 'Category-wise Expenses', 'purchase-reports', 'expense_management'),
    ('exp_vendor', 'Vendor-wise Expenses', 'purchase-reports', 'expense_management'),
    ('exp_department', 'Department-wise Expenses', 'purchase-reports', 'expense_management'),
    ('exp_trends', 'Expense Trends', 'purchase-reports', 'expense_management'),
    ('pnl', 'Management Profit and Loss', 'financial-statements', 'reporting'),
    ('rev_vs_exp', 'Revenue vs Expense', 'financial-statements', 'reporting'),
    ('monthly_perf', 'Monthly Business Performance', 'financial-statements', 'reporting'),
    ('fy_compare', 'Financial Year Comparison', 'financial-statements', 'reporting'),
    ('ratios', 'Financial Ratios', 'financial-statements', 'reporting'),
    ('emp_cost_vs_rev', 'Employee Cost vs Revenue', 'financial-statements', 'reporting'),
    ('cash_flow', 'Cash Flow', 'bank-reports', 'reporting'),
    ('outstanding', 'Outstanding Amounts', 'accounting-reports', 'reporting'),
    ('client_profit', 'Client Profitability', 'financial-statements', 'reporting'),
    ('project_profit', 'Project Profitability', 'financial-statements', 'reporting'),
    ('unallocated', 'Unallocated Expenses', 'financial-statements', 'reporting'),
    ('ledger_tb', 'Trial Balance', 'accounting-reports', 'accounting'),
    ('ledger_gl', 'General Ledger', 'accounting-reports', 'accounting'),
    ('ledger_pnl', 'Profit & Loss (ledger)', 'accounting-reports', 'accounting'),
    ('ledger_bs', 'Balance Sheet', 'accounting-reports', 'accounting'),
    ('ledger_cash', 'Cash Flow (direct)', 'bank-reports', 'accounting'),
    ('ledger_ar', 'AR Ageing', 'accounting-reports', 'accounting'),
    ('ledger_ap', 'AP Ageing', 'accounting-reports', 'accounting'),
    ('ledger_payables', 'Payables (salary / reimbursement / GST / TDS / PF)', 'accounting-reports', 'accounting'),
    ('ledger_daybook', 'Day Book', 'accounting-reports', 'accounting'),
    ('ledger_recon', 'Reconciliation vs Ledger', 'reconciliation-centre', 'accounting'),
]

PERIOD_TYPES = ['month', 'quarter', 'half_year', 'annual', 'financial_year', 'custom']
FORMATS = ['pdf', 'xlsx', 'csv']


def seed_catalogue(apps, schema_editor):
    Category = apps.get_model('financial_reporting', 'ReportCategory')
    Definition = apps.get_model('financial_reporting', 'ReportDefinition')
    db = schema_editor.connection.alias
    Category.objects.using(db).bulk_create(
        [Category(code=code, name=name, display_order=order) for code, name, order in CATEGORIES],
        ignore_conflicts=True,
    )
    category_ids = dict(Category.objects.using(db).filter(code__in=[c[0] for c in CATEGORIES])
                        .values_list('code', 'id'))
    existing_codes = set(Definition.objects.using(db).values_list('report_code', flat=True))
    definitions = []
    for code, name, category_code, source_app in REPORTS:
        if code in existing_codes:
            continue
        service = 'reporting.ledger_reports' if code.startswith('ledger_') else 'reporting.reports'
        definitions.append(Definition(
            report_code=code,
            report_name=name,
            category_id=category_ids[category_code],
            description='Existing Reports & Analytics report. Uses the established report implementation.',
            source_application=source_app,
            source_model_or_service=service,
            supported_period_types=PERIOD_TYPES,
            supported_formats=FORMATS,
            applicability_rules={'legacy_report_key': code},
            active_status=True,
        ))
    Definition.objects.using(db).bulk_create(definitions, ignore_conflicts=True)


def unseed_catalogue(apps, schema_editor):
    Category = apps.get_model('financial_reporting', 'ReportCategory')
    Definition = apps.get_model('financial_reporting', 'ReportDefinition')
    db = schema_editor.connection.alias
    for code, *_ in REPORTS:
        Definition.objects.using(db).filter(
            report_code=code,
            applicability_rules={'legacy_report_key': code},
        ).delete()
    for code, _, _ in CATEGORIES:
        Category.objects.using(db).filter(code=code, reports__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [('financial_reporting', '0001_initial')]

    operations = [migrations.RunPython(seed_catalogue, unseed_catalogue)]
