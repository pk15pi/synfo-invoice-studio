"""Stage 7: document index - read-only sync, private serving, sha256 / missing-file check."""
import hashlib
import os
import shutil
import tempfile
from datetime import date
from io import StringIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from employee_management.models import Department, Employee, EmployeeDocument

from . import services as S
from .models import Document, full_path, safe_rel_path

TMP = tempfile.mkdtemp(prefix='qb-doc-test-')
PDF = b'%PDF-1.4\n%test\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'


@override_settings(EMPLOYEE_PRIVATE_ROOT=TMP)
class DocTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        dept = Department.objects.create(name='Eng', code='ENG')
        self.emp = Employee.objects.create(first_name='Asha', last_name='R', department=dept, designation='Dev',
                                           date_of_joining=date(2024, 1, 1), status=Employee.CONFIRMED)
        self.ed = EmployeeDocument(employee=self.emp, document_type=EmployeeDocument._meta.get_field('document_type').choices[0][0],
                                   document_name='PAN', original_filename='pan.pdf', content_type='application/pdf',
                                   file_size=len(PDF))
        self.ed._validated_ext = 'pdf'
        self.ed.file.save('pan.pdf', ContentFile(PDF), save=False)
        self.ed.save()

    def test_sync_is_read_only_and_idempotent(self):
        path = full_path(self.ed.file.name)
        mtime = os.path.getmtime(path)
        s1 = S.sync_index()
        s2 = S.sync_index()
        self.assertEqual((s1['created'], s2['created'], s2['updated']), (1, 0, 1))
        d = Document.objects.get()
        self.assertEqual(d.file_path, self.ed.file.name)
        self.assertEqual(d.sha256, hashlib.sha256(PDF).hexdigest())
        self.assertEqual((d.size, d.sensitivity, d.linked), (len(PDF), 'confidential', self.emp))
        self.assertEqual(os.path.getmtime(path), mtime)
        self.assertTrue(os.path.isfile(path))
        out = StringIO()
        call_command('sync_documents', stdout=out)
        self.assertIn('Created 0', out.getvalue())

    def test_private_serving(self):
        S.sync_index()
        d = Document.objects.get()
        url = reverse('docs:serve', args=[d.pk])
        self.assertEqual(self.client.get(url, REMOTE_ADDR='192.168.1.20').status_code, 403)
        self.assertEqual(self.client.get(reverse('docs:list'), REMOTE_ADDR='10.0.0.1').status_code, 403)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertIn('attachment', r['Content-Disposition'])
        self.assertEqual(b''.join(r.streaming_content), PDF)
        self.assertIn('no-store', r['Cache-Control'])
        # no public URL: the file is not under static/media and storage has no base URL
        root = os.path.realpath(settings.EMPLOYEE_PRIVATE_ROOT)
        for pub in (settings.MEDIA_ROOT, getattr(settings, 'STATIC_ROOT', None)):
            if pub:
                pub_r = os.path.realpath(pub)
                if os.path.splitdrive(root)[0].lower() == os.path.splitdrive(pub_r)[0].lower():
                    self.assertNotEqual(os.path.commonpath([root, pub_r]), pub_r)
        self.assertNotContains(self.client.get(reverse('docs:list')), settings.MEDIA_URL + 'employees')

    def test_upload_company_doc_random_name(self):
        r = self.client.post(reverse('docs:upload'), {'category': 'gst_certificate', 'title': 'GST REG-06',
                                                      'sensitivity': 'normal', 'tags': 'gst',
                                                      'file': SimpleUploadedFile('my cert.pdf', PDF)})
        self.assertEqual(r.status_code, 302)
        d = Document.objects.get(source_type='company_doc')
        self.assertTrue(d.file_path.startswith('company_docs/'))
        self.assertNotIn('cert', d.file_path)
        self.assertTrue(os.path.isfile(os.path.join(TMP, *d.file_path.split('/'))))
        self.assertEqual(d.original_filename, 'my cert.pdf')
        with self.assertRaises(ValidationError):
            S.upload_company_doc(SimpleUploadedFile('x.pdf', b'MZ not a pdf'), 'agreement', 'x')
        with self.assertRaises(ValidationError):
            S.upload_company_doc(SimpleUploadedFile('x.pdf', PDF), 'employee', 'x')

    def test_path_must_stay_private(self):
        for bad in ('../db.sqlite3', '/etc/passwd', 'C:/x.pdf', 'a/../../b', ''):
            self.assertRaises(ValidationError, safe_rel_path, bad)
        self.assertEqual(safe_rel_path('company_docs\\a.pdf'), 'company_docs/a.pdf')

    def test_sha256_and_missing_check(self):
        S.sync_index()
        d = Document.objects.get()
        r = S.check_documents()
        self.assertEqual((r['ok'], r['missing'], r['changed']), (1, 0, 0))
        with open(full_path(d.file_path), 'ab') as f:
            f.write(b'tamper')
        r = S.check_documents()
        self.assertEqual(r['changed'], 1)
        os.remove(full_path(d.file_path))
        r = S.check_documents()
        self.assertEqual(r['missing'], 1)
        self.assertTrue(Document.objects.get().missing)
        self.assertEqual(self.client.get(reverse('docs:serve', args=[d.pk])).status_code, 404)
        out = StringIO()
        call_command('check_documents', stdout=out)
        self.assertIn('MISSING', out.getvalue())
        self.assertEqual(self.client.get(reverse('docs:list') + '?state=missing&q=PAN').status_code, 200)
        self.assertEqual(self.client.get(reverse('docs:check')).status_code, 200)
