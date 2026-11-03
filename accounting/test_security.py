"""Stage 9: light local-app hardening (settings / config only)."""
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from config.secret import load_secret_key


class LocalOnlyMiddlewareTests(TestCase):
    def test_remote_address_blocked_everywhere(self):
        for url in ('/', reverse('bank:accounts'), reverse('audit:log'), reverse('backup:home')):
            r = self.client.get(url, REMOTE_ADDR='192.168.1.20')
            self.assertEqual(r.status_code, 403)

    def test_localhost_allowed(self):
        self.assertNotEqual(self.client.get('/', REMOTE_ADDR='127.0.0.1').status_code, 403)
        self.assertNotEqual(self.client.get('/', REMOTE_ADDR='::1').status_code, 403)

    def test_settings(self):
        self.assertIn('config.middleware.LocalOnlyMiddleware', settings.MIDDLEWARE)
        self.assertEqual([h for h in settings.ALLOWED_HOSTS if h != 'testserver'], ['127.0.0.1', 'localhost'])
        self.assertNotEqual(settings.SECRET_KEY, 'local-only-development-key-change-if-exposed')


class SecretKeyTests(TestCase):
    def test_env_wins(self):
        with mock.patch.dict(os.environ, {'QB_SECRET_KEY': 'k' * 40}):
            self.assertEqual(load_secret_key('/nonexistent/x'), 'k' * 40)

    def test_file_generated_once_and_reused(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {'QB_SECRET_KEY': ''}):
            f = Path(d) / 'sub' / '.secret_key'
            k1 = load_secret_key(f)
            self.assertTrue(f.is_file())
            self.assertGreaterEqual(len(k1), 50)
            self.assertEqual(load_secret_key(f), k1)

    def test_unwritable_falls_back(self):
        with mock.patch.dict(os.environ, {'QB_SECRET_KEY': ''}), \
                mock.patch('config.secret.Path.mkdir', side_effect=PermissionError('ro')):
            k = load_secret_key(Path(tempfile.gettempdir()) / 'qb-no-such-dir' / '.secret_key')
        self.assertGreaterEqual(len(k), 50)
