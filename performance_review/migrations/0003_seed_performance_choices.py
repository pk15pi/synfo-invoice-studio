from django.db import migrations


STAGES = [
    'Resume Submitted', 'Resume Screening', 'Resume Shortlisted', 'Resume Rejected',
    'Client Submitted', 'Client Screening', 'Client Interview', 'Technical Interview',
    'Managerial Interview', 'HR Interview', 'Selected', 'Offer Released', 'Offer Accepted',
    'Joined', 'On Hold', 'Withdrawn',
]
REASONS = ['Skills mismatch', 'Experience mismatch', 'Compensation mismatch',
           'Client requirement changed', 'Candidate withdrew', 'Position put on hold', 'Other']


def add_defaults(apps, schema_editor):
    Stage = apps.get_model('performance_review', 'PerformanceStage')
    for order, name in enumerate(STAGES, 1):
        Stage.objects.get_or_create(kind='stage', name=name, defaults={'sort_order': order})
    for order, name in enumerate(REASONS, 1):
        Stage.objects.get_or_create(kind='rejection_reason', name=name, defaults={'sort_order': order})


class Migration(migrations.Migration):
    dependencies = [('performance_review', '0002_alter_candidateapplication_rejection_reason_and_more')]
    operations = [migrations.RunPython(add_defaults, migrations.RunPython.noop)]
