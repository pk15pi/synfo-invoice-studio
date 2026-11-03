from django.db import migrations

SEED = [
    ('Office', 'company', ['Rent', 'Electricity', 'Internet', 'Office supplies', 'Furniture', 'Repairs and maintenance']),
    ('Technology', 'company', ['Hosting', 'Domain', 'Software subscription', 'Cloud services', 'Computer hardware',
                               'Mobile devices']),
    ('Travel', 'both', ['Flight', 'Train', 'Bus', 'Taxi', 'Fuel', 'Hotel', 'Meals', 'Local conveyance']),
    ('Employee Expenses', 'claim', ['Food', 'Internet reimbursement', 'Phone reimbursement', 'Training',
                                    'Certification', 'Medical', 'Other employee expense']),
    ('Professional Services', 'company', ['Accounting', 'Legal', 'Consulting', 'Contractor', 'Freelancer']),
    ('Marketing and Sales', 'both', ['Advertising', 'Website', 'Events', 'Client meeting', 'Gifts']),
    ('Financial', 'company', ['Bank charges', 'Payment gateway charges', 'Interest', 'Taxes', 'Government fees']),
    ('Other', 'both', ['Miscellaneous']),
]


def seed(apps, schema_editor):
    Cat = apps.get_model('expense_management', 'ExpenseCategory')
    Sub = apps.get_model('expense_management', 'ExpenseSubcategory')
    for name, kind, subs in SEED:
        cat, _ = Cat.objects.get_or_create(name=name, defaults={'category_type': kind})
        for s in subs:
            Sub.objects.get_or_create(category=cat, name=s)


class Migration(migrations.Migration):
    dependencies = [('expense_management', '0001_initial')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
