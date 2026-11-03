from django.db import migrations


def forward(apps, schema_editor):
    from accounting.seed import seed
    seed(apps.get_model('accounting', 'Account'), apps.get_model('accounting', 'AccountMapping'))


class Migration(migrations.Migration):
    dependencies = [('accounting', '0001_initial')]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
