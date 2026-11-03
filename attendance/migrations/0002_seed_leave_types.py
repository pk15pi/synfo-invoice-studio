from decimal import Decimal

from django.db import migrations

TYPES = [
    ('CL', 'Casual Leave', True, '12', False, None),
    ('SL', 'Sick Leave', True, '12', False, None),
    ('EL', 'Earned / Privilege Leave', True, '15', True, '30'),
    ('LWP', 'Leave Without Pay', False, '0', False, None),
    ('CO', 'Comp Off', True, '0', False, None),
]


def seed(apps, schema_editor):
    LeaveType = apps.get_model('attendance', 'LeaveType')
    for code, name, paid, quota, cf, cap in TYPES:
        LeaveType.objects.get_or_create(code=code, defaults=dict(
            name=name, paid=paid, annual_quota=Decimal(quota), carry_forward=cf,
            carry_forward_cap=Decimal(cap) if cap else None, active=True))


class Migration(migrations.Migration):
    dependencies = [('attendance', '0001_initial')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
