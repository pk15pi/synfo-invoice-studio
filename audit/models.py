"""Append-only audit log. There is no login in this app, so events never name a person."""
from django.core.exceptions import PermissionDenied
from django.db import models

OPERATOR_TEXT = 'Local operator (no login)'


class AppendOnlyError(PermissionDenied):
    pass


class AuditQuerySet(models.QuerySet):
    def update(self, **kw):
        raise AppendOnlyError('The audit log is append-only; events cannot be updated.')

    def delete(self):
        raise AppendOnlyError('The audit log is append-only; events cannot be deleted.')

    def bulk_update(self, *a, **kw):
        raise AppendOnlyError('The audit log is append-only; events cannot be updated.')


class AuditEvent(models.Model):
    ACTIONS = [(a, a.title()) for a in ('create', 'update', 'delete', 'post', 'reverse', 'finalize', 'cancel',
                                        'close', 'reopen', 'restore', 'import', 'backup')]
    SOURCES = [('ui', 'UI'), ('command', 'Command'), ('system', 'System')]
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    app_label = models.CharField(max_length=60, db_index=True)
    model = models.CharField(max_length=80, db_index=True)
    object_id = models.CharField(max_length=60, db_index=True, blank=True)
    object_repr = models.CharField(max_length=300, blank=True)
    action = models.CharField(max_length=10, choices=ACTIONS, db_index=True)
    changes = models.JSONField(default=dict, blank=True, help_text='{field: [old, new]} with sensitive values masked')
    reason = models.CharField(max_length=500, blank=True)
    request_path = models.CharField(max_length=300, blank=True)
    source = models.CharField(max_length=8, choices=SOURCES, default='system', db_index=True)

    objects = AuditQuerySet.as_manager()

    class Meta:
        ordering = ['-timestamp', '-id']
        indexes = [models.Index(fields=['app_label', 'model', 'object_id'], name='audit_obj_idx')]

    @property
    def operator(self):
        return OPERATOR_TEXT

    def save(self, *a, **kw):
        if not self._state.adding or (self.pk and AuditEvent.objects.filter(pk=self.pk).exists()):
            raise AppendOnlyError('The audit log is append-only; events cannot be updated.')
        return super().save(*a, **kw)

    def delete(self, *a, **kw):
        raise AppendOnlyError('The audit log is append-only; events cannot be deleted.')

    def __str__(self):
        return f'{self.timestamp:%d-%m-%Y %H:%M} {self.action} {self.app_label}.{self.model}#{self.object_id}'
