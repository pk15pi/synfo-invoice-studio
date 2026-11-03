from django.conf import settings
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from employee_management.security import local_only

from . import services as S
from .forms import CreateForm, RestoreForm
from .models import BackupRecord


@local_only
def home(request):
    err = None
    try:
        folder = S.backup_dir()
        S.rescan()
    except S.BackupError as e:
        folder, err = None, str(e)
    return render(request, 'backup/home.html', {
        'records': BackupRecord.objects.all()[:200], 'folder': folder, 'err': err, 'form': CreateForm(),
        'keep': getattr(settings, 'BACKUP_KEEP_LAST', 10)})


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
