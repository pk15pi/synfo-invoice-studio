from django.db import migrations, models


def forward(apps, schema_editor):
    from accounting.seed import seed
    seed(apps.get_model('accounting', 'Account'), apps.get_model('accounting', 'AccountMapping'))


class Migration(migrations.Migration):
    dependencies = [('accounting', '0002_seed_chart_of_accounts')]
    operations = [
        migrations.AddField(model_name='journalline', name='party_type',
                            field=models.CharField(blank=True, db_index=True, default='', max_length=20)),
        migrations.AddField(model_name='journalline', name='party_id',
                            field=models.CharField(blank=True, default='', max_length=40)),
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
