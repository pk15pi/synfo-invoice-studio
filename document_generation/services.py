from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import DocumentNumberSequence, DocumentType


@transaction.atomic
def allocate_document_number(document_type, on_date=None):
    if not document_type.pk:
        raise ValidationError('Save the document type before allocating a number.')
    if not document_type.number_prefix.strip() or document_type.number_padding < 1:
        raise ValidationError('Configure a non-empty prefix and positive number padding.')
    on_date = on_date or timezone.localdate()
    frequency = document_type.reset_frequency
    if frequency == DocumentType.RESET_NEVER:
        period_key = 'all'
    elif frequency == DocumentType.RESET_CALENDAR:
        period_key = str(on_date.year)
    elif frequency == DocumentType.RESET_FINANCIAL:
        start_year = on_date.year if on_date.month >= 4 else on_date.year - 1
        period_key = f'{start_year}-{str(start_year + 1)[-2:]}'
    else:
        raise ValidationError('Unsupported document number reset frequency.')

    if document_type.year_convention == DocumentType.YEAR_CALENDAR:
        if frequency == DocumentType.RESET_FINANCIAL:
            raise ValidationError('Financial-year resets require financial-year numbering.')
        year_label = str(on_date.year)
    elif document_type.year_convention == DocumentType.YEAR_FINANCIAL:
        if frequency == DocumentType.RESET_CALENDAR:
            raise ValidationError('Calendar-year resets require calendar-year numbering.')
        start_year = on_date.year if on_date.month >= 4 else on_date.year - 1
        year_label = f'{start_year}-{str(start_year + 1)[-2:]}'
    elif document_type.year_convention == DocumentType.YEAR_NONE:
        year_label = ''
    else:
        raise ValidationError('Unsupported document number year convention.')
    if frequency != DocumentType.RESET_NEVER and not year_label:
        raise ValidationError('A resetting sequence must include a year in its number.')

    sequence, _ = DocumentNumberSequence.objects.get_or_create(
        document_type=document_type, period_key=period_key,
    )
    DocumentNumberSequence.objects.filter(pk=sequence.pk).update(
        next_value=F('next_value') + 1,
    )
    sequence.refresh_from_db(fields=['next_value'])
    number = sequence.next_value - 1
    suffix = f'{number:0{document_type.number_padding}d}'
    parts = [document_type.number_prefix]
    if year_label:
        parts.append(year_label)
    parts.append(suffix)
    return '-'.join(parts)
