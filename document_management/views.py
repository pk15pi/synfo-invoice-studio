from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from invoice_app.pagination import paginate as qb_paginate
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from employee_management.security import local_only
from invoice_app.table_sorting import apply_queryset_sort

from . import services as S
from .forms import EditForm, UploadForm
from .models import Document, full_path


@local_only
def doc_list(request):
    qs = Document.objects.select_related('content_type')
    q, cat, sens, state = (request.GET.get(k, '').strip() for k in ('q', 'category', 'sensitivity', 'state'))
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(tags__icontains=q) | Q(original_filename__icontains=q))
    if cat:
        qs = qs.filter(category=cat)
    if sens:
        qs = qs.filter(sensitivity=sens)
    if state == 'missing':
        qs = qs.filter(missing=True)
    elif state == 'ok':
        qs = qs.filter(missing=False)
    qs, _, _ = apply_queryset_sort(qs, request, {
        'uploaded': 'uploaded_at', 'category': 'category', 'title': 'title',
        'linked': 'object_id', 'sensitivity': 'sensitivity', 'tags': 'tags',
        'size': 'size', 'hash': 'sha256',
    })
    page = qb_paginate(request, qs)
    page_query = request.GET.copy()
    page_query.pop('page', None)
    return render(request, 'document_management/list.html', {
        'page': page, 'q': q, 'category': cat, 'sensitivity': sens, 'state': state,
        'categories': Document.CATEGORIES, 'sensitivities': Document.SENSITIVITY, 'page_query': page_query.urlencode(),
        'missing_count': Document.objects.filter(missing=True).count(), 'total': Document.objects.count()})


@local_only
def upload(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        c = form.cleaned_data
        try:
            S.upload_company_doc(c['file'], c['category'], c['title'], c['sensitivity'], c['tags'])
        except ValidationError as ex:
            form.add_error('file', ex.messages)
        else:
            messages.success(request, 'Document stored privately and indexed.')
            return redirect('docs:list')
    return render(request, 'document_management/upload.html', {'form': form})


@local_only
def edit(request, pk):
    doc = get_object_or_404(Document, pk=pk)
    form = EditForm(request.POST or None, instance=doc)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Saved.')
        return redirect('docs:list')
    return render(request, 'document_management/edit.html', {'form': form, 'doc': doc})


@local_only
def serve(request, pk):
    doc = get_object_or_404(Document, pk=pk)
    try:
        fh = open(full_path(doc.file_path), 'rb')
    except (ValidationError, OSError):
        raise Http404('File is missing from private storage.')
    name = doc.original_filename or doc.file_path.rsplit('/', 1)[-1]
    resp = FileResponse(fh, content_type=doc.mime_type or 'application/octet-stream', as_attachment=True,
                        filename=name)
    resp['Cache-Control'] = 'no-store, private'
    resp['X-Content-Type-Options'] = 'nosniff'
    resp['Content-Security-Policy'] = "default-src 'none'; sandbox"
    return resp


@local_only
def check(request):
    result = None
    if request.method == 'POST':
        if request.POST.get('action') == 'sync':
            s = S.sync_index()
            messages.success(request, f"Index synced: {s['created']} new, {s['updated']} updated, "
                                      f"{s['missing']} missing file(s).")
            return redirect('docs:check')
        result = S.check_documents()
    return render(request, 'document_management/check.html', {'result': result,
                                                              'missing': Document.objects.filter(missing=True)})
