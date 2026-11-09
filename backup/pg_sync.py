"""PostgreSQL <-> SQLite data clone for the Backup & Recovery page.

The running app keeps using settings.DATABASES['default'] (SQLite). A temporary, separate connection alias is added only
for the duration of a sync and removed afterwards. Destination schema is always created with Django migrations against
that alias (never hand-written). Rows are copied table by table inside one destination transaction (FKs are
DEFERRABLE INITIALLY DEFERRED on both backends, so order does not matter and a failure rolls everything back).

Credentials: env vars QB_PG_HOST, QB_PG_PORT, QB_PG_DB, QB_PG_USER, QB_PG_PASSWORD, QB_PG_SSLMODE override a local JSON
file (QB_PG_CONFIG_FILE, default ~/.quantbud/pg_sync.json, outside the repo and outside backup ZIPs). The password is
never logged, rendered or returned in messages.
"""
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.color import no_style
from django.db import connections, router, transaction
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from . import services as S
from .models import BackupRecord

PG_ALIAS = 'qb_pg_sync'
SQLITE_ALIAS = 'qb_sqlite_sync'
SSL_MODES = ['disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full']
FIELDS = ('host', 'port', 'name', 'user', 'password', 'sslmode')
ENV = {'host': 'QB_PG_HOST', 'port': 'QB_PG_PORT', 'name': 'QB_PG_DB', 'user': 'QB_PG_USER',
       'password': 'QB_PG_PASSWORD', 'sslmode': 'QB_PG_SSLMODE'}
DEFAULTS = {'host': 'localhost', 'port': '5432', 'name': '', 'user': '', 'password': '', 'sslmode': 'prefer'}
CONFIRM_TO_PG = 'OVERWRITE POSTGRES'
CONFIRM_TO_SQLITE = 'OVERWRITE SQLITE'
SKIP_TABLES = {'django_migrations', 'sqlite_sequence'}
BATCH = 1000


class SyncError(Exception):
    pass


# ----------------------------------------------------------------------------------------------- configuration
def config_path():
    return Path(os.environ.get('QB_PG_CONFIG_FILE') or (Path.home() / '.quantbud' / 'pg_sync.json'))


def _read_file():
    p = config_path()
    try:
        data = json.loads(p.read_text(encoding='utf-8')) if p.is_file() else {}
    except (OSError, ValueError):
        data = {}
    return {k: str(data.get(k, '') or '') for k in FIELDS}


def load_config():
    """Effective config (env overrides file). Contains the password - never pass it to templates."""
    stored = _read_file()
    cfg = {}
    for k in FIELDS:
        env = os.environ.get(ENV[k])
        cfg[k] = env if env not in (None, '') else (stored[k] or DEFAULTS[k])
    return cfg


def public_config():
    """Safe view for templates: no password, only whether one is set and where it comes from."""
    cfg = load_config()
    out = {k: cfg[k] for k in FIELDS if k != 'password'}
    out['password_set'] = bool(cfg['password'])
    out['env_overrides'] = [ENV[k] for k in FIELDS if os.environ.get(ENV[k])]
    out['password_from_env'] = bool(os.environ.get(ENV['password']))
    out['configured'] = bool(cfg['host'] and cfg['name'] and cfg['user'])
    out['config_file'] = str(config_path())
    return out


def save_config(data):
    """Persist non-empty values; an empty password keeps the stored one (replace-on-save)."""
    stored = _read_file()
    for k in FIELDS:
        if k == 'password':
            if data.get('clear_password'):
                stored['password'] = ''
            elif data.get('password'):
                stored['password'] = data['password']
        elif k in data:
            stored[k] = str(data[k] or '').strip()
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(stored, indent=1), encoding='utf-8')
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)


def _clean(msg, cfg=None):
    """Strip the password from any error text before it reaches the UI or logs."""
    msg = str(msg)
    pw = (cfg or load_config()).get('password') or ''
    if pw:
        msg = msg.replace(pw, '********')
    return msg.strip() or 'Unknown error'


