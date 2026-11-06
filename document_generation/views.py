import hashlib

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Count, Exists, F, OuterRef, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from document_management.models import full_path
from document_management.services import private_storage
from employee_management.security import local_only
from invoice_app.table_sorting import apply_queryset_sort

from .forms import CompanyDocumentProfileForm
from .generation import (
    available_output_formats,
    generate_document as generate_document_file,
    load_preview,
    prepare_preview,
    sign_preview,
)
from .generation_forms import UniversalDocumentForm
from .field_resolver import resolve_source_value
from .models import (
    CompanyDocumentProfile,
    CompanyDocumentSignatory,
    DocumentClause,
    DocumentCategory,
    DocumentTemplate,
    DocumentTemplateVersion,
    DocumentType,
    GeneratedDocument,
)
from .template_engine import html_document_content, resolve_template


@local_only
def dashboard(request):
    template_versions = DocumentTemplateVersion.objects.all()
    return render(request, 'document_generation/dashboard.html', {
        'category_count': DocumentCategory.objects.filter(is_active=True).count(),
        'type_count': DocumentType.objects.filter(is_active=True).count(),
        'document_count': GeneratedDocument.objects.count(),
        'draft_count': GeneratedDocument.objects.filter(status='draft').count(),
        'pending_count': GeneratedDocument.objects.filter(status='pending_approval').count(),
        'template_count': DocumentTemplate.objects.count(),
        'approved_template_count': template_versions.filter(
            status=DocumentTemplateVersion.APPROVED,
            template__is_active=True,
        ).count(),
        'pending_template_count': template_versions.filter(
            status=DocumentTemplateVersion.REVIEW,
        ).count(),
        'inactive_template_count': DocumentTemplate.objects.filter(is_active=False).count(),
        'template_version_count': template_versions.count(),
        'clause_count': DocumentClause.objects.count(),
        'pending_clause_count': DocumentClause.objects.filter(
            approval_status=DocumentClause.REVIEW,
        ).count(),
        'recent_documents': GeneratedDocument.objects.select_related('document_type')[:8],
        'categories': DocumentCategory.objects.filter(is_active=True).annotate(
            type_count=Count('document_types', filter=Q(document_types__is_active=True), distinct=True),
        ),
    })


@local_only
def catalogue(request):
    query = request.GET.get('q', '').strip()[:100]
    category = request.GET.get('category', '').strip()[:40]
    document_types = DocumentType.objects.filter(is_active=True).prefetch_related('categories')
    if query:
        document_types = document_types.filter(
            Q(name__icontains=query) | Q(code__icontains=query) | Q(description__icontains=query)
        )
    if category:
        document_types = document_types.filter(categories__code=category)
    document_types = document_types.distinct()
    document_types, _, _ = apply_queryset_sort(document_types, request, {
        'type': 'name', 'code': 'code', 'numbering': 'number_prefix', 'approval': 'approval_required',
    })
    page = Paginator(document_types, 30).get_page(request.GET.get('page'))
    page_query = request.GET.copy()
    page_query.pop('page', None)
    return render(request, 'document_generation/catalogue.html', {
        'page': page,
        'categories': DocumentCategory.objects.filter(is_active=True),
        'query': query,
        'selected_category': category,
        'page_query': page_query.urlencode(),
    })


@local_only
def settings(request):
    profile = CompanyDocumentProfile.current()
    form = CompanyDocumentProfileForm(request.POST or None, request.FILES or None, instance=profile)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Company document profile saved.')
        return redirect('document_generation:settings')
    return render(request, 'document_generation/settings.html', {
        'form': form,
        'profile': profile,
    })


def _approved_type(pk):
    if not pk:
        return None
    try:
        return DocumentType.objects.filter(
            pk=pk,
            is_active=True,
            templates__is_active=True,
            templates__active_version__status=DocumentTemplateVersion.APPROVED,
        ).distinct().first()
    except (TypeError, ValueError):
        return None


def _active_type(pk):
    if not pk:
        return None
    try:
        return DocumentType.objects.filter(pk=pk, is_active=True).distinct().first()
    except (TypeError, ValueError):
        return None


def _generation_context(form, document_type=None, **extra):
    categories = DocumentCategory.objects.filter(is_active=True)
    approved_templates = DocumentTemplate.objects.filter(
        document_type_id=OuterRef('pk'),
        is_active=True,
        active_version__status=DocumentTemplateVersion.APPROVED,
    )
    document_types = DocumentType.objects.filter(is_active=True).annotate(
        has_approved_template=Exists(approved_templates),
    )
    category_id = form.data.get('category') or form.initial.get('category')
    if category_id:
        document_types = document_types.filter(categories__id=category_id)
    return {
        'form': form,
        'categories': categories,
        'document_types': document_types.order_by('name'),
        'selected_type': document_type,
        'selected_category': str(category_id or ''),
        'has_approved_template': bool(
            document_type and form.fields['template_version'].queryset.exists()
        ),
        'missing_company_details': _missing_company_details(),
        **extra,
    }


