from django.db import migrations, models
from django.db.models import Min


def backfill_created_on(apps, schema_editor):
    Client = apps.get_model('invoice_app', 'Client')
    Invoice = apps.get_model('invoice_app', 'Invoice')
    ManpowerDeployment = apps.get_model('invoice_app', 'ManpowerDeployment')
    RecruitmentAgreement = apps.get_model('invoice_app', 'RecruitmentAgreement')
    ClientDocument = apps.get_model('invoice_app', 'ClientDocument')
    for client in Client.objects.filter(created_on__isnull=True).iterator():
        dates = []
        for value in (
            Invoice.objects.filter(client_id=client.pk).aggregate(d=Min('invoice_date'))['d'],
            ManpowerDeployment.objects.filter(client_id=client.pk).aggregate(d=Min('start_date'))['d'],
            RecruitmentAgreement.objects.filter(client_id=client.pk).aggregate(d=Min('effective_start'))['d'],
        ):
            if value:
                dates.append(value)
        uploaded = ClientDocument.objects.filter(client_id=client.pk).aggregate(d=Min('uploaded_at'))['d']
        if uploaded:
            dates.append(uploaded.date())
        if client.nda_signed_date:
            dates.append(client.nda_signed_date)
        if dates:
            Client.objects.filter(pk=client.pk).update(created_on=min(dates))


class Migration(migrations.Migration):

    dependencies = [
        ('invoice_app', '0023_client_is_email_active'),
    ]

    operations = [
        migrations.AddField(
            model_name='client',
            name='created_on',
            field=models.DateField(blank=True, editable=False, null=True, verbose_name='Added on',
                                   help_text='First recorded customer activity. Existing records were backfilled from '
                                             'the earliest invoice, deployment or agreement.'),
        ),
        migrations.AddField(
            model_name='client',
            name='inactive_since',
            field=models.DateField(blank=True, editable=False, null=True, verbose_name='Inactive since',
                                   help_text='Date the customer was last marked inactive; cleared when reactivated.'),
        ),
        migrations.RunPython(backfill_created_on, migrations.RunPython.noop),
    ]
