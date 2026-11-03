"""Consistent backups (sqlite backup API + private_media + media) with manifest, verify, retention and a
safe restore (temp file + atomic replace). Nothing here deletes protected or non-app backups."""
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings
from django.db import connection, connections
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from .models import BackupRecord

MANIFEST = 'manifest.json'
DB_ARC = 'db.sqlite3'
HISTORY = 'backup-history.jsonl'


class BackupError(Exception):
    pass


def app_version():
    return getattr(settings, 'QB_APP_VERSION', 'unknown')


def backup_dir():
    d = Path(getattr(settings, 'BACKUP_DIR', '') or '').expanduser()
    if not str(d) or str(d) == '.':
        raise BackupError('BACKUP_DIR is not configured.')
    d = d.resolve()
    base = Path(settings.BASE_DIR).resolve()
    if d == base or base in d.parents:
        raise BackupError(f'BACKUP_DIR ({d}) must be outside the project folder ({base}).')
    d.mkdir(parents=True, exist_ok=True)
    return d


def sha256_file(p, chunk=1 << 20):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(chunk), b''):
            h.update(b)
    return h.hexdigest()


def sha256_bytes_stream(fobj, chunk=1 << 20):
    h = hashlib.sha256()
    for b in iter(lambda: fobj.read(chunk), b''):
        h.update(b)
    return h.hexdigest()


def live_db_path():
    return Path(settings.DATABASES['default']['NAME'])


def _snapshot_db(dst_path, db_path=None):
    """Consistent copy via the sqlite online backup API."""
    dst = sqlite3.connect(str(dst_path))
    try:
        if db_path is None:
            connection.ensure_connection()
            if connection.in_atomic_block:
                # Inside an open transaction (tests / atomic callers) the online backup API waits forever on its
                # own write lock, so copy via SQL dump instead. Normal app/command use is autocommit -> backup API.
                dst.executescript('\n'.join(connection.connection.iterdump()))
            else:
                connection.connection.backup(dst)  # consistent online copy
        else:
            src = sqlite3.connect(f'file:{Path(db_path).as_posix()}?mode=ro', uri=True)
            try:
                src.backup(dst)
            finally:
                src.close()
    finally:
        dst.close()


def _db_facts(db_file):
    con = sqlite3.connect(str(db_file))
    try:
        ok = con.execute('pragma integrity_check').fetchone()[0]
        tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        migs = []
        if 'django_migrations' in tables:
            migs = [f'{a}.{n}' for a, n in con.execute('select app, name from django_migrations order by app, name')]
        counts = {}
        for m in django_apps.get_models():
            t = m._meta.db_table
            if t in tables:
                counts[m._meta.label] = con.execute(f'select count(*) from "{t}"').fetchone()[0]
        return ok, migs, counts
    finally:
        con.close()


def _media_roots():
    out = []
    for arc, p in (('private_media', getattr(settings, 'EMPLOYEE_PRIVATE_ROOT', None)),
                   ('media', getattr(settings, 'MEDIA_ROOT', None))):
        if p:
            out.append((arc, Path(p)))
    return out


def _history(event):
    try:
        with open(backup_dir() / HISTORY, 'a', encoding='utf-8') as f:
            f.write(json.dumps(event, default=str) + '\n')
    except OSError:
        pass


def _audit(action, rec_name, reason='', changes=None):
    try:
        from audit.recorder import log_manual
        log_manual('backup', 'BackupRecord', rec_name, rec_name, action, reason, changes)
    except Exception:  # audit table may be missing on a very old target DB
        pass


