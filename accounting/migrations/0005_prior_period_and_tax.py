from django.db import migrations


def forward(apps, schema_editor):
    from accounting.seed import seed
    seed(apps.get_model('accounting', 'Account'), apps.get_model('accounting', 'AccountMapping'))


class Migration(migrations.Migration):
    """Adds 5990 Prior Period Expenses + expense.prior_period, and 2460 Income Tax Payable (Provision) +
    tax.income_tax_paid (seed is get_or_create only - existing accounts and mappings are never changed)."""
    dependencies = [('accounting', '0004_advance_mappings')]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
