from django.core.management.base import BaseCommand

from document_management.services import check_documents


class Command(BaseCommand):
    help = 'Check every indexed document exists and still matches its sha256.'

    def handle(self, *a, **o):
        r = check_documents()
        for doc, state, note in r['rows']:
            if state != 'ok':
                self.stdout.write(f'{state.upper()}: #{doc.pk} {doc.title} - {note}')
        self.stdout.write(f"OK {r['ok']}, missing {r['missing']}, changed {r['changed']}.")