def create_backup(kind=BackupRecord.MANUAL, note='', db_path=None, protected=False, record=True):
    """Write <BACKUP_DIR>/<name>.zip. ``db_path`` None = the live Django connection."""
    out = backup_dir()
    ts = timezone.localtime()
    name = f'qb-{kind.replace("_", "-")}-{ts:%Y%m%d-%H%M%S}-{ts.microsecond // 1000:03d}'
    target = out / f'{name}.zip'
    partial = out / f'.{name}.zip.partial'
    files = {}
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / DB_ARC
        _snapshot_db(snap, db_path)
        ok, migs, counts = _db_facts(snap)
        if ok != 'ok':
            raise BackupError(f'integrity_check on the snapshot returned {ok!r}; backup aborted.')
        try:
            with zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as z:
                z.write(snap, DB_ARC)
                files[DB_ARC] = {'sha256': sha256_file(snap), 'size': snap.stat().st_size}
                for arc, root in _media_roots():
                    if not root.exists():
                        continue
                    for f in sorted(root.rglob('*')):
                        if f.is_file() and not f.is_symlink():
                            an = f'{arc}/{f.relative_to(root).as_posix()}'
                            z.write(f, an)
                            files[an] = {'sha256': sha256_file(f), 'size': f.stat().st_size}
                manifest = {'name': name, 'kind': kind, 'created_at': ts.isoformat(), 'app_version': app_version(),
                            'source_db': str(db_path or live_db_path()), 'integrity_check': ok,
                            'migrations': migs, 'record_counts': counts, 'files': files, 'note': note}
                z.writestr(MANIFEST, json.dumps(manifest, indent=2, sort_keys=True))
            os.replace(partial, target)
        finally:
            if partial.exists():
                partial.unlink()
    rec = None
    if record:
        rec = BackupRecord.objects.create(name=name, kind=kind, path=str(target), created_at=ts,
                                          size=target.stat().st_size, zip_sha256=sha256_file(target),
                                          manifest={k: v for k, v in manifest.items() if k != 'files'} |
                                          {'file_count': len(files)}, protected=protected, note=note[:300])
        _audit('backup', name, note, {'kind': [None, kind], 'files': [None, len(files)]})
    _history({'event': 'create', 'name': name, 'kind': kind, 'at': ts, 'zip_sha256': sha256_file(target)})
    return rec if record else target


