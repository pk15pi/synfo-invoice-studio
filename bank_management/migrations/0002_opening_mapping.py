from django.db import migrations


def forward(apps, schema_editor):
    Account = apps.get_model('accounting', 'Account')
    Mapping = apps.get_model('accounting', 'AccountMapping')
    acct = Account.objects.filter(code='3100').first()
    Mapping.objects.get_or_create(key='bank.opening_equity', defaults={
        'account': acct, 'description': 'Bank/cash opening balances: offset equity (capital / opening balance)'})


class Migration(migrations.Migration):
    dependencies = [('bank_management', '0001_initial'), ('accounting', '0002_seed_chart_of_accounts')]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
