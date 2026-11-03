from django.core.management.base import BaseCommand

from backup import services as S


class Command(BaseCommand):
    help = 'Delete app-created manual/auto backups beyond BACKUP_KEEP_LAST. Protected/pre-stage backups are kept.'

    def add_arguments(self, p):
        p.add_argument('--keep', type=int)
        p.add_argument('--dry-run', action='store_true')

    def handle(self, *a, keep=None, dry_run=False, **o):
        removed = S.apply_retention(keep, dry_run=dry_run)
        self.stdout.write(('Would remove: ' if dry_run else 'Removed: ') + (', '.join(removed) or 'nothing'))
