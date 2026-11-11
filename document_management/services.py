"""Index sync (read-only), integrity check and company-document upload."""
import os
import uuid

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from employee_management.storage import private_storage
from employee_management.validators import sniff_upload

from .models import Document, full_path, safe_rel_path, sha256_of

MIME = {'pdf': 'application/pdf', 'png': 'image/png', 'jpg': 'image/jpeg'}
MAX_BYTES = 10 * 1024 * 1024


def _sources():
    from employee_management.models import EmployeeDocument
    from expense_management.models import CompanyExpenseAttachment, EmployeeExpenseAttachment
    for d in EmployeeDocument.objects.select_related('employee'):
        yield d, {'category': 'employee', 'source_type': 'employee_document', 'source_id': str(d.pk),
                  'title': f'{d.employee.employee_id} {d.employee.full_name} - {d.document_name or d.get_document_type_display()}',
                  'linked': d.employee, 'sensitivity': Document.CONFIDENTIAL, 'tags': f'employee,{d.document_type}',
                  'uploaded_at': d.upload_date, 'file': d.file, 'mime': d.content_type,
                  'original_filename': d.original_filename}
    for a in CompanyExpenseAttachment.objects.select_related('expense'):
        yield a, {'category': 'expense', 'source_type': 'company_expense_attachment', 'source_id': str(a.pk),
                  'title': f'{a.expense.expense_id} - {a.get_doc_type_display() if hasattr(a, "get_doc_type_display") else a.doc_type}',
                  'linked': a.expense, 'sensitivity': Document.NORMAL, 'tags': f'expense,{a.doc_type}',
                  'uploaded_at': a.uploaded_at, 'file': a.file, 'mime': a.content_type,
                  'original_filename': a.original_filename}
    for a in EmployeeExpenseAttachment.objects.select_related('claim'):
        yield a, {'category': 'claim', 'source_type': 'employee_expense_attachment', 'source_id': str(a.pk),
                  'title': f'{a.claim} - {a.get_doc_type_display() if hasattr(a, "get_doc_type_display") else a.doc_type}',
                  'linked': a.claim, 'sensitivity': Document.CONFIDENTIAL, 'tags': f'claim,{a.doc_type}',
                  'uploaded_at': a.uploaded_at, 'file': a.file, 'mime': a.content_type,
                  'original_filename': a.original_filename}


def _fingerprint(rel):
    try:
        fp = full_path(rel)
        if not os.path.isfile(fp):
            return 0, '', True
        return os.path.getsize(fp), sha256_of(rel), False
    except (ValidationError, OSError):
        return 0, '', True


@transaction.atomic
def sync_index(hash_files=True):
    """Index existing private employee documents and expense attachments. Reads only; never touches the files.
    Idempotent: re-running updates the same rows (unique source_type/source_id)."""
    stats = {'created': 0, 'updated': 0, 'missing': 0, 'removed_sources': 0}
    seen = set()
    now = timezone.now()
    for obj, d in _sources():
        f = d.pop('file')
        if not f or not f.name:
            continue
        linked = d.pop('linked')
        rel = safe_rel_path(f.name)
        size, sha, missing = _fingerprint(rel) if hash_files else (0, '', False)
        defaults = {'category': d['category'], 'title': d['title'][:200],
                    'content_type': ContentType.objects.get_for_model(linked), 'object_id': str(linked.pk),
                    'file_path': rel, 'original_filename': (d['original_filename'] or '')[:255],
                    'mime_type': (d['mime'] or '')[:100], 'uploaded_at': d['uploaded_at'], 'size': size,
                    'sha256': sha, 'missing': missing, 'last_checked': now,
                    'check_note': 'File missing at sync' if missing else ''}
        doc = Document.objects.filter(source_type=d['source_type'], source_id=d['source_id']).first()
        if doc is None:
            Document.objects.create(source_type=d['source_type'], source_id=d['source_id'],
                                    sensitivity=d['sensitivity'], tags=d['tags'], **defaults)
            stats['created'] += 1
        else:
            for k, v in defaults.items():  # user-edited sensitivity / tags are kept
                setattr(doc, k, v)
            doc.save()
            stats['updated'] += 1
        stats['missing'] += missing
        seen.add((d['source_type'], d['source_id']))
    for doc in Document.objects.exclude(source_type__in=Document.SELF_MANAGED_SOURCE_TYPES):
        if (doc.source_type, doc.source_id) not in seen and not doc.missing:
            doc.missing, doc.check_note, doc.last_checked = True, 'Source record no longer exists', now
            doc.save()
            stats['removed_sources'] += 1
    return stats


def check_documents(qs=None):
    """Missing-file and sha256 check. Updates only the index flags."""
    now, out = timezone.now(), {'ok': 0, 'missing': 0, 'changed': 0, 'rows': []}
    for doc in (qs if qs is not None else Document.objects.all()):
        size, sha, missing = _fingerprint(doc.file_path)
        if missing:
            state, note = 'missing', 'File not found in private storage'
        elif doc.sha256 and sha != doc.sha256:
            state, note = 'changed', f'sha256 changed (index {doc.sha256[:12]}..., file {sha[:12]}...)'
        else:
            state, note = 'ok', ''
            if not doc.sha256:
                doc.sha256, doc.size = sha, size
        doc.missing, doc.check_note, doc.last_checked = missing, note, now
        doc.save()
        out[state] += 1
        out['rows'].append((doc, state, note))
    return out


@transaction.atomic
def upload_company_doc(f, category, title, sensitivity='normal', tags=''):
    if category not in Document.COMPANY_CATEGORIES:
        raise ValidationError('Choose a company document category.')
    ext = sniff_upload(f, MAX_BYTES)
    rel = f'company_docs/{uuid.uuid4().hex}.{ext}'
    f.seek(0)
    saved = private_storage.save(rel, f)  # random name; private root only
    rel = safe_rel_path(saved)
    doc = Document.objects.create(category=category, title=title[:200], source_type='company_doc',
                                  source_id=uuid.uuid4().hex, file_path=rel,
                                  original_filename=os.path.basename(f.name or '')[:255], mime_type=MIME[ext],
                                  sensitivity=sensitivity, tags=tags, uploaded_at=timezone.now(),
                                  size=os.path.getsize(full_path(rel)), sha256=sha256_of(rel),
                                  last_checked=timezone.now())
    return doc
