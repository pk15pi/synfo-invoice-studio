"""Small allowlist-based helpers for sorting server-rendered table querysets."""

from django.db.models import F


def apply_queryset_sort(queryset, request, allowed_fields, *, tie_field='pk'):
    """Sort a queryset by a named allowlisted field, with nulls last and a stable tie-breaker.

    Returns ``(queryset, sort_key, direction)``. Invalid or absent parameters leave the queryset's
    existing default ordering untouched.
    """
    key = (request.GET.get('sort') or '').strip()
    direction = (request.GET.get('direction') or 'asc').lower()
    field = allowed_fields.get(key)
    if field is None or direction not in {'asc', 'desc'}:
        return queryset, '', ''

    ordering = F(field).asc(nulls_last=True) if direction == 'asc' else F(field).desc(nulls_last=True)
    order_by = [ordering]
    if tie_field and field.lstrip('-') != tie_field:
        order_by.append(F(tie_field).asc(nulls_last=True))
    return queryset.order_by(*order_by), key, direction


def apply_sequence_sort(records, request, allowed_fields):
    """Sort an already-materialized, fully filtered collection using an explicit field allowlist.

    ``allowed_fields`` maps URL keys to ``(attribute_path, value_kind)`` pairs. The original order is
    retained for equal values; records with primary keys get a stable ascending primary-key tie-break.
    """
    key = (request.GET.get('sort') or '').strip()
    direction = (request.GET.get('direction') or 'asc').lower()
    spec = allowed_fields.get(key)
    if spec is None or direction not in {'asc', 'desc'}:
        return records, '', ''

    attribute, kind = spec

    def get_value(record):
        value = record
        for part in attribute.split('__'):
            value = getattr(value, part, None) if value is not None else None
        return value

    values = list(records)
    values.sort(key=lambda record: getattr(record, 'pk', None))
    wrapped = [[get_value(record), record] for record in values]
    sorted_wrapped = sort_table_values(wrapped, 0, kind, direction)
    return [row[1] for row in sorted_wrapped], key, direction


def sort_table_values(rows, column_index, kind, direction):
    """Return rows sorted by raw values for a report table, keeping blanks last and ties stable."""
    from datetime import date, datetime
    from decimal import Decimal, InvalidOperation
    import re

    def scalar(value):
        # Report cells may carry (display value, link) pairs; sort on what the
        # user sees. Lists are used for composite labels such as supported formats.
        if isinstance(value, tuple):
            return value[0] if value else ''
        if isinstance(value, list):
            return ', '.join(str(item) for item in value)
        return value

    def value_key(row):
        value = scalar(row[column_index])
        if value is None or value == '':
            return (1, None)
        if kind in {'money', 'int', 'num', 'pct', 'number', 'numeric'}:
            try:
                if isinstance(value, (int, float, Decimal)):
                    number = Decimal(str(value))
                else:
                    number = Decimal(re.sub(r'[^0-9.\-]', '', str(value)) or '0')
                return (0, number)
            except (InvalidOperation, ValueError):
                pass
        if kind == 'date':
            if isinstance(value, datetime):
                return (0, value.timestamp())
            if isinstance(value, date):
                return (0, value.toordinal())
            text_value = str(value).strip()
            for fmt in ('%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%Y-%m-%d %H:%M:%S', '%d-%m-%Y %H:%M'):
                try:
                    parsed = datetime.strptime(text_value, fmt)
                    return (0, parsed.timestamp())
                except ValueError:
                    continue
        if isinstance(value, datetime):
            return (0, value.timestamp())
        if isinstance(value, date):
            return (0, value.toordinal())
        if hasattr(value, 'isoformat'):
            try:
                return (0, value.isoformat())
            except Exception:
                pass
        text = str(value).casefold()
        parts = re.split(r'(\d+)', text)
        return (0, tuple(int(part) if part.isdigit() else part for part in parts))

    indexed = list(enumerate(rows))
    values = [item for item in indexed if scalar(item[1][column_index]) not in (None, '')]
    blanks = [item for item in indexed if scalar(item[1][column_index]) in (None, '')]
    # Python's reverse sort remains stable for equal keys, preserving source order for duplicate values.
    values.sort(key=lambda item: value_key(item[1]), reverse=direction == 'desc')
    blanks.sort(key=lambda item: item[0])
    return [row for _, row in values + blanks]