def pg_db_settings(cfg=None):
    cfg = cfg or load_config()
    if not (cfg['host'] and cfg['name'] and cfg['user']):
        raise SyncError('PostgreSQL host, database name and user are required. Save the connection settings first.')
    opts = {'connect_timeout': 8}
    if cfg.get('sslmode'):
        opts['sslmode'] = cfg['sslmode']
    return {'ENGINE': 'django.db.backends.postgresql', 'HOST': cfg['host'], 'PORT': str(cfg['port'] or '5432'),
            'NAME': cfg['name'], 'USER': cfg['user'], 'PASSWORD': cfg['password'], 'OPTIONS': opts}


# ----------------------------------------------------------------------------------------------- alias + routing
_local = threading.local()


class _ThreadRouter:
    """Sends un-routed ORM calls (e.g. data migrations using Model.objects without .using()) to the sync alias,
    but only in the thread that runs the sync. Other requests keep using 'default'."""
    def _alias(self):
        return getattr(_local, 'alias', None)

    def db_for_read(self, model, **hints):
        return self._alias()

    def db_for_write(self, model, **hints):
        return self._alias()


_router = _ThreadRouter()


@contextmanager
def temp_alias(alias, db):
    if alias in connections.settings or alias in settings.DATABASES:
        raise SyncError(f'Database alias {alias} is already in use (another sync running?).')
    cfg = connections.configure_settings({'default': dict(connections.settings['default']), alias: dict(db)})[alias]
    connections.settings[alias] = cfg
    try:
        yield connections[alias]
    finally:
        try:
            connections[alias].close()
        except Exception:
            pass
        try:
            del connections[alias]
        except Exception:
            pass
        connections.settings.pop(alias, None)


@contextmanager
def route_to(alias):
    routers = router.routers
    router.__dict__['routers'] = [_router] + [r for r in routers if r is not _router]
    _local.alias = alias
    try:
        yield
    finally:
        _local.alias = None
        router.__dict__['routers'] = routers


# ----------------------------------------------------------------------------------------------- helpers
def copy_models():
    """Concrete, managed models incl. auto-created m2m tables, one per db_table."""
    seen, out = set(), []
    for m in apps.get_models(include_auto_created=True):
        o = m._meta
        if o.proxy or not o.managed or o.db_table in seen:
            continue
        seen.add(o.db_table)
        out.append(m)
    return out


def pending_migrations(alias):
    ex = MigrationExecutor(connections[alias])
    graph = ex.loader.graph
    plan = ex.migration_plan(graph.leaf_nodes())
    unknown = sorted(f'{a}.{n}' for (a, n) in ex.loader.applied_migrations if (a, n) not in graph.nodes)
    return [f'{m.app_label}.{m.name}' for m, _ in plan], unknown


def migrate_alias(alias):
    with route_to(alias):
        call_command('migrate', database=alias, interactive=False, verbosity=0, run_syncdb=True)


def _tables(alias):
    with connections[alias].cursor() as c:
        return set(connections[alias].introspection.table_names(c))


