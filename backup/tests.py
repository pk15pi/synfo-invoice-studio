import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse

from accounting.models import Account

from . import services as S
from .models import BackupRecord


class BackupBase(TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='qbbk-'))
        priv, med = self.tmp / 'private_media', self.tmp / 'media'
        (priv / 'employees').mkdir(parents=True)
        (priv / 'employees' / 'a.pdf').write_bytes(b'%PDF-1.4 test')
        med.mkdir()
        self.ov = override_settings(BACKUP_DIR=self.tmp / 'bk', EMPLOYEE_PRIVATE_ROOT=priv, MEDIA_ROOT=med,
                                    BACKUP_KEEP_LAST=2)
        self.ov.enable()

    def tearDown(self):
        self.ov.disable()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def temp_db(self):
        p = self.tmp / 'target.sqlite3'
        S._snapshot_db(p)
        return p


class CreateVerifyTests(BackupBase):
    def test_manifest_hashes(self):
        rec = S.create_backup(note='t')
        z = zipfile.ZipFile(rec.path)
        m = json.loads(z.read('manifest.json'))
        self.assertEqual(set(m['files']), {'db.sqlite3', 'private_media/employees/a.pdf'})
        import hashlib
        for an, meta in m['files'].items():
            self.assertEqual(hashlib.sha256(z.read(an)).hexdigest(), meta['sha256'])
        self.assertIn('accounting.0001_initial', m['migrations'])
        self.assertEqual(m['record_counts']['accounting.Account'], Account.objects.count())
        self.assertEqual(m['app_version'], S.app_version())
        self.assertEqual(m['integrity_check'], 'ok')
        self.assertTrue(S.verify(rec)[0])

    def test_verify_detects_tampering(self):
        rec = S.create_backup()
        src = zipfile.ZipFile(rec.path)
        items = {i: src.read(i) for i in src.namelist()}
        src.close()
        items['private_media/employees/a.pdf'] = b'%PDF-1.4 TAMPERED'
        with zipfile.ZipFile(rec.path, 'w') as z:
            for k, v in items.items():
                z.writestr(k, v)
        ok, problems = S.verify(rec)
        self.assertFalse(ok)
        self.assertTrue(any('Hash mismatch: private_media/employees/a.pdf' in p for p in problems))
        rec.refresh_from_db()
        self.assertIs(rec.verify_ok, False)

    def test_backup_dir_inside_repo_refused(self):
        from django.conf import settings
        with override_settings(BACKUP_DIR=Path(settings.BASE_DIR) / 'backups_x'):
            with self.assertRaises(S.BackupError):
                S.create_backup()


class RestoreTests(BackupBase):
    def test_wrong_phrase_rejected(self):
        rec = S.create_backup()
        target = self.temp_db()
        before = S.sha256_file(target)
        for phrase in ('', 'RESTORE', f'restore {rec.name}', f'RESTORE {rec.name}x'):
            with self.assertRaises(S.BackupError):
                S.restore(rec, phrase, target_db=target)
        self.assertEqual(S.sha256_file(target), before)
        self.assertFalse(BackupRecord.objects.filter(kind='pre_restore').exists())

    def test_restore_to_temp_db(self):
        rec = S.create_backup()
        target = self.temp_db()
        con = sqlite3.connect(target)
        con.execute('delete from accounting_accountmapping')
        con.commit()
        con.close()
        r = S.restore(rec, f'RESTORE {rec.name}', target_db=target)
        pre = BackupRecord.objects.get(name=r['pre_restore'])
        self.assertEqual(pre.kind, 'pre_restore')
        self.assertTrue(pre.is_protected and Path(pre.path).exists() and pre.verify_ok)
        con = sqlite3.connect(target)
        self.assertEqual(con.execute('pragma integrity_check').fetchone()[0], 'ok')
        self.assertEqual(con.execute('select count(*) from accounting_accountmapping').fetchone()[0],
                         rec.manifest['record_counts']['accounting.AccountMapping'])
        self.assertEqual(con.execute("select count(*) from audit_auditevent where action='restore'").fetchone()[0], 1)
        con.close()
        self.assertEqual(r['pending_migrations'], [])

    def test_unverifiable_backup_refused(self):
        rec = S.create_backup()
        Path(rec.path).write_bytes(b'not a zip')
        with self.assertRaises(S.BackupError):
            S.restore(rec, f'RESTORE {rec.name}', target_db=self.temp_db())

    def test_newer_backup_refused(self):
        rec = S.create_backup()
        orig = S.migration_compat
        S.migration_compat = lambda m: (['futureapp.0001_initial'], [])
        try:
            with self.assertRaises(S.BackupError):
                S.restore(rec, f'RESTORE {rec.name}', target_db=self.temp_db())
        finally:
            S.migration_compat = orig


class RetentionTests(BackupBase):
    def test_keeps_protected(self):
        made = [S.create_backup(kind='auto') for _ in range(4)]
        prot = S.create_backup(kind='manual', protected=True)
        stage = S.create_backup(kind='manual')
        BackupRecord.objects.filter(pk=stage.pk).update(name='qb-manual-pre-stage9-x')
        pre = S.create_backup(kind='pre_restore')
        outside = BackupRecord.objects.create(name='pre_accounting_20261103_073941', kind='manual',
                                              path=str(self.tmp / 'pre_accounting_20261103_073941'),
                                              created_at=made[0].created_at)
        removed = S.apply_retention()
        self.assertEqual(sorted(removed), sorted(r.name for r in made[:2]))
        for r in (prot, pre, made[2], made[3]):
            self.assertTrue(Path(r.path).exists())
        stage.refresh_from_db()
        self.assertIsNone(stage.deleted_at)
        self.assertTrue(Path(stage.path).exists())
        outside.refresh_from_db()
        self.assertIsNone(outside.deleted_at)
        self.assertEqual(S.apply_retention(), [])


class UiTests(BackupBase):
    def test_pages_and_wrong_phrase(self):
        self.assertEqual(self.client.post(reverse('backup:create'), {'note': 'ui'}).status_code, 302)
        rec = BackupRecord.objects.get(note='ui')
        self.assertTrue(rec.verify_ok)
        self.assertContains(self.client.get(reverse('backup:home')), rec.name)
        r = self.client.post(reverse('backup:detail', args=[rec.pk]), {'phrase': 'RESTORE nope', 'understood': 'on'},
                             follow=True)
        self.assertContains(r, 'Restore refused')
        self.assertEqual(self.client.get(reverse('backup:home'), REMOTE_ADDR='10.1.1.1').status_code, 403)
