from django.core.management.base import BaseCommand, CommandError

from backup import services as S
from backup.models import BackupRecord


class Command(BaseCommand):
    help = 'Re-hash a backup ZIP against its manifest and run integrity_check on the extracted DB.'

    def add_arguments(self, p):
        p.add_argument('name', nargs='?', help='Backup name (default: latest)')

    def handle(self, *a, name=None, **o):
        S.rescan()
        rec = BackupRecord.objects.filter(name=name).first() if name else BackupRecord.objects.filter(deleted_at__isnull=True).first()
        if not rec:
            raise CommandError('Backup not found.')
        ok, problems = S.verify(rec)
        self.stdout.write(f'{rec.name}: ' + ('OK' if ok else 'FAILED\n  ' + '\n  '.join(problems)))
        if not ok:
            raise CommandError('Verification failed.')
