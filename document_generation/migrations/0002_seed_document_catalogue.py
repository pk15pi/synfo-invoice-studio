from django.db import migrations
from django.utils.text import slugify


CATEGORIES = [
    ('client', 'Client'),
    ('freelancer', 'Freelancer'),
    ('consultant', 'Consultant'),
    ('candidate', 'Candidate'),
    ('employee', 'Employee'),
    ('vendor', 'Vendor'),
    ('internal', 'Internal'),
]

TYPE_GROUPS = {
    'client': [
        'Non-Disclosure Agreement (NDA)', 'Mutual NDA', 'Master Client Agreement (MCA)',
        'Master Services Agreement (MSA)', 'Service Level Agreement (SLA)', 'Statement of Work (SOW)',
        'Work Order', 'Project Proposal', 'Quotation', 'Purchase Order Acknowledgement',
        'Project Agreement', 'Change Request', 'Project Extension Agreement',
        'Project Completion Certificate', 'Client Acceptance Certificate', 'Project Handover Document',
        'Support and Maintenance Agreement', 'Annual Maintenance Contract',
        'Software Development Agreement', 'Software Licensing Agreement',
        'Intellectual Property Assignment Agreement', 'Data Processing Agreement',
        'Website Development Agreement', 'Resource Deployment Agreement', 'Contract Amendment',
        'Termination Agreement', 'Outstanding Payment Confirmation', 'No-Dues Certificate',
    ],
    'freelancer': [
        'Freelancer NDA', 'Freelancer Agreement', 'Independent Contractor Agreement',
        'Consultancy Agreement', 'Project-Based Agreement', 'Freelancer Work Order',
        'Freelancer SOW', 'Intellectual Property Assignment', 'Source Code Ownership Declaration',
        'Non-Solicitation Undertaking', 'Conflict of Interest Declaration',
        'Data Confidentiality Undertaking', 'Information Security Acknowledgement',
        'Freelancer Onboarding Form', 'Freelancer Information Sheet', 'Timesheet',
        'Work Completion Certificate', 'Deliverable Acceptance Form', 'Invoice Submission Guidelines',
        'Payment Confirmation', 'Engagement Extension Letter', 'Engagement Termination Letter',
        'Freelancer Experience/Project Certificate', 'Freelancer No-Dues Certificate',
    ],
    'consultant': [
        'Consultancy Agreement', 'Independent Contractor Agreement', 'Project-Based Agreement',
        'Data Confidentiality Undertaking', 'Information Security Acknowledgement',
    ],
    'candidate': [
        'Interview Invitation', 'Interview Confirmation', 'Interview Rescheduling Letter',
        'Interview Rejection Letter', 'Selection Letter', 'Conditional Offer Letter', 'Offer Letter',
        'Offer Revision Letter', 'Offer Acceptance Form', 'Background Verification Consent',
        'Reference Check Form', 'Candidate Information Form', 'Document Submission Checklist',
        'Pre-Joining Instructions', 'Internship Offer Letter', 'Internship Agreement',
        'Internship Completion Certificate',
    ],
    'employee': [
        'Appointment Letter', 'Employment Agreement', 'Joining Letter', 'Employee Declaration',
        'Employee Information Form', 'Employee NDA', 'Confidentiality Undertaking',
        'Intellectual Property Assignment Agreement', 'Non-Solicitation Undertaking',
        'Code of Conduct Acknowledgement', 'IT and Asset Usage Policy', 'Remote Work Agreement',
        'Probation Letter', 'Probation Extension Letter', 'Confirmation Letter', 'Promotion Letter',
        'Increment Letter', 'Salary Revision Letter', 'Designation Change Letter',
        'Department Transfer Letter', 'Work Location Change Letter', 'Warning Letter',
        'Show Cause Notice', 'Performance Improvement Plan', 'Appreciation Letter',
        'Employee Undertaking', 'Employment Verification Letter', 'Experience Certificate',
        'Relieving Letter', 'Resignation Acceptance Letter', 'Termination Letter',
        'Suspension Letter', 'No-Dues Certificate', 'Full and Final Settlement Statement',
        'Salary Certificate', 'NOC', 'Service Certificate', 'Employment Extension Letter',
        'Joining Checklist', 'Employee Document Checklist', 'Identity Information Declaration',
        'Bank Details Declaration', 'Emergency Contact Form', 'Nomination Form where applicable',
        'Asset Handover Receipt', 'Asset Return Receipt', 'Laptop/Device Usage Undertaking',
        'Employee Policy Handbook Acknowledgement', 'Exit Interview Form', 'Employee Clearance Form',
        'Handover Checklist', 'Knowledge Transfer Certificate', 'Final Settlement Approval',
        'Employee Exit Checklist',
    ],
    'internal': [
        'Authorisation Letter', 'Partner Resolution Draft', 'Power of Attorney Draft',
        'Vendor Agreement', 'Purchase Agreement', 'Service Provider Agreement',
        'Data Confidentiality Policy', 'IT Security Policy', 'Work From Home Policy',
        'Leave Policy', 'Attendance Policy', 'Expense Reimbursement Policy', 'Travel Policy',
        'Employee Handbook', 'Document Retention Policy', 'Conflict of Interest Policy',
        'Asset Management Policy', 'Delegation of Authority',
    ],
}

PREFIX_OVERRIDES = {
    'non-disclosure-agreement-nda': 'QB-NDA',
    'mutual-nda': 'QB-MNDA',
    'master-client-agreement-mca': 'QB-MCA',
    'master-services-agreement-msa': 'QB-MSA',
    'service-level-agreement-sla': 'QB-SLA',
    'statement-of-work-sow': 'QB-SOW',
    'offer-letter': 'QB-OFFER',
    'appointment-letter': 'QB-APT',
    'experience-certificate': 'QB-EXP',
    'relieving-letter': 'QB-REL',
    'freelancer-agreement': 'QB-FREELANCE',
}


def seed_catalogue(apps, schema_editor):
    Category = apps.get_model('document_generation', 'DocumentCategory')
    DocumentType = apps.get_model('document_generation', 'DocumentType')
    db = schema_editor.connection.alias
    category_rows = {}
    for code, name in CATEGORIES:
        category, _ = Category.objects.using(db).get_or_create(code=code, defaults={'name': name})
        category_rows[code] = category

    for category_code, names in TYPE_GROUPS.items():
        target_category = category_rows[category_code]
        for name in names:
            code = slugify(name)[:60]
            prefix = PREFIX_OVERRIDES.get(code, f'QB-{code.upper()[:34]}')
            document_type, _ = DocumentType.objects.using(db).get_or_create(
                code=code,
                defaults={
                    'name': name,
                    'number_prefix': prefix,
                    'number_padding': 3,
                    'year_convention': 'calendar',
                    'reset_frequency': 'calendar_year',
                    'approval_required': True,
                },
            )
            document_type.categories.add(target_category)

    vendor = category_rows['vendor']
    for name in ('Vendor Agreement', 'Purchase Agreement', 'Service Provider Agreement'):
        document_type = DocumentType.objects.using(db).get(code=slugify(name)[:60])
        document_type.categories.add(vendor)


class Migration(migrations.Migration):
    dependencies = [
        ('document_generation', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_catalogue, migrations.RunPython.noop),
    ]
