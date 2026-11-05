"""Seed data for the first reviewed document-library release."""


def field(key, label, value_type='text', required=False, source='manual', source_field='',
          condition=None, help_text=''):
    return {
        'field_key': key,
        'label': label,
        'value_type': value_type,
        'is_required': required,
        'source_module': source,
        'source_field': source_field,
        'is_auto_populated': source != 'manual',
        'visibility_condition': condition or {},
        'help_text': help_text,
    }


EMPLOYEE_FIELDS = (
    field('employee_name', 'Employee name', required=True, source='employee', source_field='name'),
    field('employee_id', 'Employee ID', required=True, source='employee', source_field='employee_id'),
    field('designation', 'Designation', required=True, source='employee', source_field='designation'),
    field('department', 'Department', source='employee', source_field='department'),
    field('reporting_manager', 'Reporting manager', source='employee', source_field='reporting_manager'),
    field('work_location', 'Work location', source='employee', source_field='work_location'),
    field('date_of_joining', 'Date of joining', 'date', source='employee', source_field='date_of_joining'),
)

CLIENT_FIELDS = (
    field('client_name', 'Client name', required=True, source='client', source_field='name'),
    field('client_address', 'Client address', 'multiline', source='client', source_field='address'),
    field('client_contact_person', 'Client contact person', source='client', source_field='contact_person'),
    field('client_email', 'Client email', 'email', source='client', source_field='email'),
    field('client_phone', 'Client phone', source='client', source_field='phone'),
    field('client_gstin', 'Client GSTIN', source='client', source_field='gstin'),
)

FREELANCER_PARTY_FIELDS = (
    field('freelancer_name', 'Freelancer / consultant name', required=True),
    field('freelancer_address', 'Freelancer address', 'multiline', required=True),
    field('freelancer_email', 'Freelancer email', 'email'),
    field('freelancer_phone', 'Freelancer phone'),
)


