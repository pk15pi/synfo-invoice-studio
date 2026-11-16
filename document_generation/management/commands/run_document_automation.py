from django.core.management.base import BaseCommand
from document_generation.automation_services import run_document_automation


class Command(BaseCommand):
    help = 'Run idempotent document approval, expiry, renewal, acknowledgement, signature and compliance automations.'

    def handle(self, *args, **options):
        result = run_document_automation()
        if result['locked']:
            self.stdout.write(self.style.WARNING('Skipped: another document automation process holds the lock.'))
            return
        for execution in result['executions']:
            label = self.style.SUCCESS if execution.status == 'completed' else self.style.ERROR
            self.stdout.write(label(
                f'{execution.rule.code}: {execution.status} '
                f'({execution.result_count} notification(s)/review(s))'
            ))
            if execution.error:
                self.stderr.write(execution.error)
