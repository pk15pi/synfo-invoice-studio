from django.db import migrations


def create_profiles(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    UserProfile = apps.get_model('access_control', 'UserProfile')
    for user in User.objects.all():
        UserProfile.objects.get_or_create(
            user=user,
            defaults={'role': 'admin' if user.is_superuser else ''},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('access_control', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_profiles, migrations.RunPython.noop),
    ]
