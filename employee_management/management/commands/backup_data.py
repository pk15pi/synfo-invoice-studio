"""Zip the SQLite database, private employee files and media into backups/."""
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone


class Command(BaseCommand):
    help = 'Back up db.sqlite3, private_media/ and media/ into backups/backup-<timestamp>.zip'

    def handle(self, *a, **o):
        base = Path(settings.BASE_DIR)
        out = base / 'backups'
        out.mkdir(exist_ok=True)
        target = out / f'backup-{timezone.localtime():%Y%m%d-%H%M%S}.zip'
        db = Path(settings.DATABASES['default']['NAME'])
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
            snap = Path(tmp) / db.name
            connection.ensure_connection()
            dst = sqlite3.connect(snap)
            connection.connection.backup(dst)  # consistent online copy
            dst.close()
            z.write(snap, db.name)
            for folder in (Path(settings.EMPLOYEE_PRIVATE_ROOT), Path(settings.MEDIA_ROOT)):
                if folder.exists():
                    for f in folder.rglob('*'):
                        if f.is_file():
                            z.write(f, Path(folder.name) / f.relative_to(folder))
        self.stdout.write(f'Backup written: {target}')