def _missing_company_details():
    profile = CompanyDocumentProfile.objects.filter(pk=1).first()
    if profile is None:
        missing = ['legal entity name', 'brand name', 'registered/business address', 'LLPIN']
    else:
        missing = [
            label for label, value in (
                ('legal entity name', profile.legal_entity_name),
                ('brand name', profile.brand_name),
                ('registered/business address', profile.business_address or profile.registered_office_address),
                ('LLPIN', profile.llpin),
                ('official email', profile.official_email),
                ('contact number', profile.contact_number),
                ('website', profile.website),
            ) if not value
        ]
    if not CompanyDocumentSignatory.objects.filter(is_active=True, is_authorized=True).exists():
        missing.append('an active authorised signatory')
    return missing


@local_only
@staff_member_required
def template_preview(request, version_id):
    version = get_object_or_404(
        DocumentTemplateVersion.objects.select_related('template__document_type'),
        pk=version_id,
    )
    profile = CompanyDocumentProfile.current()
    values = {}
    for definition in version.template.document_type.field_definitions.filter(is_active=True):
        if definition.is_auto_populated and definition.source_module == 'company':
            value = resolve_source_value('company', definition.source_field, profile)
        elif definition.default_value:
            value = definition.default_value
        elif definition.value_type == 'boolean':
            value = 'Yes'
        elif definition.value_type == 'select' and definition.choices:
            value = definition.choices[0]
        else:
            value = f'[{definition.label}]'
        values[definition.field_key] = value
    signatory = CompanyDocumentSignatory.objects.filter(
        is_active=True, is_authorized=True,
    ).first()
    resolved = resolve_template(
        version,
        values,
        'TEMPLATE-PREVIEW',
        timezone.localdate(),
        None,
        signatory,
        profile,
        allow_review_clauses=True,
    )
    return render(request, 'document_generation/template_preview.html', {
        'version': version,
        'resolved': resolved,
        'preview_html': html_document_content(resolved['content']),
        'admin_url': reverse(
            'admin:document_generation_documenttemplateversion_change',
            args=(version.pk,),
        ),
        'preview_warnings': _missing_company_details(),
    })


