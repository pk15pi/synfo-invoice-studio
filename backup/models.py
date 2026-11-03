import re

from django.db import models

PROTECTED_NAME = re.compile(r'pre[_\- ]?(accounting|stage|restore|migrat)', re.I)


class BackupRecord(models.Model):
    MANUAL, AUTO, PRE_RESTORE, PRE_MIGRATION = 'manual', 'auto', 'pre_restore', 'pre_migration'
    KINDS = [(MANUAL, 'Manual'), (AUTO, 'Automatic'), (PRE_RESTORE, 'Pre-restore'), (PRE_MIGRATION, 'Pre-migration')]
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
        return self.protected or self.kind in (self.PRE_RESTORE, self.PRE_MIGRATION) or bool(PROTECTED_NAME.search(self.name))

    @property
    def confirm_phrase(self):
        return f'RESTORE {self.name}'

    def __str__(self):
        return self.name