FIELD_SETS = {
    'non-disclosure-agreement-nda': (
        field('first_party_name', 'First party', required=True, source='company', source_field='display_name'),
        field('second_party_name', 'Second party', required=True, source='client', source_field='name'),
        field('purpose_of_disclosure', 'Purpose of disclosure', 'multiline', True),
        field('confidential_information', 'Confidential information', 'multiline', True),
        field('agreement_duration', 'Agreement duration'),
        field('confidentiality_period', 'Confidentiality survival period', required=True),
        field('permitted_disclosures', 'Permitted disclosures', 'multiline'),
        field('governing_law', 'Governing law'),
        field('jurisdiction', 'Jurisdiction', source='company', source_field='jurisdiction'),
    ),
    'mutual-nda': (
        field('first_party_name', 'First party', required=True, source='company', source_field='display_name'),
        field('second_party_name', 'Second party', required=True),
        field('second_party_address', 'Second party address', 'multiline'),
        field('purpose_of_disclosure', 'Purpose of disclosure', 'multiline', True),
        field('confidential_information', 'Confidential information', 'multiline', True),
        field('agreement_duration', 'Agreement duration'),
        field('confidentiality_period', 'Confidentiality survival period', required=True),
        field('permitted_disclosures', 'Permitted disclosures', 'multiline'),
        field('governing_law', 'Governing law'),
        field('jurisdiction', 'Jurisdiction', source='company', source_field='jurisdiction'),
    ),
    'master-client-agreement-mca': (
        *CLIENT_FIELDS,
        field('services_description', 'Services and service-specific scope', 'multiline', True),
        field('sow_process', 'Statement of Work process', 'multiline', True),
        field('provider_responsibilities', 'Provider responsibilities', 'multiline', True),
        field('client_responsibilities', 'Client responsibilities', 'multiline', True),
        field('fees_and_commercial_terms', 'Fees and commercial terms', 'multiline', True),
        field('invoice_payment_terms', 'Invoicing and payment terms', 'multiline', True),
        field('tax_terms', 'Tax and statutory deduction handling', 'multiline'),
        field('acceptance_criteria', 'Acceptance criteria', 'multiline'),
        field('term_and_termination', 'Term and termination terms', 'multiline', True),
        field('notices_method', 'Notice delivery method and addresses', 'multiline', True),
        field('governing_law', 'Governing law'),
        field('jurisdiction', 'Jurisdiction', source='company', source_field='jurisdiction'),
    ),
    'statement-of-work-sow': (
        *CLIENT_FIELDS,
        field('project_name', 'Project name', required=True),
        field('project_background', 'Project background', 'multiline'),
        field('scope_of_work', 'Scope of work', 'multiline', True),
        field('deliverables', 'Deliverables', 'multiline', True),
        field('project_milestones', 'Milestones', 'multiline', True),
        field('timeline', 'Timeline', 'multiline', True),
        field('resource_details', 'Resources', 'multiline'),
        field('dependencies', 'Dependencies and assumptions', 'multiline'),
        field('acceptance_criteria', 'Acceptance criteria', 'multiline', True),
        field('commercial_terms', 'Commercial terms', 'multiline', True),
        field('payment_milestones', 'Payment milestones', 'multiline'),
        field('client_responsibilities', 'Client responsibilities', 'multiline', True),
        field('change_management', 'Change management', 'multiline'),
    ),
    'service-level-agreement-sla': (
        *CLIENT_FIELDS,
        field('services_covered', 'Services covered', 'multiline', True),
        field('support_hours', 'Support hours and timezone', required=True),
        field('severity_levels', 'Incident severity levels', 'multiline', True),
        field('response_targets', 'Response targets', 'multiline', True),
        field('resolution_targets', 'Resolution targets', 'multiline', True),
        field('escalation_matrix', 'Escalation matrix', 'multiline'),
        field('availability_target', 'Availability target', help_text='Enter only if agreed and measurable.'),
        field('service_exclusions', 'Service exclusions', 'multiline'),
        field('reporting_review', 'Reporting and review frequency', 'multiline'),
        field('sla_remedies', 'Breach and remedies', 'multiline'),
    ),
    'freelancer-agreement': (
        *FREELANCER_PARTY_FIELDS,
        field('project_name', 'Project name', required=True),
        field('project_description', 'Project background', 'multiline'),
        field('scope_of_work', 'Scope of services', 'multiline', True),
        field('deliverables', 'Deliverables', 'multiline', True),
        field('project_start_date', 'Start date', 'date'),
        field('expected_completion_date', 'Expected completion date', 'date'),
        field('working_arrangements', 'Working arrangements', 'multiline'),
        field('payment_amount', 'Fees', 'currency', True),
        field('payment_milestones', 'Payment milestones', 'multiline'),
        field('payment_terms', 'Invoice and payment process', 'multiline', True),
        field('tax_obligations', 'Tax and statutory obligations', 'multiline'),
        field('ip_ownership_terms', 'Intellectual property terms', 'multiline', True),
        field('security_obligations', 'Data protection and security obligations', 'multiline'),
        field('termination_conditions', 'Termination and handover terms', 'multiline', True),
    ),
    'freelancer-nda': (
        *FREELANCER_PARTY_FIELDS,
        field('project_name', 'Project / engagement', required=True),
        field('confidential_information', 'Confidential information', 'multiline', True),
        field('permitted_disclosures', 'Permitted disclosures', 'multiline'),
        field('data_protection_terms', 'Data protection and security', 'multiline', True),
        field('return_destruction_terms', 'Return or destruction of information', 'multiline', True),
        field('confidentiality_period', 'Confidentiality period', required=True),
        field('breach_consequences', 'Breach handling and remedies', 'multiline'),
        field('governing_law', 'Governing law'),
        field('jurisdiction', 'Jurisdiction'),
    ),
    'intellectual-property-assignment': (
        *FREELANCER_PARTY_FIELDS,
        field('work_description', 'Description of work', 'multiline', True),
        field('deliverables', 'Deliverables and documentation', 'multiline', True),
        field('source_code_included', 'Source code included', 'boolean'),
        field('pre_existing_ip', 'Pre-existing intellectual property exclusions', 'multiline', True),
        field('third_party_components', 'Third-party and open-source components', 'multiline', True),
        field('assignment_scope', 'Rights assigned and territory / duration', 'multiline', True),
        field('consideration', 'Consideration, if applicable', 'multiline'),
        field('cooperation_terms', 'Further assurances and cooperation', 'multiline'),
        field('governing_law', 'Governing law'),
    ),
    'freelancer-work-order': (
        *FREELANCER_PARTY_FIELDS,
        field('project_name', 'Project name', required=True),
        field('scope_of_work', 'Scope', 'multiline', True),
        field('deliverables', 'Deliverables', 'multiline', True),
        field('start_date', 'Start date', 'date', True),
        field('completion_date', 'Completion date', 'date', True),
        field('commercial_terms', 'Fees and commercial terms', 'multiline', True),
        field('milestones', 'Milestones', 'multiline'),
        field('reporting_requirements', 'Reporting requirements', 'multiline'),
        field('acceptance_criteria', 'Acceptance criteria', 'multiline', True),
        field('work_order_signatory', 'Authorised signatory name', required=True),
    ),
    'offer-letter': (
        field('candidate_name', 'Candidate name', required=True),
        field('candidate_address', 'Candidate address', 'multiline'),
        field('designation', 'Designation', required=True),
        field('department', 'Department'),
        field('employment_type', 'Employment type', required=True),
        field('reporting_manager', 'Reporting manager'),
        field('work_location', 'Work location'),
        field('joining_date', 'Joining date', 'date', True),
        field('annual_ctc', 'Annual CTC', 'currency', True),
        field('monthly_salary', 'Monthly salary, if applicable', 'currency'),
        field('salary_structure_reference', 'Salary structure reference'),
        field('probation_period', 'Probation period'),
        field('working_hours', 'Working hours'),
        field('notice_period', 'Notice period'),
        field('leave_benefits_reference', 'Leave and benefits reference'),
        field('conditions_of_employment', 'Conditions of employment', 'multiline', True),
        field('required_documents', 'Required documents', 'multiline'),
        field('offer_valid_until', 'Offer acceptance deadline', 'date'),
    ),
    'conditional-offer-letter': (
        field('candidate_name', 'Candidate name', required=True),
        field('candidate_address', 'Candidate address', 'multiline'),
        field('candidate_email', 'Candidate email', 'email'),
        field('designation', 'Designation', required=True),
        field('department', 'Department'),
        field('employment_type', 'Employment type', required=True),
        field('joining_date', 'Proposed joining date', 'date'),
        field('annual_ctc', 'Annual CTC', 'currency'),
        field('conditions_of_employment', 'Conditions to be satisfied', 'multiline', True),
        field('condition_deadline', 'Condition completion deadline', 'date'),
        field('offer_valid_until', 'Offer acceptance deadline', 'date'),
    ),
    'appointment-letter': (
        *EMPLOYEE_FIELDS,
        field('appointment_date', 'Appointment date', 'date', True),
        field('employment_type', 'Employment type', source='employee', source_field='employment_type'),
        field('compensation', 'Compensation and salary structure reference', 'multiline', True),
        field('probation_period', 'Probation period'),
        field('working_hours', 'Working hours'),
        field('leave_policy_reference', 'Leave policy reference'),
        field('notice_period', 'Notice period'),
        field('applicable_policies', 'Applicable policies', 'multiline'),
        field('employment_conditions', 'Additional employment conditions', 'multiline'),
    ),
    'joining-letter': (
        *EMPLOYEE_FIELDS,
        field('document_submission_checklist', 'Document submission checklist', 'multiline'),
        field('policy_acknowledgement', 'Company policy acknowledgement', 'multiline'),
        field('hr_acknowledgement', 'HR acknowledgement', 'multiline'),
    ),
    'employee-nda': (
        *EMPLOYEE_FIELDS,
        field('confidential_information', 'Confidential information', 'multiline', True),
        field('security_obligations', 'Information security obligations', 'multiline', True),
        field('systems_access', 'Company systems and access', 'multiline'),
        field('ip_ownership_terms', 'Intellectual property and source code', 'multiline', True),
        field('property_return_terms', 'Return of company property', 'multiline', True),
        field('post_employment_period', 'Post-employment confidentiality period'),
    ),
    'probation-letter': (
        *EMPLOYEE_FIELDS,
        field('probation_start_date', 'Probation commencement date', 'date', True),
        field('probation_duration', 'Probation duration', required=True),
        field('performance_expectations', 'Performance expectations', 'multiline', True),
        field('review_date', 'Review date', 'date', True),
        field('confirmation_criteria', 'Confirmation criteria', 'multiline', True),
        field('extension_provisions', 'Extension provisions'),
    ),
    'confirmation-letter': (
        *EMPLOYEE_FIELDS,
        field('confirmation_effective_date', 'Confirmation effective date', 'date', True),
        field('probation_completion_date', 'Probation completion date', 'date'),
        field('applicable_terms', 'Applicable employment terms', 'multiline'),
    ),
    'salary-revision-letter': (
        *EMPLOYEE_FIELDS,
        field('existing_designation', 'Existing designation'),
        field('revised_designation', 'Revised designation'),
        field('existing_salary', 'Existing salary', 'currency', True),
        field('revised_salary', 'Revised salary', 'currency', True),
        field('salary_effective_date', 'Effective date', 'date', True),
        field('salary_breakdown_reference', 'Salary breakdown reference'),
    ),
    'promotion-letter': (
        *EMPLOYEE_FIELDS,
        field('existing_designation', 'Existing designation', required=True),
        field('new_designation', 'New designation', required=True),
        field('promotion_effective_date', 'Effective date', 'date', True),
        field('revised_compensation', 'Revised compensation, if applicable', 'currency'),
        field('new_responsibilities', 'New responsibilities', 'multiline', True),
    ),
    'warning-letter': (
        *EMPLOYEE_FIELDS,
        field('reference_date', 'Reference date', 'date', True),
        field('issue_description', 'Description of concern (not a finding)', 'multiline', True),
        field('policy_reference', 'Relevant policy or employment provision'),
        field('explanation_requested', 'Explanation requested', 'multiline', True),
        field('response_deadline', 'Response deadline', 'date', True),
        field('next_steps', 'Potential next steps', 'multiline'),
    ),
    'show-cause-notice': (
        *EMPLOYEE_FIELDS,
        field('reference_date', 'Reference date', 'date', True),
        field('issue_description', 'Allegation / issue for response', 'multiline', True),
        field('policy_reference', 'Relevant policy or employment provision'),
        field('explanation_requested', 'Specific explanation requested', 'multiline', True),
        field('response_deadline', 'Response deadline', 'date', True),
        field('next_steps', 'Next procedural steps', 'multiline'),
    ),
    'experience-certificate': (
        *EMPLOYEE_FIELDS,
        field('last_working_date', 'Last working date', 'date', source='employee', source_field='last_working_date'),
        field('service_description', 'Role / service description', 'multiline'),
    ),
    'relieving-letter': (
        *EMPLOYEE_FIELDS,
        field('resignation_date', 'Resignation date', 'date'),
        field('last_working_date', 'Last working date', 'date', True, 'employee', 'last_working_date'),
        field('handover_status', 'Handover status (verified)', 'multiline'),
        field('clearance_status', 'Clearance status (verified)', 'multiline'),
        field('property_return_status', 'Company property return status (verified)', 'multiline'),
        field('pending_obligations', 'Pending obligations, if any', 'multiline'),
    ),
    'resignation-acceptance-letter': (
        *EMPLOYEE_FIELDS,
        field('resignation_submission_date', 'Resignation submission date', 'date', True),
        field('accepted_resignation_date', 'Accepted resignation date', 'date', True),
        field('last_working_date', 'Last working date', 'date', True),
        field('handover_expectations', 'Handover expectations', 'multiline'),
        field('clearance_requirements', 'Clearance requirements', 'multiline'),
    ),
    'salary-certificate': (
        *EMPLOYEE_FIELDS,
        field('employment_period', 'Employment period', required=True),
        field('salary_period', 'Salary period covered', required=True),
        field('salary_details', 'Salary details for requested period', 'multiline', True),
        field('certificate_purpose', 'Purpose of certificate'),
        field('salary_confidentiality_note', 'Confidentiality note'),
    ),
    'no-dues-certificate': (
        *EMPLOYEE_FIELDS,
        field('last_working_date', 'Last working date', 'date', source='employee', source_field='last_working_date'),
        field('department_clearance', 'Department-wise clearance (verified)', 'multiline', True),
        field('asset_clearance', 'Asset return status (verified)', 'multiline', True),
        field('finance_clearance', 'Finance clearance (verified)', 'multiline', True),
        field('hr_clearance', 'HR clearance (verified)', 'multiline', True),
        field('it_clearance', 'IT clearance (verified)', 'multiline', True),
        field('final_approval', 'Final approval reference', required=True),
    ),
    'asset-handover-receipt': (
        field('employee_name', 'Employee name', required=True, source='employee', source_field='name'),
        field('employee_id', 'Employee ID', required=True, source='employee', source_field='employee_id'),
        field('asset_name', 'Asset name', required=True, source='asset', source_field='name'),
        field('asset_code', 'Asset ID', required=True, source='asset', source_field='asset_code'),
        field('serial_number', 'Serial number', source='asset', source_field='serial_number'),
        field('brand_and_model', 'Make and model', source='asset', source_field='brand_and_model'),
        field('accessories_issued', 'Accessories issued', 'multiline'),
        field('issue_condition', 'Condition at handover', required=True),
        field('issue_date', 'Issue date', 'date', True),
        field('expected_return_date', 'Expected return date', 'date'),
        field('acknowledgement_text', 'Employee acknowledgement', 'multiline', True),
        field('issuing_authority', 'Issuing authority', required=True),
    ),
    'asset-return-receipt': (
        field('employee_name', 'Employee name', required=True, source='employee', source_field='name'),
        field('employee_id', 'Employee ID', required=True, source='employee', source_field='employee_id'),
        field('asset_name', 'Asset name', required=True, source='asset', source_field='name'),
        field('asset_code', 'Asset ID', required=True, source='asset', source_field='asset_code'),
        field('serial_number', 'Serial number', source='asset', source_field='serial_number'),
        field('return_date', 'Return date', 'date', True),
        field('return_condition', 'Condition at return', required=True),
        field('accessories_returned', 'Accessories returned', 'multiline'),
        field('damage_missing_items', 'Damage or missing items', 'multiline'),
        field('return_remarks', 'Remarks', 'multiline'),
        field('received_by', 'Received by', required=True),
        field('employee_acknowledgement', 'Employee acknowledgement', 'multiline', True),
    ),
    'joining-checklist': (
        *EMPLOYEE_FIELDS,
        field('identity_documents_status', 'Identity documents', 'text', help_text='Do not enter full identity numbers.'),
        field('address_documents_status', 'Address documents', 'text', help_text='Do not enter sensitive document numbers.'),
        field('pan_documents_status', 'PAN document verification status'),
        field('bank_details_status', 'Bank details verification status'),
        field('education_documents_status', 'Education documents status'),
        field('previous_employment_status', 'Previous employment documents status'),
        field('agreements_status', 'Employment agreements status'),
        field('policy_acknowledgements', 'Policy acknowledgements', 'multiline'),
        field('asset_issue_status', 'Asset issue status'),
        field('it_access_status', 'IT access status'),
        field('hr_completion_status', 'HR completion status'),
    ),
    'employee-clearance-form': (
        *EMPLOYEE_FIELDS,
        field('last_working_date', 'Last working date', 'date', source='employee', source_field='last_working_date'),
        field('hr_clearance', 'HR clearance status', required=True),
        field('finance_clearance', 'Finance clearance status', required=True),
        field('it_clearance', 'IT clearance status', required=True),
        field('asset_clearance', 'Asset clearance status', required=True),
        field('department_clearance', 'Department clearance status', required=True),
        field('knowledge_transfer_status', 'Knowledge transfer status'),
        field('pending_obligations', 'Pending obligations', 'multiline'),
        field('final_approval', 'Final approval', required=True),
    ),
    'knowledge-transfer-certificate': (
        *EMPLOYEE_FIELDS,
        field('project_details', 'Project details', 'multiline', True),
        field('handover_topics', 'Handover topics', 'multiline', True),
        field('documentation_status', 'Documentation status', 'multiline'),
        field('access_transfer_status', 'Access transfer status', 'multiline'),
        field('pending_tasks', 'Pending tasks', 'multiline'),
        field('receiving_person', 'Receiving person', required=True),
        field('completion_date', 'Completion date', 'date', True),
        field('approval_reference', 'Approval reference'),
    ),
}


