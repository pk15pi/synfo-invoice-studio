import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.db import connections
from django.test import TestCase
from django.urls import reverse

from backup import pg_sync as P
from backup import services as S

SECRET = 'Sup3r-Secret-Pw!'


class _Cfg(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfgfile = Path(self.tmp.name) / 'pg.json'
        env = {k: '' for k in P.ENV.values()}
        env['QB_PG_CONFIG_FILE'] = str(self.cfgfile)
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        for k in P.ENV.values():
            os.environ.pop(k, None)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def save(self, **kw):
        d = dict(host='127.0.0.1', port=1, name='qb', user='qbuser', password=SECRET, sslmode='prefer')
        d.update(kw)
        P.save_config(d)


class ConfigTests(_Cfg):
    def test_password_kept_on_empty_and_never_public(self):
        self.save()
        self.save(password='', host='db.local')
        cfg = P.load_config()
        self.assertEqual(cfg['password'], SECRET)
        self.assertEqual(cfg['host'], 'db.local')
        pub = P.public_config()
        self.assertNotIn('password', pub)
        self.assertTrue(pub['password_set'])
        self.assertNotIn(SECRET, json.dumps(pub))

    def test_clear_password_and_env_override(self):
        self.save()
        self.save(password='', clear_password=True)
        self.assertEqual(P.load_config()['password'], '')
        with mock.patch.dict(os.environ, {'QB_PG_HOST': 'envhost', 'QB_PG_PASSWORD': 'x'}):
            self.assertEqual(P.load_config()['host'], 'envhost')
            self.assertTrue(P.public_config()['password_from_env'])

    def test_config_file_outside_repo(self):
        with mock.patch.dict(os.environ, {'QB_PG_CONFIG_FILE': ''}):
            p = P.config_path().resolve()
            self.assertFalse(str(p).startswith(str(Path(settings.BASE_DIR).resolve())))

    def test_page_never_renders_password(self):
        self.save()
        r = self.client.get(reverse('backup:pg_home'))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, SECRET)
        self.assertContains(r, 'type="password"')
        self.assertContains(r, 'Test connection')
        self.assertContains(r, P.CONFIRM_TO_PG)
        self.assertContains(r, P.CONFIRM_TO_SQLITE)
        self.assertContains(self.client.get(reverse('backup:home')), reverse('backup:pg_home'))

    def test_postgres_sync_linked_from_shared_nav_and_backups_home(self):
        """t72u: sync must be reachable from Shared menu, not only buried on Backups."""
        home = self.client.get(reverse('home'))
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, reverse('backup:pg_home'))
        self.assertContains(home, 'PostgreSQL sync')
        self.assertContains(home, reverse('backup:home'))
        backups = self.client.get(reverse('backup:home'))
        self.assertContains(backups, reverse('backup:pg_home'))
        self.assertContains(backups, 'Open PostgreSQL sync')

    def test_save_view_keeps_password(self):
        self.save()
        r = self.client.post(reverse('backup:pg_settings'), {'host': 'h2', 'port': '5433', 'name': 'qb', 'user': 'u',
                                                            'password': '', 'sslmode': 'require'})
        self.assertEqual(r.status_code, 302)
        cfg = P.load_config()
        self.assertEqual((cfg['host'], cfg['port'], cfg['sslmode'], cfg['password']), ('h2', '5433', 'require', SECRET))


class ConnectionErrorTests(_Cfg):
    def test_unreachable_server_reports_clean_error(self):
        self.save()
        with self.assertRaises(P.SyncError) as cm:
            P.test_connection()
        self.assertNotIn(SECRET, str(cm.exception))
        self.assertNotIn(P.PG_ALIAS, connections.settings)

    def test_test_view_shows_error_without_password(self):
        self.save()
        r = self.client.post(reverse('backup:pg_test'), follow=True)
        self.assertContains(r, 'Connection failed')
        self.assertNotContains(r, SECRET)

    def test_clean_masks_password(self):
        self.assertEqual(P._clean(f'bad {SECRET} x', {'password': SECRET}), 'bad ******** x')

    def test_missing_settings(self):
        with self.assertRaises(P.SyncError):
            P.pg_db_settings()


class ConfirmationTests(_Cfg):
    def test_wrong_phrase_does_nothing(self):
        self.save()
        with mock.patch.object(P, 'migrate_alias') as mig, mock.patch.object(P, '_build_sqlite_from_pg') as b:
            with self.assertRaises(P.SyncError):
                P.sync_sqlite_to_pg('yes')
            with self.assertRaises(P.SyncError):
                P.sync_pg_to_sqlite('yes', replace_live=True)
        mig.assert_not_called()
        b.assert_not_called()

    def test_view_requires_checkbox_and_phrase(self):
        self.save()
        with mock.patch.object(P, 'sync_sqlite_to_pg') as s1, mock.patch.object(P, 'sync_pg_to_sqlite') as s2:
            self.client.post(reverse('backup:pg_sync', args=['to-postgres']), {'phrase': P.CONFIRM_TO_PG})
            self.client.post(reverse('backup:pg_sync', args=['to-sqlite']), {'understood': 'on'})
            self.assertEqual(self.client.post(reverse('backup:pg_sync', args=['drop'])).status_code, 404)
        s1.assert_not_called()
        s2.assert_not_called()