def read_manifest(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        return json.loads(z.read(MANIFEST))


def verify_zip(zip_path, expected_zip_sha=None):
    """Returns (ok, problems list, manifest)."""
    problems, manifest = [], {}
    p = Path(zip_path)
    if not p.exists():
        return False, [f'File not found: {p}'], {}
    if expected_zip_sha and sha256_file(p) != expected_zip_sha:
        problems.append('ZIP file hash differs from the hash recorded at creation (file changed).')
    try:
        with zipfile.ZipFile(p) as z:
            manifest = json.loads(z.read(MANIFEST))
            listed = manifest.get('files', {})
            present = {i.filename for i in z.infolist() if not i.is_dir()} - {MANIFEST}
            for extra in sorted(present - set(listed)):
                problems.append(f'Unexpected file not in manifest: {extra}')
            for an, meta in listed.items():
                if an not in present:
                    problems.append(f'Missing from ZIP: {an}')
                    continue
                with z.open(an) as fh:
                    if sha256_bytes_stream(fh) != meta.get('sha256'):
                        problems.append(f'Hash mismatch: {an}')
            if DB_ARC in present:
                with tempfile.TemporaryDirectory() as tmp:
                    z.extract(DB_ARC, tmp)
                    try:
                        ok, _, _ = _db_facts(Path(tmp) / DB_ARC)
                    except sqlite3.DatabaseError as e:
                        ok = f'error: {e}'
                    if ok != 'ok':
                        problems.append(f'integrity_check on extracted DB: {ok}')
            else:
                problems.append('No database in backup.')
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as e:
        problems.append(f'Unreadable backup: {e}')
    return not problems, problems, manifest


def verify(rec):
    ok, problems, _ = verify_zip(rec.path, rec.zip_sha256 or None)
    rec.verified_at, rec.verify_ok = timezone.now(), ok
    rec.verify_notes = 'All files match the manifest; integrity_check ok.' if ok else '\n'.join(problems)
    rec.save(update_fields=['verified_at', 'verify_ok', 'verify_notes'])
    _history({'event': 'verify', 'name': rec.name, 'ok': ok, 'at': rec.verified_at})
    return ok, problems


def rescan():
    """Register app-format ZIPs found in BACKUP_DIR that the (possibly restored) DB does not know about."""
    added = 0
    for z in sorted(backup_dir().glob('qb-*.zip')):
        if BackupRecord.objects.filter(name=z.stem).exists():
            continue
        try:
            m = read_manifest(z)
        except Exception:
            continue
        created = datetime.fromisoformat(m['created_at']) if m.get('created_at') else timezone.now()
        BackupRecord.objects.create(name=z.stem, kind=m.get('kind', 'manual'), path=str(z), created_at=created,
                                    size=z.stat().st_size, manifest={k: v for k, v in m.items() if k != 'files'},
                                    note='Re-registered from backup folder (ZIP hash not recorded at creation).')
        added += 1
    return added


def known_migrations():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    return {f'{a}.{n}' for a, n in loader.disk_migrations}


def migration_compat(manifest):
    """Backup may be older than the code (pending migrations to apply), never newer/unknown."""
    code = known_migrations()
    have = set(manifest.get('migrations', []))
    unknown = sorted(have - code)
    pending = sorted(code - have)
    return unknown, pending


def apply_retention(keep=None, dry_run=False):
    keep = int(getattr(settings, 'BACKUP_KEEP_LAST', 10) if keep is None else keep)
    live = [r for r in BackupRecord.objects.filter(deleted_at__isnull=True, kind__in=['manual', 'auto'])
            .order_by('-created_at', '-id')]
    candidates = [r for r in live if not r.is_protected]
    removed = []
    for r in candidates[keep:]:
        p = Path(r.path)
        if p.parent.resolve() != backup_dir() or not p.name.startswith('qb-') or p.suffix != '.zip':
            continue  # only files this app created inside BACKUP_DIR
        removed.append(r.name)
        if not dry_run:
            if p.exists():
                p.unlink()
            r.deleted_at = timezone.now()
            r.save(update_fields=['deleted_at'])
            _history({'event': 'retention_delete', 'name': r.name, 'at': r.deleted_at})
    return removed


def _assert_unlocked(target):
    if Path(str(target) + '-wal').exists():
        raise BackupError(f'{target}-wal exists (database in use). Stop runserver and any other process, then retry.')
    try:
        con = sqlite3.connect(str(target), timeout=0.5, isolation_level=None)
        try:
            con.execute('BEGIN EXCLUSIVE')
            con.execute('ROLLBACK')
        finally:
            con.close()
    except sqlite3.OperationalError as e:
        raise BackupError(f'The database is locked ({e}). Stop runserver (and any other app using it), then retry.')


def restore(rec, phrase, target_db=None):
    """Restore ``rec`` over ``target_db`` (default: the live DB). Returns dict with details. Raises BackupError
    and leaves the target untouched if any check fails."""
    if (phrase or '').strip() != rec.confirm_phrase:
        raise BackupError(f'Confirmation phrase does not match. Type exactly: {rec.confirm_phrase}')
    if rec.deleted_at:
        raise BackupError('This backup was removed by retention.')
    ok, problems = verify(rec)
    if not ok:
        raise BackupError('Backup failed verification: ' + '; '.join(problems))
    manifest = read_manifest(rec.path)
    unknown, pending = migration_compat(manifest)
    if unknown:
        raise BackupError('Backup was made by a newer version (unknown migrations: ' + ', '.join(unknown[:10]) + ').')
    live = target_db is None
    target = Path(target_db or live_db_path()).resolve()
    if not target.exists():
        raise BackupError(f'Target database not found: {target}')
    if live:
        connections.close_all()
    _assert_unlocked(target)
    pre = create_backup(kind=BackupRecord.PRE_RESTORE, note=f'Automatic backup before restoring {rec.name}',
                        db_path=None if live else target, protected=True)
    ok2, probs2 = verify(pre)
    if not ok2:
        raise BackupError('Pre-restore backup failed verification; restore aborted: ' + '; '.join(probs2))
    tmp = target.with_name(f'.{target.name}.restore-{os.getpid()}.tmp')
    try:
        with zipfile.ZipFile(rec.path) as z, z.open(DB_ARC) as src, open(tmp, 'wb') as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        if sha256_file(tmp) != manifest['files'][DB_ARC]['sha256']:
            raise BackupError('Extracted database hash mismatch; restore aborted.')
        okc, _, _ = _db_facts(tmp)
        if okc != 'ok':
            raise BackupError(f'integrity_check on extracted DB: {okc}; restore aborted.')
        if live:
            connections.close_all()
        _assert_unlocked(target)
        try:
            os.replace(tmp, target)
        except PermissionError as e:
            raise BackupError(f'Could not replace the database file ({e}). Stop runserver, then use '
                              f'"manage.py restore_backup {rec.name}".')
    finally:
        if tmp.exists():
            tmp.unlink()
    # record the restore inside the restored database itself
    con = sqlite3.connect(str(target))
    try:
        if con.execute("select 1 from sqlite_master where name='audit_auditevent'").fetchone():
            con.execute('insert into audit_auditevent (timestamp, app_label, model, object_id, object_repr, action, '
                        'changes, reason, request_path, source) values (?,?,?,?,?,?,?,?,?,?)',
                        (timezone.now().astimezone(dt_timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'), 'backup', 'BackupRecord', rec.name, rec.name, 'restore',
                         json.dumps({'pre_restore_backup': [None, pre.name]}),
                         f'Restored {rec.name}; pre-restore backup {pre.name}', '',
                         'ui' if live else 'system'))
            con.commit()
    finally:
        con.close()
    _history({'event': 'restore', 'name': rec.name, 'target': str(target), 'pre_restore': pre.name,
              'at': timezone.now()})
    if live:
        connections.close_all()
    return {'target': str(target), 'pre_restore': pre.name, 'pending_migrations': pending}
