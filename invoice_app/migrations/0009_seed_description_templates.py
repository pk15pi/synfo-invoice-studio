"""Seed the Description Template Master and give existing projects a description type.

Existing behaviour was: line description = '<project.description> - <period>' (or just the period
when the text was blank and dates existed). To keep invoices identical:
  * non-blank description 'X'  -> type 'custom', text 'X - {start_date} - {end_date}'
  * blank description          -> type 'blank' (per the brief; such rows previously printed the bare period)
Reversible: the suffix is stripped again on rollback. Saved invoice lines are never touched."""
from django.db import migrations

SEED = [
    ('Monthly Support', 'Support - {start_date} - {end_date}'),
    ('Consulting', 'Consulting Services - {start_date} - {end_date}'),
    ('Development', 'Development Services - {start_date} - {end_date}'),
    ('General Services', 'Professional Services for {month} {year}'),
]
SUFFIX = ' - {start_date} - {end_date}'


def forwards(apps, schema_editor):
    T = apps.get_model('invoice_app', 'InvoiceDescriptionTemplate')
    for name, text in SEED:
        T.objects.get_or_create(name=name, defaults={'template_text': text, 'is_active': True})
    P = apps.get_model('invoice_app', 'ProjectTemplate')
    for p in P.objects.all():
        base = (p.description or '').strip()
        if base:
            p.invoice_description_type = 'custom'
            p.description = (base.replace('{', '{{').replace('}', '}}') + SUFFIX)[:240]
        else:
            p.invoice_description_type = 'blank'
            p.description = ''
        p.description_template = None
        p.save(update_fields=['invoice_description_type', 'description', 'description_template'])


def backwards(apps, schema_editor):
    P = apps.get_model('invoice_app', 'ProjectTemplate')
    for p in P.objects.all():
        d = p.description or ''
        if p.invoice_description_type == 'custom' and d.endswith(SUFFIX):
            d = d[:-len(SUFFIX)].replace('{{', '{').replace('}}', '}')
        elif p.invoice_description_type != 'custom':
            d = ''
        p.description = d
        p.save(update_fields=['description'])
    # Seeded templates are left in place (harmless; the table is dropped by the previous migration's rollback).


class Migration(migrations.Migration):
    dependencies = [('invoice_app', '0008_description_templates')]
    operations = [migrations.RunPython(forwards, backwards)]
