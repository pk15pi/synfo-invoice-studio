from django.db import migrations


FIELD_SETS = {
    'non-disclosure-agreement-nda': [
        ('first_party_name', 'First party name', 'text', True, 'company', 'display_name'),
        ('first_party_address', 'First party address', 'multiline', False, 'company', 'address'),
        ('second_party_name', 'Second party name', 'text', True, 'client', 'name'),
        ('second_party_address', 'Second party address', 'multiline', False, 'client', 'address'),
        ('purpose_of_disclosure', 'Purpose of disclosure', 'multiline', True, 'manual', ''),
        ('confidential_information', 'Confidential information description', 'multiline', True, 'manual', ''),
        ('agreement_duration', 'Agreement duration', 'text', False, 'manual', ''),
        ('confidentiality_period', 'Confidentiality period', 'text', True, 'manual', ''),
        ('permitted_disclosures', 'Permitted disclosures', 'multiline', False, 'manual', ''),
        ('governing_law', 'Governing law', 'text', False, 'manual', ''),
        ('jurisdiction', 'Jurisdiction', 'text', False, 'company', 'jurisdiction'),
    ],
    'master-client-agreement-mca': [
        ('client_name', 'Client name', 'text', True, 'client', 'name'),
        ('client_address', 'Client address', 'multiline', True, 'client', 'address'),
        ('client_contact_person', 'Client contact person', 'text', False, 'client', 'contact_person'),
        ('client_email', 'Client email', 'email', False, 'client', 'email'),
        ('client_phone', 'Client phone', 'text', False, 'client', 'phone'),
        ('client_gstin', 'Client GSTIN', 'text', False, 'client', 'gstin'),
        ('services_description', 'Services description', 'multiline', True, 'manual', ''),
        ('payment_terms', 'Payment terms', 'multiline', True, 'manual', ''),
        ('agreement_duration', 'Agreement duration', 'text', False, 'manual', ''),
        ('governing_law', 'Governing law', 'text', False, 'manual', ''),
    ],
    'freelancer-agreement': [
        ('freelancer_name', 'Freelancer name', 'text', True, 'manual', ''),
        ('freelancer_address', 'Freelancer address', 'multiline', True, 'manual', ''),
        ('freelancer_email', 'Freelancer email', 'email', False, 'manual', ''),
        ('freelancer_phone', 'Freelancer phone', 'text', False, 'manual', ''),
        ('project_name', 'Project name', 'text', True, 'manual', ''),
        ('project_description', 'Project description', 'multiline', True, 'manual', ''),
        ('scope_of_work', 'Scope of work', 'multiline', True, 'manual', ''),
        ('project_start_date', 'Project start date', 'date', False, 'manual', ''),
        ('expected_completion_date', 'Expected completion date', 'date', False, 'manual', ''),
        ('payment_amount', 'Payment amount', 'currency', True, 'manual', ''),
        ('payment_milestones', 'Payment milestones', 'multiline', False, 'manual', ''),
        ('payment_terms', 'Payment terms', 'multiline', True, 'manual', ''),
        ('confidentiality_requirements', 'Confidentiality requirements', 'multiline', False, 'manual', ''),
        ('ip_ownership_terms', 'IP ownership terms', 'multiline', False, 'manual', ''),
        ('termination_conditions', 'Termination conditions', 'multiline', False, 'manual', ''),
    ],
    'offer-letter': [
        ('candidate_name', 'Candidate name', 'text', True, 'manual', ''),
        ('candidate_address', 'Candidate address', 'multiline', False, 'manual', ''),
        ('candidate_email', 'Candidate email', 'email', False, 'manual', ''),
        ('candidate_phone', 'Candidate phone', 'text', False, 'manual', ''),
        ('designation', 'Designation', 'text', True, 'manual', ''),
        ('department', 'Department', 'text', False, 'manual', ''),
        ('employment_type', 'Employment type', 'text', False, 'manual', ''),
        ('work_location', 'Work location', 'text', False, 'manual', ''),
        ('joining_date', 'Joining date', 'date', True, 'manual', ''),
        ('annual_ctc', 'Annual CTC', 'currency', True, 'manual', ''),
        ('monthly_salary', 'Monthly salary', 'currency', False, 'manual', ''),
        ('probation_period', 'Probation period', 'text', False, 'manual', ''),
        ('notice_period', 'Notice period', 'text', False, 'manual', ''),
        ('working_hours', 'Working hours', 'text', False, 'manual', ''),
        ('reporting_manager', 'Reporting manager', 'text', False, 'manual', ''),
        ('offer_valid_until', 'Offer valid until', 'date', False, 'manual', ''),
        ('special_conditions', 'Special conditions', 'multiline', False, 'manual', ''),
    ],
    'joining-letter': [
        ('employee_name', 'Employee name', 'text', True, 'employee', 'name'),
        ('employee_id', 'Employee ID', 'text', True, 'employee', 'employee_id'),
        ('designation', 'Designation', 'text', True, 'employee', 'designation'),
        ('department', 'Department', 'text', False, 'employee', 'department'),
        ('joining_date', 'Date of joining', 'date', True, 'employee', 'date_of_joining'),
        ('work_location', 'Work location', 'text', False, 'employee', 'work_location'),
        ('employment_type', 'Employment type', 'text', False, 'employee', 'employment_type'),
        ('reporting_manager', 'Reporting manager', 'text', False, 'employee', 'reporting_manager'),
    ],
    'experience-certificate': [
        ('employee_name', 'Employee name', 'text', True, 'employee', 'name'),
        ('employee_id', 'Employee ID', 'text', True, 'employee', 'employee_id'),
        ('designation', 'Designation', 'text', True, 'employee', 'designation'),
        ('department', 'Department', 'text', False, 'employee', 'department'),
        ('date_of_joining', 'Date of joining', 'date', True, 'employee', 'date_of_joining'),
        ('last_working_date', 'Last working date', 'date', False, 'employee', 'last_working_date'),
        ('employment_type', 'Employment type', 'text', False, 'employee', 'employment_type'),
        ('service_description', 'Experience / service description', 'multiline', False, 'manual', ''),
    ],
    'relieving-letter': [
        ('employee_name', 'Employee name', 'text', True, 'employee', 'name'),
        ('employee_id', 'Employee ID', 'text', True, 'employee', 'employee_id'),
        ('designation', 'Designation', 'text', True, 'employee', 'designation'),
        ('department', 'Department', 'text', False, 'employee', 'department'),
        ('date_of_joining', 'Date of joining', 'date', True, 'employee', 'date_of_joining'),
        ('last_working_date', 'Last working date', 'date', True, 'employee', 'last_working_date'),
        ('handover_status', 'Handover status', 'multiline', False, 'manual', ''),
        ('clearance_status', 'Clearance status', 'multiline', False, 'manual', ''),
    ],
    'asset-handover-receipt': [
        ('employee_name', 'Employee name', 'text', True, 'employee', 'name'),
        ('employee_id', 'Employee ID', 'text', True, 'employee', 'employee_id'),
        ('designation', 'Designation', 'text', False, 'employee', 'designation'),
        ('asset_name', 'Asset name', 'text', True, 'asset', 'name'),
        ('asset_code', 'Asset ID', 'text', True, 'asset', 'asset_code'),
        ('asset_category', 'Asset category', 'text', False, 'asset', 'category'),
        ('brand_and_model', 'Brand and model', 'text', False, 'asset', 'brand_and_model'),
        ('serial_number', 'Serial number', 'text', False, 'asset', 'serial_number'),
        ('issue_condition', 'Condition at handover', 'text', False, 'manual', ''),
        ('accessories_issued', 'Accessories issued', 'multiline', False, 'manual', ''),
        ('acknowledgement_text', 'Employee acknowledgement', 'multiline', True, 'manual', ''),
    ],
}


