from django.db import migrations

CATEGORIES = [
    ('Refrigerator', 'FRIDGE', 'Office refrigerators and refrigerated storage.'),
    ('Air conditioner', 'AC', 'Split, window and cassette air conditioners.'),
    ('Television', 'TV', 'Televisions and display screens in offices or common areas.'),
    ('Coffee machine', 'COFFEE', 'Coffee and beverage machines kept in pantries.'),
    ('Microwave oven', 'MICROWAVE', 'Microwave ovens and other pantry heating appliances.'),
    ('Water purifier', 'WATERPURIFIER', 'Water purifiers, dispensers and coolers.'),
    ('UPS or inverter', 'UPS', 'Uninterruptible power supplies and inverters.'),
    ('Projector', 'PROJECTOR', 'Projectors and presentation equipment.'),
    ('Air purifier', 'AIRPURIFIER', 'Air purifiers and air quality equipment.'),
    ('Camera', 'CAMERA', 'Cameras and photography equipment.'),
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
        ('asset_management', '0003_seed_asset_categories'),
    ]

    operations = [
        migrations.RunPython(seed_categories, remove_categories),
    ]
