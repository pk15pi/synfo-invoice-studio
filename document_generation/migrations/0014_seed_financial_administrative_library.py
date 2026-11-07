from django.db import migrations


FINANCIAL_TYPES = (
    ('invoice-cover-letter', 'Invoice Cover Letter', 'QB-ICL'),
    ('payment-reminder', 'Payment Reminder', 'QB-PRM'),
    ('debit-credit-note-communication', 'Debit / Credit Note Communication', 'QB-DCN'),
    ('expense-reimbursement-declaration', 'Expense Reimbursement Declaration', 'QB-ERD'),
    ('vendor-confirmation-letter', 'Vendor Confirmation Letter', 'QB-VCF'),
    ('outstanding-payment-notice', 'Outstanding Payment Notice', 'QB-OPN'),
)

OTHER_TYPES = (
    ('resource-supply-agreement', 'Resource Supply Agreement', 'QB-RSA', ('client',)),
    ('staff-augmentation-agreement', 'Staff Augmentation Agreement', 'QB-SAA', ('client',)),
    ('dedicated-resource-agreement', 'Dedicated Resource Agreement', 'QB-DRA', ('client',)),
    ('recruitment-placement-agreement', 'Recruitment / Placement Agreement', 'QB-RPA', ('client', 'candidate')),
    ('subcontractor-agreement', 'Subcontractor Agreement', 'QB-SCA', ('vendor', 'client')),
    ('internal-memo', 'Internal Memo', 'QB-MEM', ('internal',)),
)


def seed_financial_administrative_library(apps, schema_editor):
    Category = apps.get_model('document_generation', 'DocumentCategory')
    DocumentType = apps.get_model('document_generation', 'DocumentType')
    database = schema_editor.connection.alias
    financial, _ = Category.objects.using(database).get_or_create(
        code='financial-administrative',
        defaults={
            'name': 'Financial & Administrative Documents',
            'description': 'Payment, invoice, reimbursement and administrative communications.',
            'display_order': 8,
            'is_active': True,
        },
    )
    category_codes = {row.code: row for row in Category.objects.using(database).filter(is_active=True)}

    for code, name, prefix in FINANCIAL_TYPES:
        document_type, _ = DocumentType.objects.using(database).get_or_create(
            code=code,
            defaults={
                'name': name, 'number_prefix': prefix, 'number_padding': 3,
                'year_convention': 'calendar', 'reset_frequency': 'calendar_year',
                'approval_required': True, 'default_output_formats': ['docx', 'pdf'],
                'is_active': True,
            },
        )
        document_type.categories.db_manager(database).add(financial.pk)
        for related_code in ('client', 'vendor', 'employee'):
            if related_code in category_codes and code in {
                'invoice-cover-letter', 'payment-reminder', 'debit-credit-note-communication',
                'vendor-confirmation-letter', 'outstanding-payment-notice',
            }:
                document_type.categories.db_manager(database).add(category_codes[related_code].pk)
        if code == 'expense-reimbursement-declaration' and 'employee' in category_codes:
            document_type.categories.db_manager(database).add(category_codes['employee'].pk)

    for code, name, prefix, related_categories in OTHER_TYPES:
        document_type, _ = DocumentType.objects.using(database).get_or_create(
            code=code,
            defaults={
                'name': name, 'number_prefix': prefix, 'number_padding': 3,
                'year_convention': 'calendar', 'reset_frequency': 'calendar_year',
                'approval_required': True, 'default_output_formats': ['docx', 'pdf'],
                'is_active': True,
            },
        )
        for related_code in related_categories:
            if related_code in category_codes:
                document_type.categories.db_manager(database).add(category_codes[related_code].pk)


class Migration(migrations.Migration):
    dependencies = [
        ('document_generation', '0013_template_format_choice'),
    ]

    operations = [
        migrations.RunPython(seed_financial_administrative_library, migrations.RunPython.noop),
    ]
