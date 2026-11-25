from django.db import migrations

BULK_CODES = ('mouse', 'mousepad', 'headphone', 'laptopbag', 'executivebag', 'speaker')

CONSUMABLE_CATEGORIES = [
    ('PEN', 'Pens & Markers', 'Ballpoint pens, gel pens, whiteboard markers and highlighters.'),
    ('PENCIL', 'Pencils & Stationery', 'Pencils, erasers, sharpeners, sticky notes and staplers.'),
    ('PAPER', 'Paper & Notebooks', 'A4 printing paper reams, notebooks, notepads and envelopes.'),
    ('SUPPLIES', 'Office Consumables', 'Batteries, tissues, cleaning wipes and general consumables.'),
]


def seed_item_classes(apps, schema_editor):
    AssetCategory = apps.get_model('asset_management', 'AssetCategory')
    for code in BULK_CODES:
        AssetCategory.objects.filter(code__iexact=code).update(item_class='bulk')
    for code, name, description in CONSUMABLE_CATEGORIES:
        AssetCategory.objects.get_or_create(
            code=code,
            defaults={
                'name': name,
                'item_class': 'consumable',
                'description': description,
                'is_active': True,
            },
        )


def reverse_seed(apps, schema_editor):
    AssetCategory = apps.get_model('asset_management', 'AssetCategory')
    AssetCategory.objects.filter(code__in=[c for c, _, _ in CONSUMABLE_CATEGORIES]).delete()
    for code in BULK_CODES:
        AssetCategory.objects.filter(code__iexact=code).update(item_class='fixed')


class Migration(migrations.Migration):

    dependencies = [
        ('asset_management', '0009_assetcategory_item_class_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_item_classes, reverse_seed),
    ]
