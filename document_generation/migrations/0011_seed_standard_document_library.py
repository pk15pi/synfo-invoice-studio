from django.db import migrations

from document_generation.standard_library import (
    CLAUSES,
    CLAUSE_DOCUMENT_TYPES,
    FIELD_SETS,
    TEMPLATES,
)


def seed_standard_library(apps, schema_editor):
    DocumentType = apps.get_model('document_generation', 'DocumentType')
    DocumentFieldDefinition = apps.get_model('document_generation', 'DocumentFieldDefinition')
    DocumentTemplate = apps.get_model('document_generation', 'DocumentTemplate')
    DocumentTemplateVersion = apps.get_model('document_generation', 'DocumentTemplateVersion')
    DocumentClause = apps.get_model('document_generation', 'DocumentClause')
    DocumentTemplateClause = apps.get_model('document_generation', 'DocumentTemplateClause')
    database = schema_editor.connection.alias

    document_types = {
        row.code: row
        for row in DocumentType.objects.using(database).filter(code__in=FIELD_SETS)
    }
    for code, definitions in FIELD_SETS.items():
        document_type = document_types.get(code)
        if document_type is None:
            continue
        for order, definition in enumerate(definitions):
            DocumentFieldDefinition.objects.using(database).get_or_create(
                document_type_id=document_type.pk,
                field_key=definition['field_key'],
                defaults={
                    **definition,
                    'display_order': order,
                    'is_active': True,
                },
            )

    clause_rows = {}
    for code, name, category, content in CLAUSES:
        clause, _ = DocumentClause.objects.using(database).get_or_create(
            code=code,
            version=1,
            defaults={
                'name': name,
                'category': category,
                'clause_text': content,
                'approval_status': 'review',
                'admin_notes': 'Requires qualified legal review before approval or use.',
                'is_active': True,
            },
        )
        clause_rows[code] = clause

    for clause_code, applicable_codes in CLAUSE_DOCUMENT_TYPES.items():
        clause = clause_rows.get(clause_code)
        if clause is None:
            continue
        clause.applicable_document_types.db_manager(database).add(*(
            document_types[code].pk
            for code in applicable_codes if code in document_types
        ))

    for code, configuration in TEMPLATES.items():
        document_type = document_types.get(code)
        if document_type is None:
            continue
        DocumentTemplate.objects.using(database).filter(
            document_type_id=document_type.pk,
            name='Starter template (review required)',
            is_active=True,
        ).update(is_active=False)
        template, _ = DocumentTemplate.objects.using(database).get_or_create(
            document_type_id=document_type.pk,
            name=configuration['name'],
            defaults={'is_active': True},
        )
        version, _ = DocumentTemplateVersion.objects.using(database).get_or_create(
            template_id=template.pk,
            version_number=1,
            defaults={
                'status': 'review',
                'body_template': configuration['body'],
                'footer_text': '{{ company_display_name }} · {{ document_number }}',
                'output_formats': ['docx', 'pdf'],
                'signature_configuration': {
                    'parties': list(configuration['parties']),
                    'review_required': True,
                },
            },
        )
        for order, clause_code in enumerate(configuration['clauses']):
            clause = clause_rows.get(clause_code)
            if clause is None:
                continue
            DocumentTemplateClause.objects.using(database).get_or_create(
                template_version_id=version.pk,
                clause_id=clause.pk,
                defaults={
                    'display_order': order,
                    'is_mandatory': clause_code not in {'non-solicitation', 'late-payment'},
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ('document_generation', '0010_documentclause_admin_notes_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_standard_library, migrations.RunPython.noop),
    ]
