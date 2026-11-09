from django.core.management.base import BaseCommand, CommandError

from backup import pg_sync as P


class Command(BaseCommand):
    help = 'Clone data SQLite -> PostgreSQL or PostgreSQL -> SQLite (settings from the Backups > PostgreSQL sync page).'

    def add_arguments(self, p):
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument('--test', action='store_true')
        g.add_argument('--to-postgres', action='store_true')
        g.add_argument('--to-sqlite', action='store_true')
        p.add_argument('--replace-live', action='store_true', help='With --to-sqlite: replace live db (ZIP backup first).')
        p.add_argument('--confirm', default='', help='Confirmation phrase (asked interactively if omitted).')

    def _phrase(self, expected, given):
        return given or input(f'Type {expected} to continue: ')

    def handle(self, *a, test, to_postgres, to_sqlite, replace_live, confirm, **o):
        try:
            if test:
                r = P.test_connection()
                self.stdout.write(f'Connected: {r["database"]} as {r["user"]}; {r["version"]}; tables {r["table_count"]}')
                return
            if to_postgres:
                r = P.sync_sqlite_to_pg(self._phrase(P.CONFIRM_TO_PG, confirm))
            else:
                r = P.sync_pg_to_sqlite(self._phrase(P.CONFIRM_TO_SQLITE, confirm), replace_live=replace_live)
        except P.SyncError as e:
            raise CommandError(str(e))
        for t, n in r['tables']:
            self.stdout.write(f'  {t}: {n}')
        self.stdout.write(f'Done: {r["table_count"]} tables, {r["row_count"]} rows in {r["seconds"]} s.'
                          + (f' Clone: {r["clone_file"]}.' if r.get('clone_file') else '')
                          + (f' Safety ZIP: {r["pre_backup"]}.' if r.get('pre_backup') else '')
                          + (' Live DB replaced - restart run_windows.bat.' if r.get('replaced_live') else ''))
