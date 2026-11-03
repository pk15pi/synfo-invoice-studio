from django.db import migrations, models


def backfill(apps, schema_editor):
    Employee = apps.get_model('employee_management', 'Employee')
    for e in Employee.objects.filter(alias=''):
        first = ((e.first_name or '').split() or [''])[0][:100]
        if first:
            Employee.objects.filter(pk=e.pk).update(alias=first)


class Migration(migrations.Migration):
    dependencies = [('employee_management', '0004_employee_advances')]
    operations = [
        migrations.AddField('employee', 'alias', models.CharField('Alias', max_length=100, blank=True,
                                                                  help_text='Defaults to first name')),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
