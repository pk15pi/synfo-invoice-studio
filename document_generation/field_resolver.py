from datetime import date, datetime
from decimal import Decimal

from django.core.exceptions import ValidationError


FIELD_ADAPTERS = {
    'employee': {
        'name': lambda obj: obj.full_name,
        'employee_id': lambda obj: obj.employee_id,
        'designation': lambda obj: obj.designation,
        'department': lambda obj: obj.department.name,
        'address': lambda obj: ', '.join(
            part for part in (
                obj.current_address, obj.city, obj.state, obj.pin_code, obj.country,
            ) if part
        ),
        'email': lambda obj: obj.work_email or obj.personal_email,
        'phone': lambda obj: obj.official_mobile or obj.mobile,
        'date_of_joining': lambda obj: obj.date_of_joining,
        'last_working_date': lambda obj: obj.date_of_leaving,
        'employment_type': lambda obj: obj.get_employment_type_display(),
        'work_location': lambda obj: obj.work_location,
        'probation_months': lambda obj: obj.probation_months,
        'notice_period_days': lambda obj: obj.notice_period_days,
        'reporting_manager': lambda obj: obj.reporting_manager.full_name if obj.reporting_manager_id else '',
    },
    'client': {
        'name': lambda obj: obj.name,
        'address': lambda obj: obj.address,
        'contact_person': lambda obj: obj.contact_person,
        'email': lambda obj: obj.email,
        'phone': lambda obj: obj.phone,
        'gstin': lambda obj: obj.gstin,
        'tax_id': lambda obj: obj.tax_id,
    },
    'vendor': {
        'name': lambda obj: obj.name,
        'address': lambda obj: ', '.join(part for part in (
            obj.address, obj.city, obj.state, obj.country,
        ) if part),
        'contact_person': lambda obj: obj.contact_person,
        'email': lambda obj: obj.email,
        'phone': lambda obj: obj.mobile,
        'gstin': lambda obj: obj.gstin,
        'pan': lambda obj: obj.pan,
    },
    'asset': {
        'name': lambda obj: obj.name,
        'asset_code': lambda obj: obj.asset_code,
        'category': lambda obj: obj.category.name,
        'brand': lambda obj: obj.brand,
        'brand_and_model': lambda obj: ' '.join(
            value for value in (obj.brand, obj.model_number) if value
        ),
        'model_number': lambda obj: obj.model_number,
        'serial_number': lambda obj: obj.serial_number,
        'employee_name': lambda obj: obj.current_employee.full_name if obj.current_employee_id else '',
        'location': lambda obj: obj.current_location.name if obj.current_location_id else '',
    },
    'company': {
        'display_name': lambda obj: obj.contracting_party_display,
        'legal_entity_name': lambda obj: obj.legal_entity_name,
        'brand_name': lambda obj: obj.brand_name,
        'registered_office_address': lambda obj: obj.registered_office_address,
        'business_address': lambda obj: obj.business_address,
        'address': lambda obj: obj.business_address or obj.registered_office_address,
        'email': lambda obj: obj.official_email,
        'phone': lambda obj: obj.contact_number,
        'gstin': lambda obj: obj.gstin,
        'jurisdiction': lambda obj: obj.jurisdiction,
    },
}


def _serialise(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, 'f')
    return value if value is not None else ''


def resolve_source_value(module, field, record):
    adapter = FIELD_ADAPTERS.get(module, {})
    resolver = adapter.get(field)
    if resolver is None:
        return ''
    try:
        return _serialise(resolver(record))
    except (AttributeError, ValueError, TypeError) as error:
        raise ValidationError(f'Could not populate configured field "{field}" from {module}.') from error


def source_snapshot(module, record):
    adapter = FIELD_ADAPTERS.get(module, {})
    values = {}
    for field in adapter:
        values[field] = resolve_source_value(module, field, record)
    return values
