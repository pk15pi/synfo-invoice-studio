from django.db import migrations
from django.db.models import Max

from document_generation.standard_library import FIELD_SETS, TEMPLATES

PRIORITY_DOCUMENT_TYPES = (
    'non-disclosure-agreement-nda',
    'mutual-nda',
    'master-client-agreement-mca',
    'experience-certificate',
    'relieving-letter',
)


def complete_priority_documents(apps, schema_editor):
    DocumentType = apps.get_model('document_generation', 'DocumentType')
    DocumentFieldDefinition = apps.get_model('document_generation', 'DocumentFieldDefinition')
    DocumentTemplate = apps.get_model('document_generation', 'DocumentTemplate')
    DocumentTemplateVersion = apps.get_model('document_generation', 'DocumentTemplateVersion')
    database = schema_editor.connection.alias

    for code in PRIORITY_DOCUMENT_TYPES:
        document_type = DocumentType.objects.using(database).filter(code=code).first()
        if document_type is None:
            continue

        definitions = DocumentFieldDefinition.objects.using(database).filter(
            document_type_id=document_type.pk,
        )
        existing_keys = set(definitions.values_list('field_key', flat=True))
        next_order = (definitions.aggregate(Max('display_order'))['display_order__max'] or -1) + 1
        for definition in FIELD_SETS[code]:
            if definition['field_key'] in existing_keys:
                continue
            DocumentFieldDefinition.objects.using(database).create(
                document_type_id=document_type.pk,
                display_order=next_order,
                is_active=True,
                **definition,
            )
            next_order += 1

        configuration = TEMPLATES[code]
        template = DocumentTemplate.objects.using(database).filter(
            document_type_id=document_type.pk,
            name=configuration['name'],
        ).first()
        if template is None:
            continue
        template.description = configuration['description']
        template.applicable_party_type = configuration['party_type']
        template.business_scenario = configuration['scenario']
        template.save(
            using=database,
            update_fields=['description', 'applicable_party_type', 'business_scenario'],
        )
        version = DocumentTemplateVersion.objects.using(database).filter(
            template_id=template.pk,
            version_number=1,
            status__in=('draft', 'review'),
            approved_at__isnull=True,
        ).first()
        if version is None:
            continue
        version.body_template = configuration['body']
        version.signature_configuration = {
            'parties': list(configuration['parties']),
            'review_required': True,
        }
        version.save(
            using=database,
            update_fields=['body_template', 'signature_configuration'],
        )


class Migration(migrations.Migration):
    dependencies = [
        ('document_generation', '0014_seed_financial_administrative_library'),
    ]

    operations = [
        migrations.RunPython(complete_priority_documents, migrations.RunPython.noop),
    ]