CLAUSES = (
    ('confidentiality', 'Confidentiality', 'Confidentiality',
     'Each party receiving non-public information will use it only for the agreed purpose, protect it with reasonable care, and limit access to people who need it and are subject to appropriate confidentiality duties. The parties should identify the information and any applicable duration in the transaction-specific terms.'),
    ('intellectual-property', 'Intellectual Property', 'Intellectual property',
     'Ownership and permitted use of pre-existing materials, work product, and deliverables must be expressly recorded in the applicable statement of work or assignment. No transfer or licence is implied beyond the terms expressly agreed in writing.'),
    ('source-code-ownership', 'Source Code Ownership', 'Intellectual property',
     'The parties must specify whether source code is a deliverable, the rights being transferred or licensed, and any conditions for transfer. Third-party and open-source components remain subject to their own licence terms.'),
    ('payment-terms', 'Payment Terms', 'Commercial',
     'Fees, currency, milestones, invoice requirements, taxes, and due dates are those expressly stated in the applicable order or statement of work. No amount or payment period is implied by this clause.'),
    ('late-payment', 'Late Payment', 'Commercial',
     'Any interest, suspension right, or other consequence of late payment must be expressly agreed in the commercial schedule and reviewed for applicable law before use.'),
    ('termination', 'Termination', 'Term and exit',
     'Termination rights, notice, cure periods, and consequences must be stated in the applicable agreement. On termination, the parties should address accrued payment, transition assistance, return of property, and ongoing confidentiality duties.'),
    ('notice-period', 'Notice Period', 'Employment',
     'Any notice period must be supplied from the applicable employment terms and verified before issue. This clause does not create a notice period where none has been agreed.'),
    ('non-solicitation', 'Non-Solicitation', 'Restrictive terms',
     'Use only where specifically negotiated and reviewed by qualified counsel for the relevant facts and applicable law. Define the restricted conduct, persons, duration, and permitted exceptions in a separate approved schedule. No non-compete restriction is created by this clause.'),
    ('conflict-of-interest', 'Conflict of Interest', 'Conduct',
     'A person must promptly disclose an actual or reasonably apparent conflict relevant to the engagement and cooperate on proportionate steps agreed by the parties.'),
    ('data-protection', 'Data Protection', 'Privacy and security',
     'Each party will handle personal data only for authorised purposes, apply appropriate safeguards, limit access, and notify the other party of a relevant incident as required by the agreed data-processing terms and applicable law.'),
    ('information-security', 'Information Security', 'Privacy and security',
     'Each party will maintain reasonable administrative, technical, and organisational safeguards appropriate to the information and services, and will follow documented access, retention, and incident-response requirements agreed for the engagement.'),
    ('indemnification', 'Indemnification', 'Risk allocation',
     'Any indemnity, covered claims, procedures, exclusions, and limits must be set out in the transaction-specific agreement and reviewed before approval. This clause does not create an unbounded indemnity.'),
    ('limitation-of-liability', 'Limitation of Liability', 'Risk allocation',
     'Liability caps, excluded loss categories, and exceptions must be expressly negotiated and reviewed for enforceability and applicable law. Do not issue this agreement until the chosen terms are complete.'),
    ('force-majeure', 'Force Majeure', 'General',
     'A party affected by an event beyond its reasonable control will notify the other party, take reasonable steps to mitigate disruption, and resume performance when practicable. Any suspension or termination right must be stated in the agreement.'),
    ('dispute-resolution', 'Dispute Resolution', 'General',
     'The parties should first refer a dispute to authorised representatives for good-faith discussion. Any further forum, arbitration process, seat, and rules must be expressly completed and reviewed before issue.'),
    ('governing-law', 'Governing Law', 'General',
     'The governing law must be selected for the transaction and entered in the applicable field. This template does not select or imply a governing law.'),
    ('jurisdiction', 'Jurisdiction', 'General',
     'The courts or other forum with jurisdiction must be identified in the transaction-specific terms after review. This template does not select or imply a forum.'),
    ('return-company-property', 'Return of Company Property', 'Employment and exit',
     'On request or at the end of the relevant engagement, the recipient will return company property and information in their possession, subject to documented retention obligations and verification of the return.'),
    ('employee-responsibilities', 'Employee Responsibilities', 'Employment',
     'The employee will perform assigned duties with reasonable care, follow applicable policies communicated by the employer, protect company information, and promptly raise material workplace or security concerns.'),
    ('independent-contractor', 'Independent Contractor Relationship', 'Engagement',
     'The consultant provides services as an independent contractor and is not classified as an employee by this document alone. The parties must ensure that the actual working arrangement and applicable law support the intended relationship.'),
    ('project-acceptance', 'Project Acceptance', 'Delivery',
     'Deliverables, review period, objective acceptance criteria, defect handling, and deemed acceptance (if any) must be defined in the relevant statement of work. Do not infer acceptance solely from silence unless expressly agreed and reviewed.'),
    ('change-management', 'Change Management', 'Delivery',
     'Changes to scope, schedule, resources, or fees should be documented in a written change request approved by authorised representatives before the changed work begins.'),
    ('warranty-disclaimer', 'Warranty and Disclaimer', 'Risk allocation',
     'Any service warranty, remedy, duration, and disclaimer must be stated specifically in the applicable agreement. Do not imply a warranty or disclaimer beyond terms that have been reviewed and accepted.'),
    ('third-party-open-source', 'Third-Party and Open-Source Components', 'Intellectual property',
     'The provider should identify material third-party or open-source components and applicable licence obligations where relevant. Each party will comply with licences applicable to components it uses or distributes.'),
)


