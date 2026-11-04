import json
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounting.models import Account, FinancialPeriod, FinancialYear
from audit.models import AuditEvent
from document_management.models import Document

from . import services as S
from . import consolidation as C
from .models import BackupRecord, RecordOrigin


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


class ConsolidationTests(BackupBase):
    def make_package(self, records, source_id=None):
        source_id = source_id or uuid.uuid4()
        export_id = uuid.uuid4()
        normalized = []
        for record in records:
            identity = record['identity']
            fields = record['fields']
            m2m = record.get('m2m', {})
            file_hashes = record.get('file_hashes', {})
            normalized.append({
                'identity': identity, 'fields': fields, 'm2m': m2m, 'file_hashes': file_hashes,
                'source_hash': C._sha256(C._json_bytes({
                    'identity': identity, 'fields': fields, 'm2m': m2m, 'file_hashes': file_hashes,
                })),
            })
        payload = {'records': normalized, 'files': []}
        data_bytes = C._json_bytes(payload)
        entity_counts = {}
        for item in normalized:
            label = item['identity']['model']
            entity_counts[label] = entity_counts.get(label, 0) + 1
        report_bytes = C._json_bytes({
            'completion_status': 'complete', 'entity_counts': entity_counts,
            'total_records': len(normalized), 'file_count': 0,
        })
        manifest = {
            'format_version': C.FORMAT_VERSION, 'source_instance_id': str(source_id),
            'source_instance_name': 'Test source', 'app_version': S.app_version(),
            'schema_migrations': C._migrations(), 'export_id': str(export_id),
            'exported_at': timezone.now().isoformat(), 'export_type': 'merge',
            'completion_status': 'complete', 'entity_counts': entity_counts, 'total_records': len(normalized),
            'file_count': 0, 'total_attachment_bytes': 0,
        }
        entries = {C.DATA: data_bytes, C.REPORT: report_bytes}
        checksum_bytes = C._json_bytes({name: C._sha256(content) for name, content in entries.items()})
        path = self.tmp / f'{export_id}.zip'
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(C.MANIFEST, C._json_bytes(manifest))
            archive.writestr(C.CHECKSUMS, checksum_bytes)
            for name, content in entries.items():
                archive.writestr(name, content)
        return path, source_id, export_id

    def add_preview(self, path):
        return C.create_import_preview(SimpleUploadedFile(path.name, path.read_bytes(), content_type='application/zip'))

    @staticmethod
    def record(label, source_id, pk, fields, m2m=None):
        identity = {'model': label, 'instance_id': str(source_id), 'record_id': str(pk)}
        return {'identity': identity, 'fields': fields, 'm2m': m2m or {}, 'file_hashes': {}}

    def test_export_contains_manifest_and_persists_instance_identity(self):
        path, filename = C.export_package()
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read(C.MANIFEST))
        identity = C.InstanceIdentity.current()
        self.assertIn('invoiceStudio_', filename)
        self.assertEqual(manifest['format_version'], C.FORMAT_VERSION)
        self.assertEqual(manifest['source_instance_id'], str(identity.instance_id))
        self.assertEqual(manifest['completion_status'], 'complete')
        self.assertEqual(identity.last_export_id, uuid.UUID(manifest['export_id']))
        self.assertTrue(C._read_package(path)['payload']['records'])

    def test_export_includes_private_document_index_files(self):
        source_id = uuid.uuid4().hex
        Document.objects.create(
            category='other', title='Company policy', source_type='company_doc', source_id=source_id,
            file_path='employees/a.pdf', original_filename='policy.pdf', mime_type='application/pdf',
            size=Path(self.tmp / 'private_media' / 'employees' / 'a.pdf').stat().st_size,
        )
        path, _ = C.export_package()
        parsed = C._read_package(path)
        document_file = next(
            item for item in parsed['payload']['files']
            if item['model'] == 'document_management.document' and item['field'] == 'file_path'
        )
        self.assertEqual(document_file['storage'], 'private')
        self.assertEqual(parsed['content_by_name'][document_file['archive_path']], b'%PDF-1.4 test')

    def test_import_remaps_ids_and_preserves_foreign_key_relationships(self):
        source_id = uuid.uuid4()
        period = self.record('accounting.financialperiod', source_id, 701, {
            'financial_year': {'ref': {'model': 'accounting.financialyear', 'instance_id': str(source_id),
                                       'record_id': '601'}},
            'year': 2097, 'month': 4, 'start_date': '2097-04-01', 'end_date': '2097-04-30',
            'status': 'open', 'closed_at': None, 'close_reason': '', 'reopened_at': None, 'reopen_reason': '',
        })
        year = self.record('accounting.financialyear', source_id, 601, {
            'start_year': 2097, 'start_date': '2097-04-01', 'end_date': '2098-03-31',
        })
        path, _, _ = self.make_package([period, year], source_id)
        package = self.add_preview(path)
        self.assertEqual(package.preview['new_records'], 2)
        C.merge_package(package)
        pre_merge = BackupRecord.objects.get(kind=BackupRecord.PRE_MERGE)
        self.assertTrue(pre_merge.is_protected)
        self.assertTrue(pre_merge.verify_ok)
        imported_year_origin = RecordOrigin.objects.get(
            origin_instance_id=source_id, model_label='accounting.financialyear', origin_record_id='601')
        imported_period_origin = RecordOrigin.objects.get(
            origin_instance_id=source_id, model_label='accounting.financialperiod', origin_record_id='701')
        self.assertNotEqual(imported_year_origin.destination_pk, '601')
        self.assertNotEqual(imported_period_origin.destination_pk, '701')
        imported_period = FinancialPeriod.objects.get(pk=imported_period_origin.destination_pk)
        self.assertEqual(imported_period.financial_year_id, int(imported_year_origin.destination_pk))
        self.assertEqual(FinancialYear.objects.get(pk=imported_year_origin.destination_pk).start_year, 2097)

    def test_changed_source_requires_explicit_keep_destination_decision(self):
        source_id = uuid.uuid4()
        record = self.record('audit.auditevent', source_id, 901, {
            'timestamp': timezone.now(), 'app_label': 'test', 'model': 'Record', 'object_id': '901',
            'object_repr': 'original', 'action': 'create', 'changes': {}, 'reason': '',
            'request_path': '', 'source': 'system',
        })
        first_path, _, _ = self.make_package([record], source_id)
        first = self.add_preview(first_path)
        C.merge_package(first)
        original_origin = RecordOrigin.objects.get(
            origin_instance_id=source_id, model_label='audit.auditevent', origin_record_id='901')
        stored = AuditEvent.objects.get(pk=original_origin.destination_pk)

        updated_record = {**record, 'fields': {**record['fields'], 'object_repr': 'changed on source'}}
        second_path, _, _ = self.make_package([updated_record], source_id)
        second = self.add_preview(second_path)
        self.assertEqual(second.preview['changed_source_records'], 1)
        self.assertIn('object_repr', second.preview['source_conflicts'][0]['source_changed_fields'])
        self.assertTrue(second.preview['source_conflicts'][0]['destination_hash'])
        with self.assertRaises(C.ConsolidationError):
            C.merge_package(second)
        C.merge_package(second, keep_destination=True)
        stored.refresh_from_db()
        self.assertEqual(stored.object_repr, 'original')

    def test_invalid_archive_is_rejected_without_creating_import_record(self):
        bad_path = self.tmp / 'not-an-export.zip'
        bad_path.write_bytes(b'not a zip')
        with self.assertRaises(C.ConsolidationError):
            self.add_preview(bad_path)
        self.assertFalse(C.MergePackage.objects.exists())


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
