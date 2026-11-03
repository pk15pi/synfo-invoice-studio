from django.apps import AppConfig


class BackupConfig(AppConfig):
    name = 'backup'
    verbose_name = 'Backups'
    default_auto_field = 'django.db.models.BigAutoField'
