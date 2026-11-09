"""Outgoing Microsoft 365 SMTP settings for payslip emails.

Credentials: env QB_EMAIL_* overrides a local JSON file (QB_EMAIL_CONFIG_FILE, default
~/.quantbud/email.json, outside the repo). The password is never logged or rendered.
"""
import json
import os
from pathlib import Path

FIELDS = ('host', 'port', 'user', 'password', 'use_tls', 'use_ssl', 'from_email', 'send_gap_seconds')
ENV = {
    'host': 'QB_EMAIL_HOST',
    'port': 'QB_EMAIL_PORT',
    'user': 'QB_EMAIL_USER',
    'password': 'QB_EMAIL_PASSWORD',
    'use_tls': 'QB_EMAIL_USE_TLS',
    'use_ssl': 'QB_EMAIL_USE_SSL',
    'from_email': 'QB_EMAIL_FROM',
    'send_gap_seconds': 'QB_EMAIL_SEND_GAP',
}
DEFAULTS = {
    'host': 'smtp.office365.com',
    'port': '587',
    'user': 'support@synfowebs.com',
    'password': '',
    'use_tls': '1',
    'use_ssl': '0',
    'from_email': 'support@synfowebs.com',
    'send_gap_seconds': '30',
}


class EmailConfigError(Exception):
    pass


def config_path():
    return Path(os.environ.get('QB_EMAIL_CONFIG_FILE') or (Path.home() / '.quantbud' / 'email.json'))


def _read_file():
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    except (OSError, ValueError):
        data = {}
    return {key: str(data.get(key, '') or '') for key in FIELDS}


def load_config():
    stored = _read_file()
    cfg = {}
    for key in FIELDS:
        env = os.environ.get(ENV[key])
        cfg[key] = env if env not in (None, '') else (stored[key] or DEFAULTS[key])
    return cfg


def send_gap_seconds(cfg=None):
    """Seconds to wait between Send-to-all emails. Default 30; clamped to 0..600."""
    cfg = cfg or load_config()
    try:
        value = int(str(cfg.get('send_gap_seconds') or DEFAULTS['send_gap_seconds']).strip())
    except (TypeError, ValueError):
        value = 30
    return max(0, min(600, value))


def public_config():
    cfg = load_config()
    return {
        'host': cfg['host'],
        'port': cfg['port'],
        'user': cfg['user'],
        'from_email': cfg['from_email'] or cfg['user'],
        'use_tls': cfg['use_tls'] not in ('0', 'false', 'False', ''),
        'use_ssl': cfg['use_ssl'] in ('1', 'true', 'True'),
        'password_set': bool(cfg['password']),
        'password_from_env': bool(os.environ.get(ENV['password'])),
        'config_file': str(config_path()),
        'env_overrides': [ENV[k] for k in FIELDS if os.environ.get(ENV[k])],
        'send_gap_seconds': send_gap_seconds(cfg),
    }


def save_config(data):
    stored = _read_file()
    for key in FIELDS:
        if key == 'password':
            if data.get('clear_password'):
                stored['password'] = ''
            elif data.get('password'):
                stored['password'] = data['password']
        elif key == 'send_gap_seconds':
            try:
                stored[key] = str(max(0, min(600, int(data.get('send_gap_seconds')))))
            except (TypeError, ValueError):
                stored[key] = DEFAULTS['send_gap_seconds']
        elif key in data:
            value = data[key]
            if isinstance(value, bool):
                stored[key] = '1' if value else '0'
            else:
                stored[key] = str(value or '').strip()
    if not stored.get('from_email'):
        stored['from_email'] = stored.get('user') or DEFAULTS['from_email']
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(stored, indent=1), encoding='utf-8')
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _clean(message, cfg=None):
    text = str(message)
    password = (cfg or load_config()).get('password') or ''
    if password:
        text = text.replace(password, '********')
    return text.strip() or 'Unknown error'


def django_email_settings(cfg=None):
    """Map config into kwargs for django.core.mail.get_connection / settings."""
    cfg = cfg or load_config()
    use_tls = cfg['use_tls'] not in ('0', 'false', 'False', '')
    use_ssl = cfg['use_ssl'] in ('1', 'true', 'True')
    try:
        port = int(cfg['port'] or 587)
    except (TypeError, ValueError):
        port = 587
    return {
        'HOST': cfg['host'] or DEFAULTS['host'],
        'PORT': port,
        'HOST_USER': cfg['user'] or DEFAULTS['user'],
        'HOST_PASSWORD': cfg['password'] or '',
        'USE_TLS': use_tls and not use_ssl,
        'USE_SSL': use_ssl,
        'FROM': cfg['from_email'] or cfg['user'] or DEFAULTS['from_email'],
    }


def require_ready(cfg=None):
    cfg = cfg or load_config()
    mapped = django_email_settings(cfg)
    if not mapped['HOST_PASSWORD']:
        raise EmailConfigError(
            'Outgoing email password is not set. Open Shared → Settings, enter the '
            'Microsoft 365 password for support@synfowebs.com, and save.'
        )
    if not mapped['HOST_USER']:
        raise EmailConfigError('Outgoing email username is not set.')
    return mapped
