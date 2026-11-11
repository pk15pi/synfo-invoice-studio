from django.db import migrations

CATEGORIES = [
    ('Laptop', 'LAPTOP', 'Portable computers issued to employees or kept for company use.'),
    ('Desktop computer', 'DESKTOP', 'Desktop workstations and all-in-one computers.'),
    ('Monitor', 'MONITOR', 'Displays used with desktops, laptops or shared desks.'),
    ('Mobile phone', 'MOBILE', 'Smartphones and feature phones.'),
    ('Tablet', 'TABLET', 'Tablets and handheld devices.'),
    ('Printer or scanner', 'PRINTER', 'Printers, scanners and multifunction devices.'),
    ('Networking equipment', 'NETWORK', 'Routers, switches, access points and other networking gear.'),
    ('Furniture', 'FURNITURE', 'Desks, chairs, cabinets and other office furniture.'),
    ('Vehicle', 'VEHICLE', 'Company cars, two-wheelers and other vehicles.'),
    ('Other', 'OTHER', 'Any asset that does not fit the categories above.'),
]


def seed_categories(apps, schema_editor):
    AssetCategory = apps.get_model('asset_management', 'AssetCategory')
    for name, code, description in CATEGORIES:
        AssetCategory.objects.get_or_create(
            code=code,
            defaults={'name': name, 'code': code, 'description': description, 'is_active': True},
        )


def remove_categories(apps, schema_editor):
    AssetCategory = apps.get_model('asset_management', 'AssetCategory')
    AssetCategory.objects.filter(
        code__in=[code for _, code, _ in CATEGORIES], assets__isnull=True,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('asset_management', '0002_remove_assetacknowledgement_asset_digital_ack_has_confirmation_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_categories, remove_categories),
    ]
