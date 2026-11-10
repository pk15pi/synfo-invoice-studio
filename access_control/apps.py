from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_save


class AccessControlConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'access_control'
    verbose_name = 'Users & roles'

    def ready(self):
        from django.contrib.auth import get_user_model

        from .models import UserProfile

        def ensure_profile(sender, instance, created, **kwargs):
            profile, _ = UserProfile.objects.get_or_create(user=instance)
            if instance.is_superuser and profile.role != UserProfile.Role.ADMIN:
                profile.role = UserProfile.Role.ADMIN
                profile.save(update_fields=['role'])

        post_save.connect(ensure_profile, sender=get_user_model(), dispatch_uid='access_control.ensure_profile')
