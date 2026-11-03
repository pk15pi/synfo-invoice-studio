from django.db import migrations


def forward(apps, schema_editor):
    from accounting.seed import seed
    seed(apps.get_model('accounting', 'Account'), apps.get_model('accounting', 'AccountMapping'))


class Migration(migrations.Migration):
    """Stage 6: adds 5910 Employee Advance Write-off and the payroll.advance_writeoff / payroll.advance_interest
    mappings (seed is get_or_create only - existing accounts and mappings are never changed)."""
    dependencies = [('accounting', '0003_line_party_payroll_other')]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