TEMPLATES = {
    'non-disclosure-agreement-nda': {
        'name': 'Client Non-Disclosure Agreement',
        'body': """## 1. Parties and purpose
This agreement is between {{ first_party_name }} ({{ first_party_address }}) and {{ second_party_name }} ({{ second_party_address }}). Its purpose is {{ purpose_of_disclosure }}.

## 2. Information covered
Confidential information includes: {{ confidential_information }}. The receiving party will use it only for the stated purpose, protect it with reasonable care, and restrict access to authorised persons.

## 3. Permitted disclosure and exclusions
{{ permitted_disclosures }}
Information independently developed, already lawfully known, publicly available without breach, or lawfully received from another source is not confidential to the extent the receiving party can demonstrate that status. A compelled disclosure may be made only to the extent legally required, with prior notice where lawful.

## 4. Protection and permitted use
No intellectual-property right is transferred by disclosure. The parties will apply appropriate data-protection and information-security controls and promptly address suspected unauthorised access.

## 5. Duration and return
Agreement duration: {{ agreement_duration }}. Confidentiality survival period: {{ confidentiality_period }}. On request or termination, information will be returned or securely destroyed, subject to lawful retention.

## 6. Remedies and relationship
The parties may seek remedies available under applicable law; no outcome or remedy is guaranteed by this draft. Disclosure does not create a partnership, employment, or agency relationship.

## 7. Governing law and dispute resolution
Governing law: {{ governing_law }}. Jurisdiction / forum: {{ jurisdiction }}. Complete and review these terms before issue.

## 8. Signatures
Each signatory confirms authority to sign for the relevant party. This document remains unsigned until completed and executed by the parties.""",
        'clauses': ('confidentiality', 'data-protection', 'information-security', 'governing-law', 'jurisdiction'),
        'parties': ('issuer', 'counterparty'),
    },
    'mutual-nda': {
        'name': 'Mutual Non-Disclosure Agreement',
        'body': """## 1. Parties and purpose
This mutual agreement is between {{ first_party_name }} and {{ second_party_name }}. Each party may disclose information for {{ purpose_of_disclosure }}.

## 2. Mutual obligations
Each receiving party will use the other party's confidential information only for the stated purpose, protect it with reasonable care, and disclose it only to authorised persons with a need to know.

## 3. Confidential information and exclusions
{{ confidential_information }}
Information independently developed, publicly available without breach, already lawfully known, or lawfully received from another source is excluded where the receiving party can demonstrate the exclusion. Compelled disclosure is limited to the legally required extent.

## 4. Permitted disclosure, security and ownership
{{ permitted_disclosures }}
Each party will apply appropriate safeguards. No intellectual-property right is transferred by disclosure.

## 5. Duration, return and remedies
Agreement duration: {{ agreement_duration }}. Survival period: {{ confidentiality_period }}. On request, information will be returned or securely destroyed except where retention is legally required. Remedies remain subject to applicable law.

## 6. Governing law and dispute resolution
Governing law: {{ governing_law }}. Jurisdiction / forum: {{ jurisdiction }}. Complete and review these terms before issue.

## 7. Signatures
Each signatory confirms authority for the party represented. This draft is not executed until signed.""",
        'clauses': ('confidentiality', 'data-protection', 'information-security', 'dispute-resolution', 'governing-law', 'jurisdiction'),
        'parties': ('issuer', 'counterparty'),
    },
    'master-client-agreement-mca': {
        'name': 'Master Client Services Agreement',
        'body': """## 1. Parties and background
This agreement is between {{ company_display_name }} and {{ client_name }} (the Client). Client address: {{ client_address }}. Client contact: {{ client_contact_person }}; {{ client_email }}; {{ client_phone }}; GSTIN {{ client_gstin }}. Effective date: {{ effective_date }}.

## 2. Purpose and service scope
The parties may use this agreement for IT services, software development, recruitment, staffing, consulting, or technology solutions only as selected for each engagement. Services: {{ services_description }}.

## 3. Statements of Work and delivery
{{ sow_process }}
Each statement of work should identify scope, deliverables, resources, timeline, dependencies, acceptance criteria, and commercial terms. No unlisted service is implied.

## 4. Responsibilities
Provider responsibilities: {{ provider_responsibilities }}
Client responsibilities: {{ client_responsibilities }}

## 5. Fees, invoicing and taxes
Fees: {{ fees_and_commercial_terms }}
Invoicing and payment: {{ invoice_payment_terms }}
Tax and statutory deduction handling: {{ tax_terms }}

## 6. Change, acceptance and service-specific terms
Acceptance criteria: {{ acceptance_criteria }}
Changes require a written change request approved by both parties. Any recruitment, staffing, software, or consulting-specific terms must be included in the applicable statement of work.

## 7. Term, termination and consequences
{{ term_and_termination }}
On termination, the parties will address accrued amounts, transition, return of property, and continuing obligations in accordance with the reviewed agreement.

## 8. General and execution
Confidentiality, intellectual property, data protection, security, warranties, indemnities, liability limits, force majeure, non-solicitation (if applicable), Notices: {{ notices_method }}. Dispute resolution, governing law, and jurisdiction must be completed from approved terms before issue. This draft creates no unfilled commercial term.""",
        'clauses': ('confidentiality', 'intellectual-property', 'payment-terms', 'data-protection', 'information-security', 'project-acceptance', 'change-management', 'warranty-disclaimer', 'indemnification', 'limitation-of-liability', 'force-majeure', 'dispute-resolution', 'governing-law', 'jurisdiction'),
        'parties': ('issuer', 'counterparty'),
    },
    'statement-of-work-sow': {
        'name': 'Statement of Work',
        'body': """## 1. Parties and project
Client: {{ client_name }}. Project: {{ project_name }}. Effective date: {{ effective_date }}.

## 2. Background and scope
{{ project_background }}
{{ scope_of_work }}

## 3. Deliverables and milestones
{{ deliverables }}
Milestones: {{ project_milestones }}
Timeline: {{ timeline }}
Resources: {{ resource_details }}

## 4. Dependencies and responsibilities
Dependencies and assumptions: {{ dependencies }}
Client responsibilities: {{ client_responsibilities }}

## 5. Acceptance and change management
{{ acceptance_criteria }}
{{ change_management }}

## 6. Commercial terms
{{ commercial_terms }}
Payment milestones: {{ payment_milestones }}
Any taxes, currency, invoice timing, and payment due dates must be expressly completed in the commercial schedule.

## 7. Signatures
Authorised representatives approve this SOW subject to the governing master agreement. No work outside the agreed scope is authorised by this draft.""",
        'clauses': ('project-acceptance', 'change-management', 'payment-terms', 'confidentiality', 'intellectual-property'),
        'parties': ('issuer', 'counterparty'),
    },
    'service-level-agreement-sla': {
        'name': 'Service Level Agreement',
        'body': """## 1. Services covered
Client: {{ client_name }}. Covered services: {{ services_covered }}.

## 2. Support and incidents
Support hours and timezone: {{ support_hours }}.
Severity levels: {{ severity_levels }}
Response targets: {{ response_targets }}
Resolution targets: {{ resolution_targets }}
Escalation matrix: {{ escalation_matrix }}

## 3. Availability, exclusions and reporting
Availability target, if applicable: {{ availability_target }}
Exclusions: {{ service_exclusions }}
Reporting and review frequency: {{ reporting_review }}

## 4. Responsibilities and remedies
The parties will provide timely cooperation, access, and information reasonably required to deliver and support the covered services. Breach handling and remedies: {{ sla_remedies }}.
All targets must be measurable, agreed, and aligned with the underlying services contract before approval.""",
        'clauses': ('project-acceptance', 'change-management', 'warranty-disclaimer', 'limitation-of-liability'),
        'parties': ('issuer', 'counterparty'),
    },
    'freelancer-agreement': {
        'name': 'Freelancer / Consultant Agreement',
        'body': """## 1. Parties and engagement
Provider: {{ company_display_name }}. Consultant: {{ freelancer_name }}. Address: {{ freelancer_address }}. Email: {{ freelancer_email }}. Project: {{ project_name }}.

## 2. Scope, deliverables and duration
{{ project_description }}
Services: {{ scope_of_work }}
Deliverables: {{ deliverables }}
Start date: {{ project_start_date }}. Expected completion: {{ expected_completion_date }}.

## 3. Working arrangements and fees
{{ working_arrangements }}
Fees: {{ payment_amount }}
Milestones: {{ payment_milestones }}
Invoice and payment process: {{ payment_terms }}
Tax and statutory obligations: {{ tax_obligations }}

## 4. Confidentiality, data and intellectual property
{{ confidentiality_requirements }}
{{ security_obligations }}
{{ ip_ownership_terms }}
No intellectual-property transfer is implied beyond the terms expressly agreed for identified deliverables.

## 5. Independent relationship and conflicts
The parties intend an independent contractor relationship, not employment. Actual working arrangements must remain consistent with applicable law. Conflicts of interest should be disclosed promptly.

## 6. Termination and handover
{{ termination_conditions }}
The consultant will return company property and provide agreed handover materials. Any non-solicitation restriction must be separately negotiated and legally reviewed.""",
        'clauses': ('independent-contractor', 'confidentiality', 'intellectual-property', 'source-code-ownership', 'data-protection', 'information-security', 'conflict-of-interest', 'payment-terms', 'termination', 'return-company-property', 'dispute-resolution', 'governing-law'),
        'parties': ('issuer', 'counterparty'),
    },
    'freelancer-nda': {
        'name': 'Freelancer Confidentiality Agreement',
        'body': """## 1. Parties and engagement
Provider: {{ company_display_name }}. Freelancer / consultant: {{ freelancer_name }}. Address: {{ freelancer_address }}. Project: {{ project_name }}.

## 2. Confidential information and use
{{ confidential_information }}
The recipient will use information only for the engagement, restrict access to authorised persons, and apply reasonable safeguards.

## 3. Permitted disclosure and data protection
{{ permitted_disclosures }}
Data protection and security requirements: {{ data_protection_terms }}

## 4. Return, duration and breach
{{ return_destruction_terms }}
Confidentiality period: {{ confidentiality_period }}.
Breach handling and remedies: {{ breach_consequences }}

## 5. Governing law and signatures
Governing law: {{ governing_law }}. Jurisdiction: {{ jurisdiction }}. Complete and legally review all transaction terms before use.""",
        'clauses': ('confidentiality', 'data-protection', 'information-security', 'return-company-property', 'governing-law'),
        'parties': ('issuer', 'counterparty'),
    },
    'intellectual-property-assignment': {
        'name': 'Intellectual Property Assignment',
        'body': """## 1. Parties and work
Assignor: {{ freelancer_name }}. Address: {{ freelancer_address }}. Assignee: {{ company_display_name }}.
Work: {{ work_description }}
Deliverables and documentation: {{ deliverables }}

## 2. Rights and scope
Assignment scope, rights, territory, and duration: {{ assignment_scope }}.
Source code included: {{ source_code_included }}.
Any transfer is limited to the rights expressly identified and remains subject to applicable law.

## 3. Excluded and third-party material
Pre-existing intellectual property: {{ pre_existing_ip }}
Third-party and open-source components: {{ third_party_components }}

## 4. Consideration and further cooperation
{{ consideration }}
{{ cooperation_terms }}

## 5. Confidentiality and governing law
The parties will protect confidential information. Governing law: {{ governing_law }}.
This draft requires review of ownership, scope, consideration, and third-party rights before signature.""",
        'clauses': ('intellectual-property', 'source-code-ownership', 'third-party-open-source', 'confidentiality', 'governing-law'),
        'parties': ('issuer', 'counterparty'),
    },
    'freelancer-work-order': {
        'name': 'Freelancer Work Order',
        'body': """## 1. Parties and project
Provider: {{ company_display_name }}. Freelancer: {{ freelancer_name }}. Project: {{ project_name }}.

## 2. Scope and deliverables
{{ scope_of_work }}
{{ deliverables }}

## 3. Dates, milestones and reporting
Start: {{ start_date }}. Completion: {{ completion_date }}.
Milestones: {{ milestones }}
Reporting requirements: {{ reporting_requirements }}

## 4. Commercial terms and acceptance
{{ commercial_terms }}
Acceptance criteria: {{ acceptance_criteria }}
Authorised signatory: {{ work_order_signatory }}.
This work order is subject to the applicable master agreement and must be approved by authorised representatives before work begins.""",
        'clauses': ('payment-terms', 'project-acceptance', 'change-management', 'confidentiality', 'intellectual-property'),
        'parties': ('issuer', 'counterparty'),
    },
    'offer-letter': {
        'name': 'Offer Letter',
        'body': """## 1. Offer
Dear {{ candidate_name }},
Candidate address: {{ candidate_address }}. Email: {{ candidate_email }}.
Quantbud LLP, operating under the brand name Synfowebs Technology, is pleased to offer the position of {{ designation }}.

## 2. Proposed employment details
Department: {{ department }}. Employment type: {{ employment_type }}.
Reporting manager: {{ reporting_manager }}. Work location: {{ work_location }}.
Joining date: {{ joining_date }}.

## 3. Compensation and work terms
Annual CTC: {{ annual_ctc }}. Monthly salary, if applicable: {{ monthly_salary }}.
Salary structure reference: {{ salary_structure_reference }}.
Probation: {{ probation_period }}. Working hours: {{ working_hours }}. Notice period: {{ notice_period }}.
Leave and benefits reference: {{ leave_benefits_reference }}.

## 4. Conditions and acceptance
{{ conditions_of_employment }}
Required documents: {{ required_documents }}
Please accept by {{ offer_valid_until }} by signing below. This offer is subject to completion and verification of stated conditions and an approved appointment process.

## 5. Candidate acceptance
I accept the offer and acknowledge the terms provided to me. Candidate signature: ____________________. Date: ____________________.""",
        'clauses': ('employee-responsibilities', 'confidentiality', 'intellectual-property', 'notice-period'),
        'parties': ('issuer', 'counterparty'),
    },
    'conditional-offer-letter': {
        'name': 'Conditional Offer Letter',
        'body': """## 1. Conditional offer
Dear {{ candidate_name }}, {{ candidate_address }}. Email: {{ candidate_email }}.
We propose to offer the position of {{ designation }} in {{ department }} as {{ employment_type }}, subject to the conditions below.

## 2. Proposed terms and conditions
Proposed joining date: {{ joining_date }}. Annual CTC, if finalised: {{ annual_ctc }}.
Conditions to be satisfied: {{ conditions_of_employment }}
Completion deadline: {{ condition_deadline }}. Acceptance deadline: {{ offer_valid_until }}.

## 3. Important notice
This conditional offer does not confirm that conditions have been met or create a final appointment. The employer must verify completion and issue the appropriate appointment documentation.

## 4. Candidate acknowledgement
Candidate signature: ____________________. Date: ____________________.""",
        'clauses': ('employee-responsibilities', 'confidentiality'),
        'parties': ('issuer', 'counterparty'),
    },
    'appointment-letter': {
        'name': 'Appointment Letter',
        'body': """## 1. Appointment
This letter confirms the appointment of {{ employee_name }} (Employee ID {{ employee_id }}) as {{ designation }} with {{ company_display_name }}.

## 2. Employment particulars
Department: {{ department }}. Reporting manager: {{ reporting_manager }}.
Employment type: {{ employment_type }}. Work location: {{ work_location }}.
Appointment date: {{ appointment_date }}. Date of joining: {{ date_of_joining }}.

## 3. Compensation and policies
{{ compensation }}
Probation: {{ probation_period }}. Working hours: {{ working_hours }}.
Leave policy: {{ leave_policy_reference }}. Notice period: {{ notice_period }}.
Applicable policies: {{ applicable_policies }}
Additional conditions: {{ employment_conditions }}

## 4. Confidentiality, property and acceptance
The employee must protect company information, use company property responsibly, and comply with communicated policies. The employee acknowledges receipt of this letter by signing below.""",
        'clauses': ('employee-responsibilities', 'confidentiality', 'intellectual-property', 'return-company-property', 'notice-period'),
        'parties': ('issuer', 'counterparty'),
    },
    'joining-letter': {
        'name': 'Joining Letter and Acknowledgement',
        'body': """## 1. Joining confirmation
Employee: {{ employee_name }} ({{ employee_id }}). Designation: {{ designation }}.
Department: {{ department }}. Reporting manager: {{ reporting_manager }}.
Work location: {{ work_location }}. Joining date: {{ date_of_joining }}.

## 2. Documents and policy acknowledgement
Document submission checklist: {{ document_submission_checklist }}
Policy acknowledgement: {{ policy_acknowledgement }}

## 3. Acknowledgement
The employee confirms the joining information above is accurate to their knowledge and acknowledges the policies specifically provided to them. HR acknowledgement: {{ hr_acknowledgement }}.""",
        'clauses': ('employee-responsibilities', 'confidentiality'),
        'parties': ('issuer', 'counterparty'),
    },
    'employee-nda': {
        'name': 'Employment Confidentiality Agreement',
        'body': """## 1. Parties and employment
Employer: {{ company_display_name }}. Employee: {{ employee_name }} ({{ employee_id }}), {{ designation }}.

## 2. Confidential information and obligations
{{ confidential_information }}
The employee will access and use information only for authorised work, protect credentials, follow approved security controls, and report suspected loss or unauthorised access.

## 3. Systems, data and intellectual property
Systems and access: {{ systems_access }}
Security obligations: {{ security_obligations }}
Intellectual property and source code: {{ ip_ownership_terms }}

## 4. Return and continuing duties
{{ property_return_terms }}
Post-employment confidentiality period: {{ post_employment_period }}.
Any continuing restriction must be reviewed and clearly limited to applicable lawful obligations.""",
        'clauses': ('confidentiality', 'data-protection', 'information-security', 'intellectual-property', 'source-code-ownership', 'return-company-property'),
        'parties': ('issuer', 'counterparty'),
    },
    'probation-letter': {
        'name': 'Probation Letter',
        'body': """## 1. Probation particulars
Employee: {{ employee_name }} ({{ employee_id }}), {{ designation }}.
Probation begins {{ probation_start_date }} for {{ probation_duration }}.
Review date: {{ review_date }}.

## 2. Expectations and review
{{ performance_expectations }}
Confirmation criteria: {{ confirmation_criteria }}
Extension provisions: {{ extension_provisions }}

## 3. Status
This letter records the stated probation terms only. Any extension or confirmation requires a separate documented decision by an authorised representative.""",
        'clauses': ('employee-responsibilities', 'notice-period'),
        'parties': ('issuer', 'counterparty'),
    },
    'confirmation-letter': {
        'name': 'Employment Confirmation Letter',
        'body': """## 1. Confirmation
Employee: {{ employee_name }} ({{ employee_id }}), {{ designation }}.
Department: {{ department }}. Date of joining: {{ date_of_joining }}.
Probation completion date: {{ probation_completion_date }}.
Employment confirmation is effective from {{ confirmation_effective_date }}.

## 2. Terms
{{ applicable_terms }}
All other applicable written employment terms and policies remain subject to their own terms and any lawful amendment.""",
        'clauses': ('employee-responsibilities', 'confidentiality'),
        'parties': ('issuer',),
    },
    'salary-revision-letter': {
        'name': 'Salary Revision Letter',
        'body': """## 1. Employee and role
Employee: {{ employee_name }} ({{ employee_id }}).
Existing designation: {{ existing_designation }}. Revised designation: {{ revised_designation }}.

## 2. Compensation revision
Existing salary: {{ existing_salary }}. Revised salary: {{ revised_salary }}.
Effective date: {{ salary_effective_date }}. Salary breakdown reference: {{ salary_breakdown_reference }}.

## 3. Confidentiality
This communication contains confidential compensation information and should be handled accordingly. All other employment terms remain unchanged unless expressly amended in writing.""",
        'clauses': ('confidentiality', 'employee-responsibilities'),
        'parties': ('issuer',),
    },
    'promotion-letter': {
        'name': 'Promotion Letter',
        'body': """## 1. Promotion
Employee: {{ employee_name }} ({{ employee_id }}). Department: {{ department }}.
Existing designation: {{ existing_designation }}. New designation: {{ new_designation }}.
Effective date: {{ promotion_effective_date }}.

## 2. Responsibilities and compensation
{{ new_responsibilities }}
Revised compensation, if applicable: {{ revised_compensation }}.
Reporting manager: {{ reporting_manager }}.

## 3. Acknowledgement
All other employment terms remain unchanged unless separately amended in writing.""",
        'clauses': ('employee-responsibilities', 'confidentiality'),
        'parties': ('issuer', 'counterparty'),
    },
    'warning-letter': {
        'name': 'Workplace Concern Letter',
        'body': """## 1. Employee and reference
Employee: {{ employee_name }} ({{ employee_id }}), {{ designation }}.
Reference date: {{ reference_date }}. Policy / provision: {{ policy_reference }}.

## 2. Concern requiring response
{{ issue_description }}
This communication records a concern for response and is not a final finding of misconduct.

## 3. Response and next steps
Please provide the following explanation by {{ response_deadline }}: {{ explanation_requested }}
Potential next steps: {{ next_steps }}
The response will be considered before any decision is made. Follow applicable policy and fair procedure.""",
        'clauses': ('employee-responsibilities',),
        'parties': ('issuer', 'counterparty'),
    },
    'show-cause-notice': {
        'name': 'Show Cause Notice',
        'body': """## 1. Employee and reference
Employee: {{ employee_name }} ({{ employee_id }}), {{ designation }}.
Reference date: {{ reference_date }}. Policy / provision: {{ policy_reference }}.

## 2. Matter requiring explanation
{{ issue_description }}
No conclusion has been reached. You are invited to respond to these matters: {{ explanation_requested }}

## 3. Response deadline and process
Please respond by {{ response_deadline }}. Next procedural steps: {{ next_steps }}
Your response will be considered before any decision. This draft does not make a finding and must be reviewed against the applicable policy and fair process.""",
        'clauses': ('employee-responsibilities',),
        'parties': ('issuer', 'counterparty'),
    },
    'experience-certificate': {
        'name': 'Experience Certificate',
        'body': """## Experience certificate
This is to certify that {{ employee_name }} (Employee ID {{ employee_id }}) was employed by {{ company_display_name }} as {{ designation }} in the {{ department }} department.

Employment commenced on {{ date_of_joining }} and ended on {{ last_working_date }}.
{{ service_description }}

This certificate is based on the employment information reviewed as of the issue date. It is issued at the employee's request without additional representation.""",
        'clauses': (),
        'parties': ('issuer',),
    },
    'relieving-letter': {
        'name': 'Relieving Letter',
        'body': """## 1. Employment and resignation
Employee: {{ employee_name }} ({{ employee_id }}), {{ designation }}.
Date of joining: {{ date_of_joining }}. Resignation date: {{ resignation_date }}.
Last working date: {{ last_working_date }}.

## 2. Handover and clearance
Handover status: {{ handover_status }}
Clearance status: {{ clearance_status }}
Company property return status: {{ property_return_status }}
Pending obligations: {{ pending_obligations }}

## 3. Relieving confirmation
Relieving and clearance must be confirmed by authorised records before issue. This draft does not itself certify that outstanding obligations or clearances are complete.""",
        'clauses': ('return-company-property', 'confidentiality'),
        'parties': ('issuer',),
    },
    'resignation-acceptance-letter': {
        'name': 'Resignation Acceptance Letter',
        'body': """## 1. Resignation
Employee: {{ employee_name }} ({{ employee_id }}), {{ designation }}.
Resignation submitted: {{ resignation_submission_date }}.
Accepted resignation date: {{ accepted_resignation_date }}.
Last working date: {{ last_working_date }}.

## 2. Handover and clearance
{{ handover_expectations }}
Clearance requirements: {{ clearance_requirements }}
Any relieving confirmation will be issued separately after authorised verification.""",
        'clauses': ('return-company-property', 'confidentiality'),
        'parties': ('issuer',),
    },
    'salary-certificate': {
        'name': 'Salary Certificate',
        'body': """## Salary certificate
This is to certify that {{ employee_name }} (Employee ID {{ employee_id }}) is employed as {{ designation }} with {{ company_display_name }}.

Employment period: {{ employment_period }}.
Salary period covered: {{ salary_period }}.
Salary details: {{ salary_details }}
Purpose, if supplied: {{ certificate_purpose }}

This confidential certificate is based on payroll records verified for the stated period. Issue only to the employee or an authorised recipient after identity and disclosure checks.""",
        'clauses': ('confidentiality',),
        'parties': ('issuer',),
    },
    'no-dues-certificate': {
        'name': 'No Dues / Clearance Certificate',
        'body': """## 1. Employee and clearance
Employee: {{ employee_name }} ({{ employee_id }}), {{ designation }}.
Department: {{ department }}. Last working date, if applicable: {{ last_working_date }}.

## 2. Verified clearance status
Department clearance: {{ department_clearance }}
Asset return: {{ asset_clearance }}
Finance: {{ finance_clearance }}
HR: {{ hr_clearance }}
IT: {{ it_clearance }}

## 3. Final approval
Final approval reference: {{ final_approval }}.
This certificate must not be issued as clear until every relevant department has verified and authorised its status.""",
        'clauses': ('return-company-property',),
        'parties': ('issuer',),
    },
    'asset-handover-receipt': {
        'name': 'Asset Handover Receipt',
        'body': """## Asset handover
Employee: {{ employee_name }} ({{ employee_id }}).
Asset: {{ asset_name }}. Asset ID: {{ asset_code }}. Category: {{ asset_category }}. Serial number: {{ serial_number }}.
Make and model: {{ brand_and_model }}.
Accessories issued: {{ accessories_issued }}
Condition at handover: {{ issue_condition }}
Issue date: {{ issue_date }}. Expected return date: {{ expected_return_date }}.
Issuing authority: {{ issuing_authority }}.

## Employee acknowledgement
{{ acknowledgement_text }}
The employee acknowledges receipt of the item(s) listed above, subject to the stated condition and any applicable company policy. This is an internal acknowledgement, not a certified electronic signature.""",
        'clauses': ('return-company-property',),
        'parties': ('issuer', 'counterparty'),
    },
    'asset-return-receipt': {
        'name': 'Asset Return Receipt',
        'body': """## Asset return
Employee: {{ employee_name }} ({{ employee_id }}).
Asset: {{ asset_name }}. Asset ID: {{ asset_code }}. Serial number: {{ serial_number }}.
Return date: {{ return_date }}. Condition at return: {{ return_condition }}.
Accessories returned: {{ accessories_returned }}
Damage or missing items: {{ damage_missing_items }}
Remarks: {{ return_remarks }}
Received by: {{ received_by }}.

## Acknowledgement
{{ employee_acknowledgement }}
Record only items inspected and received. This receipt does not decide responsibility for damage or loss.""",
        'clauses': ('return-company-property',),
        'parties': ('issuer', 'counterparty'),
    },
    'joining-checklist': {
        'name': 'Joining Checklist',
        'body': """## Employee details
{{ employee_name }} ({{ employee_id }}), {{ designation }}. Department: {{ department }}.
Joining date: {{ date_of_joining }}.

## Document and setup checklist
Identity documents: {{ identity_documents_status }}
Address documents: {{ address_documents_status }}
PAN document status: {{ pan_documents_status }}
Bank detail verification: {{ bank_details_status }}
Education documents: {{ education_documents_status }}
Previous employment documents: {{ previous_employment_status }}
Employment agreements: {{ agreements_status }}
Policy acknowledgements: {{ policy_acknowledgements }}
Asset issue: {{ asset_issue_status }}
IT access: {{ it_access_status }}
HR completion: {{ hr_completion_status }}

Do not include full identity or bank account numbers in this checklist.""",
        'clauses': ('employee-responsibilities', 'information-security'),
        'parties': ('issuer', 'counterparty'),
    },
    'employee-clearance-form': {
        'name': 'Exit Clearance Certificate',
        'body': """## Employee and exit details
Employee: {{ employee_name }} ({{ employee_id }}), {{ designation }}.
Department: {{ department }}. Last working date: {{ last_working_date }}.

## Department clearance
HR: {{ hr_clearance }}
Finance: {{ finance_clearance }}
IT: {{ it_clearance }}
Assets: {{ asset_clearance }}
Department: {{ department_clearance }}
Knowledge transfer: {{ knowledge_transfer_status }}
Pending obligations: {{ pending_obligations }}

## Final approval
Final approval: {{ final_approval }}.
Do not certify exit clearance until each relevant status has been confirmed by an authorised person.""",
        'clauses': ('return-company-property', 'confidentiality'),
        'parties': ('issuer',),
    },
    'knowledge-transfer-certificate': {
        'name': 'Knowledge Transfer Certificate',
        'body': """## Employee and project
Employee: {{ employee_name }} ({{ employee_id }}), {{ designation }}.
Project details: {{ project_details }}

## Handover record
Topics: {{ handover_topics }}
Documentation: {{ documentation_status }}
Access transfer: {{ access_transfer_status }}
Pending tasks: {{ pending_tasks }}
Receiving person: {{ receiving_person }}.
Completion date: {{ completion_date }}. Approval reference: {{ approval_reference }}.

This certificate records only the handover items confirmed above and does not imply that omitted tasks are complete.""",
        'clauses': ('return-company-property', 'information-security'),
        'parties': ('issuer', 'counterparty'),
    },
}


