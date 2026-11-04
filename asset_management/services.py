from django.db import transaction
from django.db.models import F

from .models import AssetSettings


def _allocate_sequence(field_name, prefix_field, category_code=''):
    AssetSettings.objects.get_or_create(pk=1)
    with transaction.atomic():
        changed = AssetSettings.objects.filter(pk=1).update(**{field_name: F(field_name) + 1})
        if changed != 1:
            raise RuntimeError('Asset settings are unavailable; could not allocate a number.')
        settings = AssetSettings.objects.get(pk=1)
        sequence = getattr(settings, field_name) - 1
        number = f'{sequence:03d}'
        prefix = getattr(settings, prefix_field)
        if category_code:
            return f'{prefix}-{category_code}-{number}'
        return f'{prefix}-{number}'


def next_asset_code(category):
    return _allocate_sequence('next_asset_sequence', 'asset_prefix', category.code)


def next_handover_code():
    return _allocate_sequence('next_handover_sequence', 'handover_prefix')
