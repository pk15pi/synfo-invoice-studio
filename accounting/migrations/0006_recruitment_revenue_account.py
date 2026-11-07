from django.db import migrations


def add_recruitment_revenue_mapping(apps, schema_editor):
    from accounting.seed import seed
    seed(apps.get_model('accounting', 'Account'), apps.get_model('accounting', 'AccountMapping'))


class Migration(migrations.Migration):
    dependencies = [('accounting', '0005_prior_period_and_tax')]
    operations = [migrations.RunPython(add_recruitment_revenue_mapping, migrations.RunPython.noop)]