CLAUSE_DOCUMENT_TYPES = {
    'confidentiality': tuple(FIELD_SETS),
    'intellectual-property': ('master-client-agreement-mca', 'statement-of-work-sow', 'freelancer-agreement', 'intellectual-property-assignment', 'offer-letter', 'appointment-letter', 'employee-nda'),
    'source-code-ownership': ('master-client-agreement-mca', 'freelancer-agreement', 'intellectual-property-assignment', 'employee-nda'),
    'payment-terms': ('master-client-agreement-mca', 'statement-of-work-sow', 'freelancer-agreement', 'freelancer-work-order'),
    'late-payment': ('master-client-agreement-mca', 'freelancer-agreement', 'freelancer-work-order'),
    'termination': ('master-client-agreement-mca', 'freelancer-agreement'),
    'notice-period': ('offer-letter', 'appointment-letter', 'probation-letter'),
    'non-solicitation': ('master-client-agreement-mca', 'freelancer-agreement', 'employee-nda'),
    'conflict-of-interest': ('freelancer-agreement', 'appointment-letter'),
    'data-protection': ('non-disclosure-agreement-nda', 'mutual-nda', 'master-client-agreement-mca', 'freelancer-agreement', 'freelancer-nda', 'employee-nda'),
    'information-security': ('non-disclosure-agreement-nda', 'mutual-nda', 'master-client-agreement-mca', 'service-level-agreement-sla', 'freelancer-agreement', 'freelancer-nda', 'employee-nda', 'joining-checklist', 'knowledge-transfer-certificate'),
    'indemnification': ('master-client-agreement-mca', 'freelancer-agreement'),
    'limitation-of-liability': ('master-client-agreement-mca', 'service-level-agreement-sla'),
    'force-majeure': ('master-client-agreement-mca',),
    'dispute-resolution': ('non-disclosure-agreement-nda', 'mutual-nda', 'master-client-agreement-mca', 'freelancer-agreement', 'freelancer-nda'),
    'governing-law': ('non-disclosure-agreement-nda', 'mutual-nda', 'master-client-agreement-mca', 'freelancer-agreement', 'freelancer-nda', 'intellectual-property-assignment'),
    'jurisdiction': ('non-disclosure-agreement-nda', 'mutual-nda', 'master-client-agreement-mca'),
    'return-company-property': ('freelancer-agreement', 'freelancer-nda', 'employee-nda', 'relieving-letter', 'resignation-acceptance-letter', 'no-dues-certificate', 'asset-handover-receipt', 'asset-return-receipt', 'employee-clearance-form', 'knowledge-transfer-certificate', 'appointment-letter'),
    'employee-responsibilities': ('offer-letter', 'conditional-offer-letter', 'appointment-letter', 'joining-letter', 'employee-nda', 'probation-letter', 'confirmation-letter', 'promotion-letter', 'warning-letter', 'show-cause-notice', 'joining-checklist'),
    'independent-contractor': ('freelancer-agreement', 'freelancer-work-order'),
    'project-acceptance': ('master-client-agreement-mca', 'statement-of-work-sow', 'service-level-agreement-sla', 'freelancer-agreement', 'freelancer-work-order'),
    'change-management': ('master-client-agreement-mca', 'statement-of-work-sow', 'service-level-agreement-sla', 'freelancer-agreement', 'freelancer-work-order'),
    'warranty-disclaimer': ('master-client-agreement-mca', 'service-level-agreement-sla', 'freelancer-agreement'),
    'third-party-open-source': ('master-client-agreement-mca', 'freelancer-agreement', 'intellectual-property-assignment', 'employee-nda'),
}
