"""Stage 7: a document INDEX. It never moves, copies or deletes files.

Every indexed file lives inside the private root (settings.EMPLOYEE_PRIVATE_ROOT, i.e. private_media) and is only
served through the localhost-only view. file_path is stored relative to that root and validated to stay inside it."""
import hashlib
import os
from pathlib import PurePosixPath

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models

from employee_management.storage import private_storage


def safe_rel_path(path):
    """Normalise a path relative to the private root; reject absolute paths and any escape from the root."""
    p = (path or '').replace('\\', '/').strip()
    if not p or p.startswith('/') or (len(p) > 1 and p[1] == ':'):
        raise ValidationError('File path must be relative to the private document root.')
    parts = PurePosixPath(p).parts
    if any(x in ('..', '') for x in parts):
        raise ValidationError('File path may not leave the private document root.')
    root = os.path.realpath(private_storage.location)
    full = os.path.realpath(os.path.join(root, *parts))
    if os.path.commonpath([root, full]) != root:
        raise ValidationError('File path may not leave the private document root.')
    return '/'.join(parts)


def full_path(rel):
    return os.path.join(os.path.realpath(private_storage.location), *safe_rel_path(rel).split('/'))


def sha256_of(rel):
    h = hashlib.sha256()
    with open(full_path(rel), 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


class Document(models.Model):
    CATEGORIES = [('employee', 'Employee document'), ('expense', 'Expense attachment'),
                  ('claim', 'Expense claim attachment'), ('registration', 'Company registration'),
                  ('agreement', 'Agreement / contract'), ('gst_certificate', 'GST certificate'),
                  ('bank_statement', 'Bank statement'), ('asset', 'Asset / handover receipt'),
                  ('other', 'Other company document')]
    COMPANY_CATEGORIES = ['registration', 'agreement', 'gst_certificate', 'bank_statement', 'other']
    # Rows written by other modules; sync_index must not flag them as orphaned sources.
    SELF_MANAGED_SOURCE_TYPES = ('company_doc', 'asset_receipt', 'asset_handover_receipt')
    NORMAL, CONFIDENTIAL = 'normal', 'confidential'
    SENSITIVITY = [(NORMAL, 'Normal'), (CONFIDENTIAL, 'Confidential')]

    category = models.CharField(max_length=20, choices=CATEGORIES, db_index=True)
    title = models.CharField(max_length=200)
    content_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL)
    object_id = models.CharField(max_length=40, blank=True)
    linked = GenericForeignKey('content_type', 'object_id')
    source_type = models.CharField(max_length=40, db_index=True, help_text='e.g. employee_document, company_doc')
    source_id = models.CharField(max_length=40)
    file_path = models.CharField(max_length=255, help_text='Relative to the private document root.')
    original_filename = models.CharField(max_length=255, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)
    sensitivity = models.CharField(max_length=12, choices=SENSITIVITY, default=NORMAL)
    tags = models.CharField(max_length=240, blank=True, help_text='Comma-separated.')
    uploaded_at = models.DateTimeField(null=True, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    missing = models.BooleanField(default=False, db_index=True)
    last_checked = models.DateTimeField(null=True, blank=True)
    check_note = models.CharField(max_length=200, blank=True)
    indexed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-uploaded_at', '-id']
        constraints = [models.UniqueConstraint(fields=['source_type', 'source_id'], name='document_unique_source')]

    def __str__(self):
        return self.title

    def clean(self):
        self.file_path = safe_rel_path(self.file_path)

    def save(self, *a, **kw):
        self.file_path = safe_rel_path(self.file_path)
        super().save(*a, **kw)

    @property
    def tag_list(self):
        return [t.strip() for t in self.tags.split(',') if t.strip()]

    def exists(self):
        try:
            return os.path.isfile(full_path(self.file_path))
        except ValidationError:
            return False