def clone(src, dst):
    """Copy every model table from alias ``src`` into alias ``dst`` (already migrated). Destination model tables are
    emptied first. One atomic transaction on ``dst``. Returns list of (table, rows)."""
    sconn, dconn = connections[src], connections[dst]
    models = copy_models()
    dtables = _tables(dst)
    missing = [m._meta.db_table for m in models if m._meta.db_table not in dtables]
    if missing:
        raise SyncError('Destination is missing tables after migrate: ' + ', '.join(missing[:10]))
    q = dconn.ops.quote_name
    report = []
    with transaction.atomic(using=src), transaction.atomic(using=dst):
        with dconn.cursor() as cur:
            names = [m._meta.db_table for m in models]
            if dconn.vendor == 'postgresql':
                cur.execute('TRUNCATE ' + ', '.join(q(t) for t in names) + ' RESTART IDENTITY CASCADE')
            else:
                for t in names:
                    cur.execute(f'DELETE FROM {q(t)}')
            for m in models:
                fields = list(m._meta.local_concrete_fields)
                cols = ', '.join(q(f.column) for f in fields)
                sql = f'INSERT INTO {q(m._meta.db_table)} ({cols}) VALUES ({", ".join(["%s"] * len(fields))})'
                qs = m._base_manager.using(src).order_by('pk').values_list(*[f.attname for f in fields])
                n, batch = 0, []
                for row in qs.iterator(chunk_size=2000):
                    batch.append([f.get_db_prep_save(v, connection=dconn) for f, v in zip(fields, row)])
                    if len(batch) >= BATCH:
                        cur.executemany(sql, batch)
                        n += len(batch)
                        batch = []
                if batch:
                    cur.executemany(sql, batch)
                    n += len(batch)
                expected = m._base_manager.using(src).count()
                got = m._base_manager.using(dst).count()
                if n != expected or got != expected:
                    raise SyncError(f'Row count mismatch in {m._meta.db_table}: source {expected}, copied {n}, '
                                    f'destination {got}.')
                report.append((m._meta.db_table, n))
            for stmt in dconn.ops.sequence_reset_sql(no_style(), models):
                cur.execute(stmt)
    extra = sorted(_tables(src) - {m._meta.db_table for m in models} - SKIP_TABLES)
    return report, extra


def _summary(direction, report, extra, started, **more):
    return dict(direction=direction, tables=report, table_count=len(report), row_count=sum(n for _, n in report),
                skipped_tables=extra, seconds=round(time.monotonic() - started, 1), **more)


# ----------------------------------------------------------------------------------------------- public API
def test_connection(cfg=None):
    cfg = cfg or load_config()
    db = pg_db_settings(cfg)
    try:
        with temp_alias(PG_ALIAS, db) as conn:
            with conn.cursor() as c:
                c.execute('SELECT version()')
                version = c.fetchone()[0]
                c.execute('SELECT current_database(), current_user')
                dbname, user = c.fetchone()
            tables = _tables(PG_ALIAS)
            try:
                pend, _ = pending_migrations(PG_ALIAS)
            except Exception:
                pend = None
            try:
                with conn.cursor() as c:
                    c.execute('SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()')
                    row = c.fetchone()
                ssl = bool(row[0]) if row else None
            except Exception:  # noqa: BLE001 - informational only
                ssl = None
    except SyncError:
        raise
    except Exception as e:  # noqa: BLE001
        raise SyncError('Connection failed: ' + _clean(e, cfg))
    return {'version': version.split(',')[0], 'database': dbname, 'user': user, 'table_count': len(tables),
            'pending_migrations': None if pend is None else len(pend), 'ssl': ssl}


def _check_source_current(alias, label):
    pend, unknown = pending_migrations(alias)
    if unknown:
        raise SyncError(f'{label} has migrations this code does not know ({", ".join(unknown[:5])}). Update the code first.')
    if pend:
        raise SyncError(f'{label} has {len(pend)} unapplied migration(s) (e.g. {pend[0]}). Apply them before syncing '
                        f'so both schemas match.')


def sync_sqlite_to_pg(phrase, cfg=None):
    if (phrase or '').strip() != CONFIRM_TO_PG:
        raise SyncError(f'Confirmation phrase does not match. Type exactly: {CONFIRM_TO_PG}')
    cfg = cfg or load_config()
    db = pg_db_settings(cfg)
    started = time.monotonic()
    _check_source_current('default', 'The live SQLite database')
    try:
        with temp_alias(PG_ALIAS, db):
            migrate_alias(PG_ALIAS)
            report, extra = clone('default', PG_ALIAS)
    except SyncError:
        raise
    except Exception as e:  # noqa: BLE001
        raise SyncError('Sync to PostgreSQL failed; the PostgreSQL transaction was rolled back. ' + _clean(e, cfg))
    res = _summary('sqlite_to_postgres', report, extra, started, destination=f'{cfg["name"]} @ {cfg["host"]}')
    S._history({'event': 'pg_sync', 'direction': res['direction'], 'tables': res['table_count'],
                'rows': res['row_count'], 'destination': res['destination'], 'at': timezone.now()})
    return res