def seed_fields(apps, schema_editor):
    DocumentCategory = apps.get_model('document_generation', 'DocumentCategory')
    DocumentType = apps.get_model('document_generation', 'DocumentType')
    DocumentFieldDefinition = apps.get_model('document_generation', 'DocumentFieldDefinition')
    DocumentTemplate = apps.get_model('document_generation', 'DocumentTemplate')
    DocumentTemplateVersion = apps.get_model('document_generation', 'DocumentTemplateVersion')
    database = schema_editor.connection.alias
    category_names = {
        'client': 'Client & Business Documents',
        'freelancer': 'Freelancer & Consultant Documents',
        'candidate': 'Recruitment & Candidate Documents',
        'employee': 'Employee & HR Documents',
        'internal': 'Internal Administration Documents',
    }
    for code, name in category_names.items():
        DocumentCategory.objects.using(database).filter(code=code).update(name=name)
    freelancer = DocumentCategory.objects.using(database).get(code='freelancer')
    consultant = DocumentCategory.objects.using(database).get(code='consultant')
    freelancer.document_types.add(*consultant.document_types.using(database).all())
    consultant.is_active = False
    consultant.save(using=database, update_fields=('is_active',))
    onboarding, _ = DocumentCategory.objects.using(database).get_or_create(
        code='employee-onboarding-exit',
        defaults={'name': 'Employee Onboarding & Exit Documents', 'is_active': True},
    )
    onboarding_types = DocumentType.objects.using(database).filter(code__in=(
        'joining-checklist', 'employee-document-checklist', 'asset-handover-receipt',
        'asset-return-receipt', 'knowledge-transfer-certificate', 'employee-exit-checklist',
        'handover-checklist', 'employee-clearance-form',
    ))
    for document_type in onboarding_types:
        document_type.categories.add(onboarding)

    for type_code, definitions in FIELD_SETS.items():
        document_type = DocumentType.objects.using(database).filter(code=type_code).first()
        if not document_type:
            continue
        for order, (key, label, value_type, required, source, source_field) in enumerate(definitions):
            DocumentFieldDefinition.objects.using(database).get_or_create(
                document_type=document_type,
                field_key=key,
                defaults={
                    'label': label,
                    'value_type': value_type,
                    'is_required': required,
                    'is_active': True,
                    'source_module': source,
                    'source_field': source_field,
                    'is_auto_populated': source != 'manual',
                    'display_order': order,
                },
            )
        template, _ = DocumentTemplate.objects.using(database).get_or_create(
            document_type=document_type,
            name='Starter template (review required)',
            defaults={'is_active': True},
        )
        field_lines = '\n'.join(
            f'{{{{ {key} }}}}' for key, *_ in definitions
        )
        body = (
            '## Document information\n'
            '{{ company_display_name }}\n\n'
            'Document number: {{ document_number }}\n'
            'Document date: {{ document_date }}\n'
            'Effective date: {{ effective_date }}\n\n'
            '## Document particulars\n'
            f'{field_lines}'
        )
        DocumentTemplateVersion.objects.using(database).get_or_create(
            template=template,
            version_number=1,
            defaults={
                'status': 'draft',
                'body_template': body,
                'signature_configuration': {'review_required': True},
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('document_generation', '0006_documentfielddefinition_choices_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_fields, migrations.RunPython.noop),
    ]
