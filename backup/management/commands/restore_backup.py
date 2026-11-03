from django.core.management.base import BaseCommand, CommandError

from audit.context import audit_context
from backup import services as S
from backup.models import BackupRecord


class Command(BaseCommand):
    help = 'Restore a verified backup over the live DB. Stop runserver first. Requires --confirm "RESTORE <name>".'

    def add_arguments(self, p):
        p.add_argument('name')
        p.add_argument('--confirm', required=True)

    def handle(self, *a, name, confirm, **o):
        S.rescan()
        rec = BackupRecord.objects.filter(name=name).first()
        if not rec:
            raise CommandError('Backup not found.')
        with audit_context(source='command', path='manage.py restore_backup'):
            try:
                r = S.restore(rec, confirm)
            except S.BackupError as e:
                raise CommandError(f'Restore refused - nothing changed. {e}')
        self.stdout.write(f'Restored {name} into {r["target"]}. Pre-restore backup: {r["pre_restore"]}.')
        if r['pending_migrations']:
            apps = sorted({m.split('.')[0] for m in r['pending_migrations']})
            self.stdout.write('Backup is older than the code; migrate these apps (one by one): ' + ', '.join(apps))
        self.stdout.write('Restart runserver before using the app.')