def _build_sqlite_from_pg(target, cfg):
    """Create a brand-new SQLite file at ``target`` (must not exist) holding a clone of PostgreSQL."""
    if target.exists():
        raise SyncError(f'{target} already exists.')
    with temp_alias(PG_ALIAS, pg_db_settings(cfg)):
        _check_source_current(PG_ALIAS, 'The PostgreSQL database')
        with temp_alias(SQLITE_ALIAS, {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(target)}):
            migrate_alias(SQLITE_ALIAS)
            report, extra = clone(PG_ALIAS, SQLITE_ALIAS)
    con = sqlite3.connect(str(target))
    try:
        ok = con.execute('PRAGMA integrity_check').fetchone()[0]
        fk = con.execute('PRAGMA foreign_key_check').fetchall()
    finally:
        con.close()
    if ok != 'ok' or fk:
        raise SyncError(f'Clone failed checks (integrity: {ok}, foreign-key problems: {len(fk)}).')
    return report, extra


def sync_pg_to_sqlite(phrase, replace_live=False, cfg=None):
    """Clone PostgreSQL into a new SQLite file in BACKUP_DIR/pg_clones. With ``replace_live`` the live db.sqlite3 is
    then replaced by that clone, but only after a verified, protected ZIP backup of the live DB."""
    if (phrase or '').strip() != CONFIRM_TO_SQLITE:
        raise SyncError(f'Confirmation phrase does not match. Type exactly: {CONFIRM_TO_SQLITE}')
    cfg = cfg or load_config()
    pg_db_settings(cfg)
    started = time.monotonic()
    folder = S.backup_dir() / 'pg_clones'
    folder.mkdir(parents=True, exist_ok=True)
    stamp = timezone.localtime().strftime('%Y%m%d-%H%M%S')
    target = folder / f'pg-clone-{stamp}.sqlite3'
    try:
        report, extra = _build_sqlite_from_pg(target, cfg)
    except Exception as e:  # noqa: BLE001
        for p in (target, Path(str(target) + '-journal')):
            try:
                p.unlink()
            except OSError:
                pass
        if isinstance(e, SyncError):
            raise
        raise SyncError('Sync from PostgreSQL failed; nothing was replaced. ' + _clean(e, cfg))
    res = _summary('postgres_to_sqlite', report, extra, started, clone_file=str(target), pre_backup=None,
                   replaced_live=False, source=f'{cfg["name"]} @ {cfg["host"]}')
    if replace_live:
        live = S.live_db_path().resolve()
        connections['default'].close()
        try:
            pre = S.create_backup(kind=BackupRecord.PRE_RESTORE, protected=True,
                                  note=f'Automatic backup before replacing SQLite with PostgreSQL clone {target.name}')
        except S.BackupError as e:
            raise SyncError(f'ZIP backup of the live database failed, so it was NOT replaced: {e}. '
                            f'The clone is kept at {target}.')
        ok, probs = S.verify(pre)
        if not ok:
            raise SyncError('Pre-overwrite ZIP backup failed verification; live database NOT replaced: '
                            + '; '.join(probs))
        res['pre_backup'] = pre.name
        connections.close_all()
        tmp = live.with_name(f'.{live.name}.pgclone-{os.getpid()}.tmp')
        try:
            S._assert_unlocked(live)
            src, dst = sqlite3.connect(str(target)), sqlite3.connect(str(tmp))
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()
            os.replace(tmp, live)
        except (S.BackupError, OSError) as e:
            raise SyncError(f'Live database NOT replaced ({e}). Safety backup {pre.name} exists; the clone is at '
                            f'{target}. Stop run_windows.bat and run "manage.py pg_sync --to-sqlite --replace-live".')
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        connections.close_all()
        res['replaced_live'] = True
    S._history({'event': 'pg_sync', 'direction': res['direction'], 'tables': res['table_count'],
                'rows': res['row_count'], 'clone_file': res['clone_file'], 'replaced_live': res['replaced_live'],
                'pre_backup': res['pre_backup'], 'at': timezone.now()})
    return res