@local_only
def generate_document(request):
    if request.method == 'POST' and request.POST.get('action') == 'generate':
        token = request.POST.get('preview_token', '')
        token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
        used_tokens = request.session.get('document_generation_used_previews', [])
        if token_hash in used_tokens:
            raise Http404('This preview has already been used.')
        payload = {}
        try:
            payload = load_preview(token)
            output_format = request.POST.get('output_format', '')
            generated = generate_document_file(payload, output_format, request.user)
        except ValidationError as error:
            return render(request, 'document_generation/document_preview.html', {
                'payload': payload,
                'preview_token': token,
                'errors': error.messages,
                'formats': tuple(payload.get('output_formats', ('docx', 'pdf', 'both'))),
                'preview_html': html_document_content(payload.get('rendered_content', '')),
            }, status=400)
        except IntegrityError:
            return render(request, 'document_generation/document_preview.html', {
                'payload': payload,
                'preview_token': token,
                'errors': ['This draft version was already revised or its document number was consumed. Preview again.'],
                'formats': tuple(payload.get('output_formats', ('docx', 'pdf', 'both'))),
                'preview_html': html_document_content(payload.get('rendered_content', '')),
            }, status=409)
        used_tokens.append(token_hash)
        request.session['document_generation_used_previews'] = used_tokens[-20:]
        messages.success(request, f'{generated.document_type.name} {generated.document_number} generated.')
        return render(request, 'document_generation/generated.html', {
            'document': generated,
            'word_url': reverse(
                'document_generation:download',
                args=(generated.pk, 'docx'),
            ) if generated.word_file_document_id else '',
            'pdf_url': reverse(
                'document_generation:download',
                args=(generated.pk, 'pdf'),
            ) if generated.pdf_file_document_id else '',
        })

    selected_type = (
        _approved_type(request.POST.get('document_type'))
        if request.method == 'POST'
        else _active_type(request.GET.get('type'))
    )
    if request.method == 'POST':
        form = UniversalDocumentForm(
            request.POST, document_type=selected_type,
            related_record=request.POST.get('related_record'),
        )
        if request.POST.get('action') == 'preview' and form.is_valid():
            try:
                payload = prepare_preview(form)
            except ValidationError as error:
                form.add_error(None, error)
            else:
                return render(request, 'document_generation/document_preview.html', {
                    'payload': payload,
                    'preview_html': html_document_content(payload['rendered_content']),
                    'preview_token': sign_preview(payload),
                    'formats': tuple(payload['output_formats']),
                    'default_format': (
                        selected_type.default_output_formats[0]
                        if selected_type.default_output_formats else 'both'
                    ),
                })
    else:
        initial = {}
        edit_payload = None
        edit_token = request.GET.get('edit')
        if edit_token:
            try:
                edit_payload = load_preview(edit_token)
                selected_type = _approved_type(edit_payload.get('document_type_id'))
            except ValidationError as error:
                messages.error(request, error.messages[0])
        if selected_type:
            initial['document_type'] = selected_type.pk
            selected_category = request.GET.get('category')
            category_queryset = selected_type.categories.filter(is_active=True)
            if selected_category and category_queryset.filter(pk=selected_category).exists():
                initial['category'] = selected_category
            else:
                initial['category'] = category_queryset.values_list('pk', flat=True).first()
            versions = DocumentTemplateVersion.objects.filter(
                template__document_type=selected_type,
                template__is_active=True,
                status=DocumentTemplateVersion.APPROVED,
                template__active_version_id=F('pk'),
            ).order_by('template__name')
            template_id = request.GET.get('template_version')
            if edit_payload:
                template_id = edit_payload.get('template_version_id')
            if template_id:
                initial['template_version'] = template_id
            elif versions.count() == 1:
                initial['template_version'] = versions.first().pk
        revision_id = request.GET.get('revise')
        if revision_id:
            initial['revision_of'] = revision_id
            previous = GeneratedDocument.objects.filter(pk=revision_id, status='draft').first()
            if previous:
                selected_type = previous.document_type
                initial.update({
                    'document_type': previous.document_type_id,
                    'category': previous.document_type.categories.filter(is_active=True).values_list(
                        'pk', flat=True,
                    ).first(),
                    'template_version': previous.template_version_id,
                })
        elif request.GET.get('category'):
            initial['category'] = request.GET['category']
        related_record = request.GET.get('entity')
        related_asset = request.GET.get('asset')
        if edit_payload:
            initial.update(edit_payload['field_values'])
            initial.update({
                'document_date': edit_payload['document_date'],
                'effective_date': edit_payload['effective_date'] or '',
                'authorised_signatory': edit_payload['signatory_id'],
                'revision_of': edit_payload.get('previous_document_id'),
            })
            party_records = edit_payload['party'].get('records', [])
            primary = next((row for row in party_records if row.get('module') != 'asset'), None)
            asset = next((row for row in party_records if row.get('module') == 'asset'), None)
            related_record = (
                f"{primary['module']}:{primary['object_id']}"
                if primary and primary.get('module') and primary.get('object_id') else ''
            )
            related_asset = asset.get('object_id', '') if asset else ''
        if related_record:
            initial['related_record'] = related_record
        if related_asset:
            initial['related_asset'] = related_asset
        form = UniversalDocumentForm(
            document_type=selected_type,
            related_record=related_record,
            initial=initial,
        )
    return render(
        request,
        'document_generation/generate_document.html',
        _generation_context(form, selected_type),
    )


@local_only
def download(request, document_id, output_format):
    if output_format not in {'docx', 'pdf'}:
        raise Http404
    generated = GeneratedDocument.objects.filter(pk=document_id).first()
    if generated is None:
        raise Http404
    reference = generated.word_file_document if output_format == 'docx' else generated.pdf_file_document
    if reference is None or reference.missing:
        raise Http404('The generated file is not available.')
    try:
        full_path(reference.file_path)
        file_object = private_storage.open(reference.file_path, 'rb')
    except (OSError, ValidationError) as error:
        raise Http404('The generated file is missing from private storage.') from error
    response = FileResponse(
        file_object,
        as_attachment=True,
        filename=reference.original_filename,
        content_type=reference.mime_type or 'application/octet-stream',
    )
    response['Cache-Control'] = 'no-store, private'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@local_only
def placeholder(request, section):
    labels = {
        'generate': 'Generate Document',
        'client-documents': 'Client Documents',
        'freelancer-documents': 'Freelancer Documents',
        'candidate-documents': 'Candidate Documents',
        'employee-documents': 'Employee Documents',
        'internal-documents': 'Internal Documents',
        'templates': 'Document Templates',
        'clauses': 'Clause Library',
        'approvals': 'Approval Centre',
        'acknowledgements': 'Digital Acknowledgements',
        'signed': 'Signed Documents',
        'expiring': 'Expiring Agreements',
        'history': 'Document History',
        'vendors': 'Vendor Documents',
    }
    label = labels.get(section)
    if label is None:
        raise Http404
    return render(request, 'document_generation/placeholder.html', {'section_title': label})
