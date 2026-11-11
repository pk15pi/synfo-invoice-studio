from django.db import migrations

DEFAULTS = [
    ('Head Office', 'office'),
    ('Branch Office', 'office'),
    ('IT Store', 'department'),
    ('Server Room', 'room'),
    ('Warehouse', 'building'),
    ('Service Centre', 'service_centre'),
]


def seed(apps, schema_editor):
    AssetLocation = apps.get_model('asset_management', 'AssetLocation')
    for name, location_type in DEFAULTS:
        AssetLocation.objects.get_or_create(
            name=name, defaults={'location_type': location_type, 'is_active': True},
        )


def unseed(apps, schema_editor):
    AssetLocation = apps.get_model('asset_management', 'AssetLocation')
    AssetLocation.objects.filter(
        name__in=[name for name, _ in DEFAULTS], assets__isnull=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('asset_management', '0005_assetassignment_handover_number_and_more'),
    ]

    operations = [migrations.RunPython(seed, unseed)]
