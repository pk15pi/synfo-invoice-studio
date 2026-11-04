import re
import uuid

from django.conf import settings
from django.db import models

PROTECTED_NAME = re.compile(r'pre[_\- ]?(accounting|stage|restore|migrat)', re.I)


class BackupRecord(models.Model):
    MANUAL, AUTO, PRE_RESTORE, PRE_MIGRATION, PRE_MERGE = (
        'manual', 'auto', 'pre_restore', 'pre_migration', 'pre_merge')
    KINDS = [(MANUAL, 'Manual'), (AUTO, 'Automatic'), (PRE_RESTORE, 'Pre-restore'),
             (PRE_MIGRATION, 'Pre-migration'), (PRE_MERGE, 'Pre-merge')]
    name = models.CharField(max_length=120, unique=True)
    kind = models.CharField(max_length=14, choices=KINDS, default=MANUAL)
    path = models.CharField(max_length=500)
    created_at = models.DateTimeField()
    size = models.BigIntegerField(default=0)
    zip_sha256 = models.CharField(max_length=64, blank=True)
    manifest = models.JSONField(default=dict, blank=True)
    protected = models.BooleanField(default=False, help_text='Never removed by the retention rule.')
    note = models.CharField(max_length=300, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verify_ok = models.BooleanField(null=True)
    verify_notes = models.TextField(blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True, help_text='Set when retention removed the file.')

    class Meta:
        ordering = ['-created_at', '-id']

    @property
    def is_protected(self):
        return self.protected or self.kind in (self.PRE_RESTORE, self.PRE_MIGRATION, self.PRE_MERGE) or bool(PROTECTED_NAME.search(self.name))

    @property
    def confirm_phrase(self):
        return f'RESTORE {self.name}'

    def __str__(self):
        return self.name


class InstanceIdentity(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    instance_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=120, default='Invoice Studio')
    created_at = models.DateTimeField(auto_now_add=True)
    last_export_at = models.DateTimeField(null=True, blank=True)
    last_export_id = models.UUIDField(null=True, blank=True)
    last_import_at = models.DateTimeField(null=True, blank=True)
    last_import_id = models.UUIDField(null=True, blank=True)

    @classmethod
    def current(cls):
        configured_name = getattr(settings, 'QB_INSTANCE_NAME', 'Invoice Studio')[:120]
        identity, created = cls.objects.get_or_create(id=1, defaults={'name': configured_name})
        if not created and configured_name != 'Invoice Studio' and identity.name == 'Invoice Studio':
            identity.name = configured_name
            identity.save(update_fields=['name'])
        return identity

    def __str__(self):
        return f'{self.name} ({self.instance_id})'


class RecordOrigin(models.Model):
    origin_instance_id = models.UUIDField()
    model_label = models.CharField(max_length=100)
    origin_record_id = models.CharField(max_length=100)
    destination_pk = models.CharField(max_length=100)
    source_hash = models.CharField(max_length=64)
    source_snapshot = models.JSONField(default=dict)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['origin_instance_id', 'model_label', 'origin_record_id'],
                                    name='backup_origin_record_unique'),
            models.UniqueConstraint(fields=['model_label', 'destination_pk'], name='backup_destination_record_unique'),
        ]


class MergePackage(models.Model):
    PREVIEW, MERGED = 'preview', 'merged'
    STATUSES = [(PREVIEW, 'Preview'), (MERGED, 'Merged')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_instance_id = models.UUIDField()
    export_id = models.UUIDField()
    source_instance_name = models.CharField(max_length=120)
    uploaded_name = models.CharField(max_length=255)
    path = models.CharField(max_length=500)
    manifest = models.JSONField(default=dict)
    preview = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=STATUSES, default=PREVIEW)
    created_at = models.DateTimeField(auto_now_add=True)
    imported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['source_instance_id', 'export_id'], name='backup_merge_export_unique'),
        ]
        ordering = ['-created_at']
