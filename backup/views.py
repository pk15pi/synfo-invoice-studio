from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from employee_management.security import local_only
from invoice_app.table_sorting import apply_queryset_sort

from . import services as S
from . import consolidation as C
from .forms import CreateForm, ImportPackageForm, MergeConfirmForm, RestoreForm
from .models import BackupRecord, InstanceIdentity, MergePackage


@local_only
def home(request):
    err = None
    try:
        folder = S.backup_dir()
        S.rescan()
    except S.BackupError as e:
        folder, err = None, str(e)
    records = BackupRecord.objects.all()
    records, _, _ = apply_queryset_sort(records, request, {
        'name': 'name', 'kind': 'kind', 'created': 'created_at', 'size': 'size',
        'verified': 'verify_ok', 'protected': 'protected', 'note': 'note',
    })
    packages = MergePackage.objects.all()
    packages, _, _ = apply_queryset_sort(packages, request, {'source': 'source_instance_name', 'status': 'status'})
    return render(request, 'backup/home.html', {
        'records': records[:200], 'folder': folder, 'err': err, 'form': CreateForm(),
        'keep': getattr(settings, 'BACKUP_KEEP_LAST', 10), 'identity': InstanceIdentity.current(),
        'import_form': ImportPackageForm(), 'packages': packages[:20]})


@local_only
@require_POST
def export_package(request):
    try:
        path, filename = C.export_package()
        return FileResponse(open(path, 'rb'), as_attachment=True, filename=filename,
                            content_type='application/zip')
    except S.BackupError as exc:
        messages.error(request, str(exc))
        return redirect('backup:home')


@local_only
@require_POST
def import_package(request):
    form = ImportPackageForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, '; '.join(form.errors.get('package', ['Select a valid ZIP package.'])))
        return redirect('backup:home')
    try:
        package = C.create_import_preview(form.cleaned_data['package'])
    except S.BackupError as exc:
        messages.error(request, str(exc))
        return redirect('backup:home')
    messages.info(request, 'Package validated. Review the preview before confirming any merge.')
    return redirect('backup:merge_preview', package_id=package.pk)


@local_only
def merge_preview(request, package_id):
    package = get_object_or_404(MergePackage, pk=package_id)
    return render(request, 'backup/merge_preview.html', {
        'package': package, 'form': MergeConfirmForm(),
    })


@local_only
def download_import_package(request, package_id):
    package = get_object_or_404(MergePackage, pk=package_id)
    path = Path(package.path)
    if path.parent.resolve() != (C.backup_services.backup_dir() / 'imports').resolve() or not path.is_file():
        messages.error(request, 'The retained import package is unavailable.')
        return redirect('backup:merge_preview', package_id=package.pk)
    return FileResponse(open(path, 'rb'), as_attachment=True, filename=package.uploaded_name,
                        content_type='application/zip')


@local_only
@require_POST
def confirm_merge(request, package_id):
    package = get_object_or_404(MergePackage, pk=package_id)
    form = MergeConfirmForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Confirm the reviewed preview before merging.')
        return redirect('backup:merge_preview', package_id=package.pk)
    if package.preview.get('changed_source_records') and not form.cleaned_data['keep_destination']:
        messages.error(request, 'Choose how to handle changed source records. Their incoming version remains in the package.')
        return redirect('backup:merge_preview', package_id=package.pk)
    try:
        C.merge_package(package, keep_destination=form.cleaned_data['keep_destination'])
    except S.BackupError as exc:
        messages.error(request, f'Merge refused; no database records were imported. {exc}')
        return redirect('backup:merge_preview', package_id=package.pk)
    messages.success(request, f'Imported {package.preview.get("merged_records", 0)} new records from '
                              f'{package.source_instance_name}. Existing records were preserved.')
    return redirect('backup:merge_preview', package_id=package.pk)


@local_only
@require_POST
def create(request):
    f = CreateForm(request.POST)
    note = f.data.get('note', '')[:300]
    try:
        rec = S.create_backup(kind=BackupRecord.MANUAL, note=note)
        ok, problems = S.verify(rec)
        messages.success(request, f'Backup {rec.name} created' + (' and verified.' if ok else f' but FAILED verification: {problems}'))
    except S.BackupError as e:
        messages.error(request, str(e))
    return redirect('backup:home')


@local_only
@require_POST
def verify(request, pk):
    rec = get_object_or_404(BackupRecord, pk=pk)
    ok, problems = S.verify(rec)
    (messages.success if ok else messages.error)(request, f'{rec.name}: ' + ('verified OK.' if ok else '; '.join(problems)))
    return redirect('backup:detail', pk=pk)


@local_only
@require_POST
def retention(request):
    removed = S.apply_retention()
    messages.success(request, f'Retention removed {len(removed)} backup(s): {", ".join(removed) or "none"}.')
    return redirect('backup:home')


@local_only
def detail(request, pk):
    rec = get_object_or_404(BackupRecord, pk=pk)
    form = RestoreForm(request.POST or None)
    unknown = pending = None
    if rec.verify_ok:
        try:
            unknown, pending = S.migration_compat(S.read_manifest(rec.path))
        except Exception:
            pass
    if request.method == 'POST' and form.is_valid():
        try:
            r = S.restore(rec, form.cleaned_data['phrase'])
            msg = (f'Restored {rec.name}. Pre-restore backup: {r["pre_restore"]}. RESTART runserver now '
                   f'(close this tab, stop the server, start it again).')
            if r['pending_migrations']:
                msg += ' The backup is older than the code: migrate the listed apps one by one before use.'
            messages.warning(request, msg)
            return redirect('backup:home')
        except S.BackupError as e:
            messages.error(request, f'Restore refused - nothing was changed. {e}')
    return render(request, 'backup/detail.html', {'rec': rec, 'form': form, 'unknown': unknown, 'pending': pending})
