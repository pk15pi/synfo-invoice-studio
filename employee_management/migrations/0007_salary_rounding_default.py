from django.db import migrations, models


def enable_nearest_rupee_rounding(apps, schema_editor):
    settings = apps.get_model('employee_management', 'PayrollSettings')
    settings.objects.filter(rounding='half_up_2dp').update(rounding='net_nearest_rupee')


class Migration(migrations.Migration):

    dependencies = [
        ('employee_management', '0006_document_types_form_uploads'),
    ]

    operations = [
        migrations.AlterField(
            model_name='payrollsettings',
            name='rounding',
            field=models.CharField(
                choices=[
                    ('net_nearest_rupee',
                     '2 decimals, then Net Salary to nearest rupee (0.50 rounds up; adjustment shown)'),
                    ('half_up_2dp', 'Every amount to 2 decimals (ROUND_HALF_UP)'),
                ],
                default='net_nearest_rupee',
                max_length=20,
                verbose_name='Salary rounding rule',
            ),
        ),
        migrations.RunPython(enable_nearest_rupee_rounding, migrations.RunPython.noop),
    ]
