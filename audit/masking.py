"""Masking of sensitive values before they reach the audit log."""
import re

LAST4_FIELDS = {'aadhaar_number', 'aadhaar', 'pan', 'pan_number', 'snap_pan_masked', 'account_number',
                'bank_account_number', 'snap_account_number', 'snap_aadhaar_masked',
                'passport_number', 'driving_licence_number', 'uan_number', 'esic_number'}
NEVER_LOG = ('password', 'secret', 'token', 'api_key', 'apikey', 'private_key', 'otp', 'pin_code_hash')
HASH_FIELDS = {
    'rendered_content', 'content_snapshot', 'body_template', 'clause_text', 'contact_snapshot',
    'partner_details', 'signature_configuration', 'approval_workflow', 'validation_rules', 'metadata',
    'details',
}

_LONG_DIGITS = re.compile(r'\d[\d \-]{6,}\d')
_PAN = re.compile(r'\b[A-Z]{5}\d{4}[A-Z]\b')


def last4(v):
    s = re.sub(r'\s|-', '', str(v or ''))
    if not s:
        return ''
    return 'X' * max(len(s) - 4, 4) + s[-4:]


def never_log(field_name):
    n = field_name.lower()
    return any(t in n for t in NEVER_LOG)


def needs_last4(field_name):
    n = field_name.lower()
    return n in LAST4_FIELDS or 'aadhaar' in n or n.endswith('account_number') or n == 'pan' or n.endswith('_pan') \
        or n.startswith('pan_')


def mask_value(field_name, value):
    if value in (None, ''):
        return value
    if needs_last4(field_name):
        return last4(value)
    return value


def scrub_text(text):
    """Mask long digit runs (account / Aadhaar numbers) and PANs inside free text such as object reprs."""
    t = _LONG_DIGITS.sub(lambda m: last4(m.group(0)), str(text or ''))
    return _PAN.sub(lambda m: last4(m.group(0)), t)
