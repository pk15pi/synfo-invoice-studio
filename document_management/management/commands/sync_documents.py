from django.core.management.base import BaseCommand

from document_management.services import sync_index


class Command(BaseCommand):
    help = 'Index existing private employee documents and expense attachments (read-only; files are not moved).'

    def add_arguments(self, parser):
        parser.add_argument('--no-hash', action='store_true', help='Skip size/sha256 (faster).')

    def handle(self, *a, **o):
        s = sync_index(hash_files=not o['no_hash'])
        self.stdout.write(f"Created {s['created']}, updated {s['updated']}, missing files {s['missing']}, "
                          f"sources gone {s['removed_sources']}.")