def _mkdb(path, value):
    con = sqlite3.connect(str(path))
    con.execute('create table t (v text)')
    con.execute('insert into t values (?)', (value,))
    con.commit()
    con.close()


def _val(path):
    con = sqlite3.connect(str(path))
    try:
        return con.execute('select v from t').fetchone()[0]
    finally:
        con.close()


class ReplaceLiveTests(_Cfg):
    def setUp(self):
        super().setUp()
        self.save()
        self.live = Path(self.tmp.name) / 'live.sqlite3'
        _mkdb(self.live, 'old')
        self.bdir = Path(self.tmp.name) / 'bk'
        self.bdir.mkdir()
        self.order = []

        def build(target, cfg):
            _mkdb(target, 'from-pg')
            return [('t', 1)], []
        self.patches = [mock.patch.object(P, '_build_sqlite_from_pg', side_effect=build),
                        mock.patch.object(S, 'live_db_path', return_value=self.live),
                        mock.patch.object(S, 'backup_dir', return_value=self.bdir),
                        mock.patch.object(S, '_history')]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def test_clone_only_leaves_live_untouched(self):
        with mock.patch.object(S, 'create_backup') as cb:
            r = P.sync_pg_to_sqlite(P.CONFIRM_TO_SQLITE)
        cb.assert_not_called()
        self.assertEqual(_val(self.live), 'old')
        self.assertEqual(_val(r['clone_file']), 'from-pg')
        self.assertFalse(r['replaced_live'])

    def test_zip_backup_before_overwrite(self):
        def cb(**kw):
            self.order.append(('backup', _val(self.live), kw['kind'], kw['protected']))
            rec = mock.Mock()
            rec.name = 'qb-pre.zip'
            return rec
        with mock.patch.object(S, 'create_backup', side_effect=cb), \
                mock.patch.object(S, 'verify', return_value=(True, [])):
            r = P.sync_pg_to_sqlite(P.CONFIRM_TO_SQLITE, replace_live=True)
        self.assertEqual(self.order, [('backup', 'old', 'pre_restore', True)])
        self.assertTrue(r['replaced_live'])
        self.assertEqual(_val(self.live), 'from-pg')

    def test_failed_backup_aborts_overwrite(self):
        with mock.patch.object(S, 'create_backup', side_effect=S.BackupError('disk full')):
            with self.assertRaises(P.SyncError):
                P.sync_pg_to_sqlite(P.CONFIRM_TO_SQLITE, replace_live=True)
        self.assertEqual(_val(self.live), 'old')

    def test_unverified_backup_aborts_overwrite(self):
        with mock.patch.object(S, 'create_backup', return_value=mock.Mock()), \
                mock.patch.object(S, 'verify', return_value=(False, ['bad hash'])):
            with self.assertRaises(P.SyncError):
                P.sync_pg_to_sqlite(P.CONFIRM_TO_SQLITE, replace_live=True)
        self.assertEqual(_val(self.live), 'old')


class CloneEndToEndTests(TestCase):
    """SQLite -> (fresh SQLite via migrations) exercises the same migrate + copy path used for PostgreSQL."""

    def test_clone_copies_exact_rows_and_keeps_default(self):
        from accounting.models import JournalEntry  # noqa: F401 - ensure models are loaded
        from audit.models import AuditEvent
        from invoice_app.models import Client
        Client.objects.create(name='Clone Test Co', pin_code='560001')
        AuditEvent.objects.create(app_label='x', model='y', object_id='1', object_repr='r', action='create',
                                  changes={'a': [1, True]}, reason='t', request_path='', source='system')
        default_before = dict(settings.DATABASES['default'])
        before = {m._meta.db_table: m._base_manager.using('default').count() for m in P.copy_models()}
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'clone.sqlite3'
            allow = mock.patch.object(type(self), 'databases', frozenset({'default', P.SQLITE_ALIAS}))
            with allow, P.temp_alias(P.SQLITE_ALIAS, {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(target)}):
                P.migrate_alias(P.SQLITE_ALIAS)
                after_migrate = {m._meta.db_table: m._base_manager.using('default').count() for m in P.copy_models()}
                self.assertEqual(before, after_migrate, 'migrating the sync alias must not write to default')
                report, extra = P.clone('default', P.SQLITE_ALIAS)
                self.assertEqual(dict(report), before)
                c = Client.objects.using(P.SQLITE_ALIAS).get(name='Clone Test Co')
                self.assertEqual(c.pk, Client.objects.get(name='Clone Test Co').pk)
                ev = AuditEvent.objects.using(P.SQLITE_ALIAS).get(reason='t')
                src = AuditEvent.objects.get(reason='t')
                self.assertEqual((ev.changes, ev.timestamp), (src.changes, src.timestamp))
        self.assertNotIn(P.SQLITE_ALIAS, connections.settings)
        self.assertEqual(settings.DATABASES['default'], default_before)
        self.assertEqual(settings.DATABASES['default']['ENGINE'], 'django.db.backends.sqlite3')
        self.assertEqual(list(settings.DATABASES), ['default'])

    def test_live_setting_is_sqlite(self):
        self.assertEqual(settings.DATABASES['default']['ENGINE'], 'django.db.backends.sqlite3')
        self.assertEqual(P.pg_db_settings({'host': 'h', 'port': '5432', 'name': 'n', 'user': 'u', 'password': 'p',
                                           'sslmode': 'require'})['OPTIONS']['sslmode'], 'require')
