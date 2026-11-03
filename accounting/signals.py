"""pre_delete guards so queryset/cascade deletes cannot remove posted entries or used accounts."""
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .models import Account, ImmutableError, JournalEntry, JournalLine


@receiver(pre_delete, sender=JournalEntry)
def _guard_entry(sender, instance, **kw):
    if JournalEntry.objects.filter(pk=instance.pk).exclude(status=JournalEntry.DRAFT).exists():
        raise ImmutableError(f'Journal entry {instance} is posted and cannot be deleted.')


@receiver(pre_delete, sender=JournalLine)
def _guard_line(sender, instance, **kw):
    if JournalEntry.objects.filter(pk=instance.entry_id).exclude(status=JournalEntry.DRAFT).exists():
        raise ImmutableError('Lines of a posted journal entry cannot be deleted.')


@receiver(pre_delete, sender=Account)
def _guard_account(sender, instance, **kw):
    if instance.is_system or JournalLine.objects.filter(account_id=instance.pk).exists():
        raise ImmutableError('Used or system accounts cannot be deleted; deactivate instead.')
