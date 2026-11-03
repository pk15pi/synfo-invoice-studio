# Audit log and backups (Stage 8)

## Audit log (`/audit/`)
- Append-only `AuditEvent` rows for saves/deletes of the financial and sensitive models listed in `audit/apps.py`
  (`AUDITED_MODELS`). Old values are captured in `pre_save`; changes are stored as `{field: [old, new]}`.
- Actions are derived from status changes: posted -> post, reversal entry posted -> reverse, finalized -> finalize,
  cancelled / cancelled_at set -> cancel, period closed -> close, closed -> open -> reopen; bank ImportBatch create -> import.
  Backups write `backup`, restores write `restore` (inside the restored DB).
- There is no login: every event shows **Local operator (no login)**. No user name is ever recorded.
- Masking: Aadhaar, PAN and account numbers keep only the last 4; long digit runs / PANs in object text are masked;
  fields whose name contains password/secret/token/api_key are never logged.
- `audit.middleware.AuditContextMiddleware` stores the request path and any submitted `reason` / `*_reason` field.
- Limitation: `QuerySet.update()` / bulk operations bypass model signals and are not captured (e.g. the
  original entry's status flip in `accounting.services.reverse`; the reversal entry itself is logged as `reverse`).

## Backups (`/backups/`)
- Folder: `BACKUP_DIR` (default `E:\QuantBud_Backups`, override with env `QB_BACKUP_DIR`); must be outside the repo.
- ZIP = `db.sqlite3` (sqlite online backup API) + `private_media/` + `media/` + `manifest.json`
  (app version, applied migrations, sha256 + size per file, record counts per model, integrity_check).
- ZIP contents are not encrypted by the app. Protect `BACKUP_DIR` with OS/disk encryption and filesystem
  access controls; encrypt any off-machine copies. Anyone who can read a ZIP can read its database and files.
- Verify re-hashes every file, checks for missing/extra files and the ZIP hash, and runs `integrity_check` on the
  extracted DB.
- Commands:
  - `manage.py create_backup [--kind manual|auto|pre_migration] [--verify] [--retention] [--note ...]`
  - `manage.py verify_backup [name]`
  - `manage.py backup_retention [--keep N] [--dry-run]`
  - `manage.py restore_backup <name> --confirm "RESTORE <name>"` (stop runserver first)
- Automatic backup: `run_backup.bat` (= `create_backup --kind auto --verify --retention`). No scheduled task is
  created; add one yourself in Task Scheduler if wanted.
- **Habit before every migration:** `.venv\Scripts\python.exe manage.py create_backup --kind pre_migration --verify`
  then migrate the specific app(s) only.
- Retention (`BACKUP_KEEP_LAST`, default 10, env `QB_BACKUP_KEEP_LAST`) only removes app-made `qb-manual-*` /
  `qb-auto-*` ZIPs in BACKUP_DIR. Never removed: protected, pre-restore, pre-migration, any name containing
  pre_accounting / pre-stage, and anything not created by this app (e.g. `pre_accounting_20261103_073941\`,
  `backups\db-pre-stage*.sqlite3`).
- Restore: only a verified backup, exact phrase `RESTORE <name>`, refuses backups with migrations unknown to the
  code, refuses if the DB is locked / has a -wal file, makes and verifies a protected pre-restore backup, extracts to a
  temp file next to the DB, re-checks hash + integrity, then `os.replace` (atomic). Restart runserver afterwards.
  If the backup is older than the code, migrate the listed apps one by one.
