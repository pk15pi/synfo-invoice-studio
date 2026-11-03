from django.core.management.base import BaseCommand, CommandError

from audit.context import audit_context
from backup import services as S


class Command(BaseCommand):
    help = 'Consistent backup (sqlite backup API + private_media + media + manifest) into BACKUP_DIR.'

    def add_arguments(self, p):
        p.add_argument('--kind', default='manual', choices=['manual', 'auto', 'pre_migration'])
        p.add_argument('--note', default='')
        p.add_argument('--verify', action='store_true', help='Verify the ZIP right after writing it.')
        p.add_argument('--retention', action='store_true', help='Then apply the keep-last-N rule.')

    def handle(self, *a, kind, note, verify, retention, **o):
        with audit_context(source='command', path='manage.py create_backup', reason=note):
            try:
                rec = S.create_backup(kind=kind, note=note, protected=(kind == 'pre_migration'))
            except S.BackupError as e:
                raise CommandError(str(e))
            self.stdout.write(f'Backup written: {rec.path} ({rec.size} bytes, {rec.manifest.get("file_count")} files)')
            if verify:
                ok, problems = S.verify(rec)
                self.stdout.write('Verify: OK' if ok else 'Verify FAILED:\n  ' + '\n  '.join(problems))
                if not ok:
                    raise CommandError('Verification failed.')
            if retention:
                removed = S.apply_retention()
                self.stdout.write(f'Retention removed: {", ".join(removed) or "nothing"}')
