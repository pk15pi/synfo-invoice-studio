"""Local single-user settings for QuantBud Invoice Studio (no auth, no admin by design)."""
import os
import sys
from pathlib import Path

from .secret import load_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent
# Key: env QB_SECRET_KEY, else private_media/.secret_key (auto-generated, git-ignored), else per-process.
SECRET_KEY = load_secret_key(Path(os.environ.get('QB_PRIVATE_ROOT') or (BASE_DIR / 'private_media')) / '.secret_key')
# Debug is opt-in; WhiteNoise serves static assets independently of DEBUG.
DEBUG = os.environ.get('QB_DEBUG', '0') == '1'
ALLOWED_HOSTS = ['127.0.0.1', 'localhost']
if len(sys.argv) > 1 and sys.argv[1] == 'test':
    ALLOWED_HOSTS.append('testserver')  # Django test client host

INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'invoice_app',
    'employee_management',
    'attendance',
    'expense_management',
    'reporting',
    'accounting',
    'bank_management',
    'compliance',
    'document_management',
    'audit',
    'backup',
]
MIDDLEWARE = [
    'config.middleware.LocalOnlyMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'audit.middleware.AuditContextMiddleware',
]
# No sessions app: flash messages are stored in a signed cookie.
MESSAGE_STORAGE = 'django.contrib.messages.storage.cookie.CookieStorage'

# Same-origin referrers keep the browser's Origin header intact for CSRF checks (never 'no-referrer').
SECURE_REFERRER_POLICY = 'same-origin'
CSRF_TRUSTED_ORIGINS = ['http://localhost:8000', 'http://127.0.0.1:8000']

ROOT_URLCONF = 'config.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [], 'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request',
        'django.contrib.messages.context_processors.messages',
    ]},
}]
WSGI_APPLICATION = 'config.wsgi.application'
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3',
                         'NAME': os.environ.get('QB_DB_PATH') or BASE_DIR / 'db.sqlite3'}}
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
WHITENOISE_USE_FINDERS = True
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Employee documents/photos: private folder, never served as static/media.
EMPLOYEE_PRIVATE_ROOT = Path(os.environ.get('QB_PRIVATE_ROOT') or (BASE_DIR / 'private_media'))
FILE_UPLOAD_PERMISSIONS = 0o600
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

# Backups (backup app): folder outside the repo; keep-last-N applies to app-made manual/automatic ZIPs only.
QB_APP_VERSION = os.environ.get('QB_APP_VERSION', 'stage8')
BACKUP_DIR = Path(os.environ.get('QB_BACKUP_DIR') or (r'E:\QuantBud_Backups' if os.name == 'nt'
                                                       else BASE_DIR.parent / 'QuantBud_Backups'))
BACKUP_KEEP_LAST = int(os.environ.get('QB_BACKUP_KEEP_LAST', '10'))
