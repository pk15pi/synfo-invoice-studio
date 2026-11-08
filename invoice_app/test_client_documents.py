"""Customer NDA / MCA documents and CIN/LLPIN, NDA dates."""
import shutil
import tempfile
from datetime import date

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import ClientForm
from .models import Client, ClientDocument

PDF = b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 40
TMP = tempfile.mkdtemp(prefix='qb-client-docs-')


@override_settings(EMPLOYEE_PRIVATE_ROOT=TMP)
class ClientDocumentTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        from employee_management.storage import private_storage
        private_storage.__dict__.pop('base_location', None)
        private_storage.__dict__.pop('location', None)
        self.client_obj = Client.objects.create(name='Acme Docs Ltd', country_ref_id=__import__('invoice_app.models', fromlist=['Country']).Country.objects.get(iso2='US').pk)

    def post_data(self, **extra):
        init = ClientForm(instance=self.client_obj).initial
        data = {k: ('' if v is None else v) for k, v in init.items() if not isinstance(v, bool) or v}
        data.pop('id', None)
        data.update(extra)
        return data

    def edit(self, files=None, **extra):
        data = self.post_data(**extra)
        data.update(files or {})
        return self.client.post(reverse('client_edit', args=[self.client_obj.pk]), data)

    def test_fields_on_add_page(self):
        html = self.client.get(reverse('client_add')).content.decode()
        for name in ('cin_llpin', 'nda_signed_date', 'nda_expiry_date', 'nda_file', 'mca_file'):
            self.assertIn(f'name="{name}"', html)
        self.assertIn('multipart/form-data', html)

    def test_upload_dates_cin_and_serving(self):
        r = self.edit({'nda_file': SimpleUploadedFile('nda.pdf', PDF), 'mca_file': SimpleUploadedFile('mca.png', PNG)},
                      cin_llpin='U72900HR2020PTC000001', nda_signed_date='2026-04-01', nda_expiry_date='2028-03-31')
        self.assertEqual(r.status_code, 302, getattr(r, 'context', None) and r.context['form'].errors)
        c = Client.objects.get(pk=self.client_obj.pk)
        self.assertEqual(c.cin_llpin, 'U72900HR2020PTC000001')
        self.assertEqual(c.nda_signed_date, date(2026, 4, 1))
        self.assertEqual(c.nda_expiry_date, date(2028, 3, 31))
        nda, mca = c.nda_document, c.mca_document
        self.assertEqual(nda.content_type, 'application/pdf')
        self.assertEqual(mca.content_type, 'image/png')
        self.assertTrue(nda.file.name.startswith(f'clients/{c.pk}/documents/'))
        resp = self.client.get(reverse('client_document', args=[nda.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b''.join(resp.streaming_content), PDF)
        detail = self.client.get(reverse('client_detail', args=[c.pk])).content.decode()
        self.assertIn(reverse('client_document', args=[nda.pk]), detail)
        edit = self.client.get(reverse('client_edit', args=[c.pk])).content.decode()
        self.assertIn(reverse('client_document', args=[mca.pk]), edit)

    def test_replace_keeps_history(self):
        self.edit({'nda_file': SimpleUploadedFile('v1.pdf', PDF)})
        self.edit({'nda_file': SimpleUploadedFile('v2.pdf', PDF)})
        docs = ClientDocument.objects.filter(client=self.client_obj, kind='nda')
        self.assertEqual(docs.count(), 2)
        self.assertEqual(docs.filter(is_current=True).get().original_filename, 'v2.pdf')
        old = docs.get(is_current=False)
        self.assertTrue(old.file.storage.exists(old.file.name))

    def test_rejects_fake_or_wrong_type(self):
        r = self.edit({'nda_file': SimpleUploadedFile('nda.pdf', b'not a pdf at all')})
        self.assertEqual(r.status_code, 200)
        r = self.edit({'mca_file': SimpleUploadedFile('mca.exe', b'MZ\x90\x00')})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(ClientDocument.objects.exists())

    def test_expiry_before_signed_rejected(self):
        r = self.edit(nda_signed_date='2026-04-01', nda_expiry_date='2026-01-01')
        self.assertEqual(r.status_code, 200)
        self.assertIn('nda_expiry_date', r.context['form'].errors)

    def test_save_without_docs_still_works(self):
        r = self.edit(notes='plain save')
        self.assertEqual(r.status_code, 302)
        self.assertFalse(ClientDocument.objects.exists())

    def test_serving_is_local_only(self):
        self.edit({'nda_file': SimpleUploadedFile('nda.pdf', PDF)})
        doc = ClientDocument.objects.get()
        r = self.client.get(reverse('client_document', args=[doc.pk]), REMOTE_ADDR='203.0.113.5')
        self.assertNotEqual(r.status_code, 200)