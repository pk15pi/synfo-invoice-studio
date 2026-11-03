import re

from django.core.exceptions import ValidationError

AADHAAR_RE = re.compile(r'^\d{12}$')
PAN_RE = re.compile(r'^[A-Z]{5}\d{4}[A-Z]$')
IFSC_RE = re.compile(r'^[A-Z]{4}0[A-Z0-9]{6}$')
PIN_RE = re.compile(r'^\d{6}$')
PHONE_RE = re.compile(r'^\+?[\d\s-]{7,15}$')
UAN_RE = re.compile(r'^\d{12}$')
ESIC_RE = re.compile(r'^\d{10,17}$')

# magic bytes -> canonical extension
SIGNATURES = [(b'%PDF-', 'pdf'), (b'\x89PNG\r\n\x1a\n', 'png'), (b'\xff\xd8\xff', 'jpg')]
EXT_ALIASES = {'pdf': 'pdf', 'png': 'png', 'jpg': 'jpg', 'jpeg': 'jpg'}
CONTENT_TYPES = {'pdf': 'application/pdf', 'png': 'image/png', 'jpg': 'image/jpeg', 'zip': 'application/zip'}


def digits(value):
    return re.sub(r'[\s-]', '', value or '')


def validate_aadhaar(value):
    v = digits(value)
    if v and not AADHAAR_RE.match(v):
        raise ValidationError('Aadhaar must be 12 digits.')


def validate_pan(value):
    if value and not PAN_RE.match(value.upper()):
        raise ValidationError('PAN must look like ABCDE1234F.')


def validate_ifsc(value):
    if value and not IFSC_RE.match(value.upper()):
        raise ValidationError('IFSC must be 11 characters: 4 letters, 0, then 6 letters/digits (e.g. HDFC0001234).')


def validate_pin(value):
    if value and not PIN_RE.match(value):
        raise ValidationError('PIN code must be 6 digits.')


def validate_phone(value):
    if value and not PHONE_RE.match(value):
        raise ValidationError('Enter a valid phone number (7-15 digits, optional +).')


def validate_uan(value):
    if value and not UAN_RE.match(digits(value)):
        raise ValidationError('UAN must be 12 digits.')


def validate_esic(value):
    if value and not ESIC_RE.match(digits(value)):
        raise ValidationError('ESIC number must be 10-17 digits.')


def sniff_upload(f, max_bytes, allowed=('pdf', 'png', 'jpg')):
    """Validate an uploaded file by size, extension AND content. Returns canonical ext."""
    if f.size > max_bytes:
        raise ValidationError(f'File is too large ({f.size // 1024} KB). Limit is {max_bytes // (1024 * 1024)} MB.')
    name = (f.name or '').lower()
    ext = EXT_ALIASES.get(name.rsplit('.', 1)[-1] if '.' in name else '')
    if ext not in allowed:
        raise ValidationError('Only PDF, JPG, JPEG and PNG files are allowed.')
    pos = f.tell() if hasattr(f, 'tell') else 0
    f.seek(0)
    head = f.read(16)
    f.seek(pos)
    real = next((e for sig, e in SIGNATURES if head.startswith(sig)), None)
    if real is None or real != ext:
        raise ValidationError('The file content does not match its extension (or is not a real PDF/JPG/PNG).')
    return ext


def form_doc_limits():
    """(per-file MB, ZIP MB) for the employee-form uploads. Override with settings or env."""
    import os
    from django.conf import settings
    f = int(getattr(settings, 'EMPLOYEE_DOC_MAX_MB', None) or os.environ.get('QB_EMPLOYEE_DOC_MAX_MB') or 10)
    z = int(getattr(settings, 'EMPLOYEE_ZIP_MAX_MB', None) or os.environ.get('QB_EMPLOYEE_ZIP_MAX_MB') or 25)
    return f, z


ZIP_MAX_UNCOMPRESSED = 500 * 1024 * 1024
ZIP_MAX_ENTRIES = 2000


def sniff_zip(f, max_bytes):
    """Accept only a real, unencrypted, intact .zip. Returns 'zip'."""
    import zipfile
    if f.size > max_bytes:
        raise ValidationError(f'ZIP is too large ({f.size // 1024} KB). Limit is {max_bytes // (1024 * 1024)} MB.')
    if not (f.name or '').lower().endswith('.zip'):
        raise ValidationError('Only .zip files are allowed for skills & education documents.')
    f.seek(0)
    try:
        if not zipfile.is_zipfile(f):
            raise ValidationError('This is not a valid ZIP archive.')
        f.seek(0)
        with zipfile.ZipFile(f) as z:
            infos = z.infolist()
            if not infos:
                raise ValidationError('The ZIP archive is empty.')
            if len(infos) > ZIP_MAX_ENTRIES:
                raise ValidationError(f'The ZIP has too many files (limit {ZIP_MAX_ENTRIES}).')
            if any(i.flag_bits & 0x1 for i in infos):
                raise ValidationError('Password-protected (encrypted) ZIP files are not allowed.')
            if sum(i.file_size for i in infos) > ZIP_MAX_UNCOMPRESSED:
                raise ValidationError('The ZIP expands to more than 500 MB.')
            if z.testzip() is not None:
                raise ValidationError('The ZIP archive is corrupt.')
    except ValidationError:
        raise
    except Exception:
        raise ValidationError('The ZIP archive is corrupt or unreadable.')
    finally:
        f.seek(0)
    return 'zip'


def mask(value, keep=4, char='X'):
    v = (value or '').replace(' ', '')
    if not v:
        return ''
    if len(v) <= keep:
        return char * len(v)
    return char * (len(v) - keep) + v[-keep:]
