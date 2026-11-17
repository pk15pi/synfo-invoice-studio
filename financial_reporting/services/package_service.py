"""ExportPackage generation service: runs each included ReportRun and zips them."""
import hashlib
import traceback
import uuid
import zipfile
from io import BytesIO

from django.core.files.base import ContentFile
from django.utils import timezone

from financial_reporting.models import ExportPackage
from financial_reporting.services.run_service import execute_report_run


def generate_package(export_package, user):
    """
    Generate a ZIP archive containing output for every included ExportPackageItem.

    For each item that has a report_run with a generated file, that file is added to the ZIP.
    For each item that has a report_run without a generated file (or in a non-succeeded state),
    execute_report_run() is called to produce the file first.

    Items of type 'source_document' are added from their document's stored file when available.

    Args:
        export_package: an ExportPackage instance.
        user: the requesting user.

    Returns:
        The ExportPackage instance with updated status and zip_file.
    """
    export_package.generation_status = ExportPackage.RUNNING
    export_package.save(update_fields=['generation_status'])

    manifest_files = []
    errors = []
    zip_buf = BytesIO()

    try:
        with zipfile.ZipFile(zip_buf, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
            items = export_package.items.filter(included=True).select_related('report_run__report_definition',
                                                                               'source_document')
            for item in items:
                try:
                    if item.item_type == 'report' and item.report_run_id:
                        run = item.report_run
                        # Re-run if not succeeded or no file
                        if run.status != 'succeeded' or not run.generated_file:
                            period_params = {
                                'date_from': export_package.from_date.isoformat(),
                                'date_to': export_package.to_date.isoformat(),
                                'period': 'custom',
                            }
                            from reporting import periods as P
                            rp_period = P.resolve(period_params)
                            run = execute_report_run(
                                run.report_definition,
                                rp_period,
                                run.output_format or 'xlsx',
                                user,
                            )
                            # Update the item's run reference if a new run was created
                            if run.pk != item.report_run_id:
                                item.report_run = run
                                item.save(update_fields=['report_run'])

                        if run.generated_file:
                            with run.generated_file.open('rb') as fh:
                                file_bytes = fh.read()
                            arcname = item.file_name or f'{run.report_definition.report_code}.{run.output_format or "bin"}'
                            zf.writestr(arcname, file_bytes)
                            sha = hashlib.sha256(file_bytes).hexdigest()
                            manifest_files.append({
                                'file': arcname,
                                'report_code': run.report_definition.report_code,
                                'report_run_id': run.pk,
                                'size': len(file_bytes),
                                'sha256': sha,
                                'status': run.status,
                            })
                        else:
                            errors.append(f'{item.file_name}: report run {run.pk} produced no file (status={run.status}).')

                    elif item.item_type == 'source_document' and item.source_document_id:
                        doc = item.source_document
                        # Attempt to read a stored file from the document object
                        doc_file = getattr(doc, 'file', None) or getattr(doc, 'document_file', None)
                        if doc_file and doc_file.name:
                            with doc_file.open('rb') as fh:
                                file_bytes = fh.read()
                            arcname = item.file_name or doc_file.name.rsplit('/', 1)[-1]
                            zf.writestr(arcname, file_bytes)
                            sha = hashlib.sha256(file_bytes).hexdigest()
                            manifest_files.append({
                                'file': arcname,
                                'document_id': doc.pk,
                                'size': len(file_bytes),
                                'sha256': sha,
                            })
                        else:
                            errors.append(f'{item.file_name}: source document {item.source_document_id} has no stored file.')
                    else:
                        errors.append(f'{item.file_name}: unrecognised item type or missing reference.')

                except Exception:
                    errors.append(f'{item.file_name}: {traceback.format_exc()}')

        # Save the ZIP
        zip_bytes = zip_buf.getvalue()
        zip_name = f'{uuid.uuid4().hex}.zip'
        export_package.zip_file.save(zip_name, ContentFile(zip_bytes, name=zip_name), save=False)

        export_package.manifest = {
            'files': manifest_files,
            'errors': errors,
            'generated_at': timezone.now().isoformat(),
            'user': str(user),
            'package_number': export_package.package_number,
        }
        export_package.generation_status = ExportPackage.SUCCEEDED if not errors else ExportPackage.INCOMPLETE
        export_package.generated_at = timezone.now()
        export_package.save(update_fields=['zip_file', 'manifest', 'generation_status', 'generated_at'])

    except Exception:
        export_package.manifest = {
            'files': manifest_files,
            'errors': errors + [traceback.format_exc()],
            'generated_at': timezone.now().isoformat(),
            'user': str(user),
        }
        export_package.generation_status = ExportPackage.FAILED
        export_package.generated_at = timezone.now()
        export_package.save(update_fields=['manifest', 'generation_status', 'generated_at'])

    return export_package
