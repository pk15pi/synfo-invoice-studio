"""Versioned, additive data exchange for independent invoiceStudio installations."""
import hashlib
import json
import os
import re
import stat
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.db.migrations.recorder import MigrationRecorder
from django.db.models import FileField
from django.utils import timezone
from django.utils.text import get_valid_filename

from employee_management.storage import private_storage

from . import services as backup_services
from .models import BackupRecord, InstanceIdentity, MergePackage, RecordOrigin

FORMAT_VERSION = 1
MANIFEST = 'manifest.json'
DATA = 'data.json'
CHECKSUMS = 'checksums.json'
REPORT = 'export_report.json'
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 25000
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
_SHA256 = re.compile(r'^[a-f0-9]{64}$')


class ConsolidationError(backup_services.BackupError):
    pass


def _json_bytes(value):
    return json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _installed_models():
    return {
        model._meta.label_lower: model
        for model in apps.get_models()
        if model._meta.managed and not model._meta.proxy
        and model._meta.app_label not in {'backup', 'contenttypes'}
    }


def _migrations():
    return [f'{app}.{name}' for app, name in MigrationRecorder.Migration.objects.order_by('app', 'name')
            .values_list('app', 'name')]


def _storage_kind(storage):
    location = getattr(storage, 'location', None)
    if not location:
        raise ConsolidationError('Only filesystem-backed attachments can be exported by this package format.')
    resolved = Path(location).resolve()
    if resolved == Path(settings.EMPLOYEE_PRIVATE_ROOT).resolve():
        return 'private'
    if resolved == Path(settings.MEDIA_ROOT).resolve():
        return 'media'
    raise ConsolidationError(f'Attachment storage {resolved} is not a configured private/media root.')


def _safe_storage_name(name):
    if not isinstance(name, str) or not name:
        raise ConsolidationError('Attachment has an empty storage name.')
    normalized = name.replace('\\', '/')
    path = PurePosixPath(normalized)
    if (path.is_absolute() or any(part in ('', '.', '..') for part in normalized.split('/'))
            or ':' in path.parts[0]):
        raise ConsolidationError('Attachment has an unsafe storage path.')
    return normalized


def _origin_index():
    return {
        (row.model_label, row.destination_pk): (str(row.origin_instance_id), row.origin_record_id)
        for row in RecordOrigin.objects.all()
    }


def _identity_for(model, pk, instance_id, origins):
    label = model._meta.label_lower
    return origins.get((label, str(pk)), (str(instance_id), str(pk)))


def _ref_for(model, pk, instance_id, origins):
    if pk is None:
        return None
    if model is ContentType:
        content_type = ContentType.objects.get(pk=pk)
        return {'natural': [content_type.app_label, content_type.model]}
    origin_instance, origin_pk = _identity_for(model, pk, instance_id, origins)
    return {'ref': {'model': model._meta.label_lower, 'instance_id': origin_instance, 'record_id': origin_pk}}


def _document_source_model(source_type):
    return {
        'employee_document': 'employee_management.employeedocument',
        'company_expense_attachment': 'expense_management.companyexpenseattachment',
        'employee_expense_attachment': 'expense_management.employeeexpenseattachment',
    }.get(source_type)


def _read_attachment(field, name, storage=None):
    name = _safe_storage_name(name)
    storage = storage or field.storage
    kind = _storage_kind(storage)
    local_path = None
    try:
        local_path = Path(storage.path(name))
        before = local_path.stat()
    except (AttributeError, NotImplementedError, OSError):
        before = None

    chunks = []
    total = 0
    with storage.open(name, 'rb') as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise ConsolidationError(f'Attachment exceeds the {MAX_FILE_BYTES // (1024 * 1024)} MB package limit: {name}')
            chunks.append(chunk)

    if before is not None:
        try:
            after = local_path.stat()
        except OSError as exc:
            raise ConsolidationError(f'Attachment changed or disappeared during export: {name}') from exc
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ConsolidationError(f'Attachment changed during export: {name}')
    content = b''.join(chunks)
    if total != len(content):
        raise ConsolidationError(f'Attachment size changed during export: {name}')
    return kind, content


def export_package():
    """Create and verify a portable export; the final ZIP is published only on success."""
    identity = InstanceIdentity.current()
    export_id = uuid.uuid4()
    timestamp = timezone.now()
    models_by_label = _installed_models()
    origins = _origin_index()
    records = []
    file_payloads = {}
    file_refs = []
    counts = {}

    try:
        with transaction.atomic():
            for label, model in sorted(models_by_label.items()):
                model_records = []
                for obj in model._default_manager.order_by(model._meta.pk.name).iterator(chunk_size=500):
                    source_instance, source_pk = _identity_for(model, obj.pk, identity.instance_id, origins)
                    record_identity = {'model': label, 'instance_id': source_instance, 'record_id': source_pk}
                    fields = {}
                    file_hashes = {}
                    for field in model._meta.concrete_fields:
                        if field.primary_key:
                            continue
                        value = getattr(obj, field.attname)
                        if label == 'document_management.document' and field.name == 'object_id' and value:
                            content_type = obj.content_type
                            target_model = apps.get_model(content_type.app_label, content_type.model) if content_type else None
                            fields[field.name] = (_ref_for(target_model, value, identity.instance_id, origins)
                                                  if target_model else value)
                        elif label == 'document_management.document' and field.name == 'source_id' and value:
                            source_model_label = _document_source_model(obj.source_type)
                            source_model = apps.get_model(source_model_label) if source_model_label else None
                            fields[field.name] = (_ref_for(source_model, value, identity.instance_id, origins)
                                                  if source_model else value)
                        elif field.is_relation and field.remote_field:
                            fields[field.name] = _ref_for(field.remote_field.model, value, identity.instance_id, origins)
                        elif isinstance(field, FileField):
                            name = getattr(obj, field.name).name
                            fields[field.name] = name
                            if name:
                                storage_kind, content = _read_attachment(field, name)
                                digest = _sha256(content)
                                file_hashes[field.name] = digest
                                archive_path = f'files/{digest}'
                                file_payloads.setdefault(archive_path, content)
                                file_refs.append({
                                    'model': label, 'identity': record_identity, 'field': field.name,
                                    'storage': storage_kind, 'storage_name': _safe_storage_name(name),
                                    'archive_path': archive_path, 'sha256': digest, 'size': len(content),
                                })
                        elif label == 'document_management.document' and field.name == 'file_path' and value:
                            storage_name = _safe_storage_name(value)
                            fields[field.name] = storage_name
                            storage_kind, content = _read_attachment(None, storage_name, storage=private_storage)
                            digest = _sha256(content)
                            file_hashes[field.name] = digest
                            archive_path = f'files/{digest}'
                            file_payloads.setdefault(archive_path, content)
                            file_refs.append({
                                'model': label, 'identity': record_identity, 'field': field.name,
                                'storage': storage_kind, 'storage_name': storage_name,
                                'archive_path': archive_path, 'sha256': digest, 'size': len(content),
                            })
                        else:
                            fields[field.name] = value
                    m2m = {}
                    for field in model._meta.many_to_many:
                        m2m[field.name] = [
                            _ref_for(field.remote_field.model, pk, identity.instance_id, origins)
                            for pk in getattr(obj, field.name).values_list('pk', flat=True)
                        ]
                    source_hash = _sha256(_json_bytes({
                        'identity': record_identity, 'fields': fields, 'm2m': m2m, 'file_hashes': file_hashes,
                    }))
                    model_records.append({
                        'identity': record_identity, 'fields': fields, 'm2m': m2m,
                        'file_hashes': file_hashes, 'source_hash': source_hash,
                    })
                counts[label] = len(model_records)
                records.extend(model_records)

        payload = {'records': records, 'files': file_refs}
        data_bytes = _json_bytes(payload)
        report = {
            'completion_status': 'complete',
            'entity_counts': counts,
            'total_records': len(records),
            'file_count': len(file_refs),
            'unique_file_count': len(file_payloads),
            'created_at': timestamp,
        }
        report_bytes = _json_bytes(report)
        if sum(len(content) for content in file_payloads.values()) + len(data_bytes) + len(report_bytes) > MAX_UNCOMPRESSED_BYTES:
            raise ConsolidationError('Export exceeds the 512 MB package safety limit.')
        manifest = {
            'format_version': FORMAT_VERSION,
            'source_instance_id': str(identity.instance_id),
            'source_instance_name': identity.name,
            'app_version': backup_services.app_version(),
            'schema_migrations': _migrations(),
            'export_id': str(export_id),
            'exported_at': timestamp,
            'export_type': 'merge',
            'completion_status': 'complete',
            'entity_counts': counts,
            'total_records': len(records),
            'file_count': len(file_refs),
            'total_attachment_bytes': sum(item['size'] for item in file_refs),
        }
        archive_entries = {DATA: data_bytes, REPORT: report_bytes, **file_payloads}
        checksum_bytes = _json_bytes({name: _sha256(content) for name, content in sorted(archive_entries.items())})
        out = backup_services.backup_dir() / 'exports'
        out.mkdir(parents=True, exist_ok=True)
        filename = f'invoiceStudio_{str(identity.instance_id)[:8]}_{timestamp:%Y%m%d-%H%M%S}_{str(export_id)[:8]}.zip'
        target = out / filename
        partial = out / f'.{filename}.partial'
        try:
            with zipfile.ZipFile(partial, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                archive.writestr(MANIFEST, _json_bytes(manifest))
                archive.writestr(CHECKSUMS, checksum_bytes)
                for name, content in archive_entries.items():
                    archive.writestr(name, content)
            _read_package(partial, verify_compatibility=False, check_extension=False)
            os.replace(partial, target)
        finally:
            if partial.exists():
                partial.unlink()
        identity.last_export_at = timestamp
        identity.last_export_id = export_id
        identity.save(update_fields=['last_export_at', 'last_export_id'])
        return target, filename
    except (OSError, ValueError, TypeError, zipfile.BadZipFile) as exc:
        raise ConsolidationError(f'Export failed; no completed package was published: {exc}') from exc


def _read_package(path, verify_compatibility=True, check_extension=True):
    path = Path(path)
    if (check_extension and path.suffix.lower() != '.zip') or not path.is_file():
        raise ConsolidationError('Choose a readable .zip export package.')
    if path.stat().st_size > MAX_UPLOAD_BYTES:
        raise ConsolidationError(f'Package exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit.')

    try:
        with zipfile.ZipFile(path, 'r') as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ARCHIVE_ENTRIES:
                raise ConsolidationError('Package is empty or contains too many archive entries.')
            names = [entry.filename for entry in infos]
            if len(names) != len(set(names)):
                raise ConsolidationError('Package contains duplicate ZIP entry names.')
            total_size = 0
            for entry in infos:
                if entry.flag_bits & 0x1:
                    raise ConsolidationError('Encrypted ZIP packages are not supported.')
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ConsolidationError('Symbolic links are not allowed in export packages.')
                total_size += entry.file_size
                if entry.file_size > MAX_FILE_BYTES + 64 * 1024 * 1024:
                    raise ConsolidationError('A package entry exceeds the per-file size limit.')
                if entry.compress_size == 0 and entry.file_size:
                    raise ConsolidationError('Package contains an invalid compression ratio.')
                if entry.compress_size and entry.file_size / entry.compress_size > MAX_COMPRESSION_RATIO:
                    raise ConsolidationError('Package exceeds the maximum compression ratio.')
            if total_size > MAX_UNCOMPRESSED_BYTES:
                raise ConsolidationError('Package expands beyond the 2 GB safety limit.')
            if archive.testzip() is not None:
                raise ConsolidationError('Package CRC validation failed.')
            allowed_fixed = {MANIFEST, DATA, CHECKSUMS, REPORT}
            file_entries = set()
            for name in names:
                if name in allowed_fixed:
                    continue
                if not re.fullmatch(r'files/[a-f0-9]{64}', name):
                    raise ConsolidationError(f'Unexpected or unsafe archive entry: {name}')
                file_entries.add(name)
            if not allowed_fixed.issubset(names):
                raise ConsolidationError('Package is missing a required manifest, data, checksums, or report file.')
            manifest = json.loads(archive.read(MANIFEST))
            payload = json.loads(archive.read(DATA))
            checksums = json.loads(archive.read(CHECKSUMS))
            report = json.loads(archive.read(REPORT))
            if (not isinstance(manifest, dict) or not isinstance(payload, dict)
                    or not isinstance(checksums, dict) or not isinstance(report, dict)):
                raise ConsolidationError('Package JSON has an invalid top-level shape.')
            expected_names = {DATA, REPORT} | file_entries
            if set(checksums) != expected_names:
                raise ConsolidationError('Checksum inventory does not match the archive contents.')
            content_by_name = {}
            for name in expected_names:
                checksum = checksums.get(name)
                if not isinstance(checksum, str) or not _SHA256.fullmatch(checksum):
                    raise ConsolidationError(f'Invalid checksum entry for {name}.')
                content = archive.read(name)
                if _sha256(content) != checksum:
                    raise ConsolidationError(f'Checksum mismatch for {name}.')
                content_by_name[name] = content
    except ConsolidationError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError, UnicodeDecodeError, RuntimeError) as exc:
        raise ConsolidationError(f'Package is malformed or unreadable: {exc}') from exc

    if manifest.get('format_version') != FORMAT_VERSION:
        raise ConsolidationError(f'Unsupported package format version: {manifest.get("format_version")!r}.')
    try:
        source_instance_id = uuid.UUID(str(manifest['source_instance_id']))
        uuid.UUID(str(manifest['export_id']))
        exported_at = datetime.fromisoformat(str(manifest['exported_at']).replace('Z', '+00:00'))
    except (KeyError, ValueError, TypeError) as exc:
        raise ConsolidationError('Manifest source identity, export ID, or timestamp is invalid.') from exc
    if exported_at.utcoffset() is None:
        raise ConsolidationError('Manifest export timestamp must include a timezone.')
    if manifest.get('export_type') != 'merge' or manifest.get('completion_status') != 'complete':
        raise ConsolidationError('Only completed merge-export packages can be imported.')
    if report.get('completion_status') != 'complete':
        raise ConsolidationError('Export report does not mark the package complete.')
    if not isinstance(manifest.get('source_instance_name'), str) or not manifest['source_instance_name'].strip():
        raise ConsolidationError('Manifest source instance name is missing.')
    if not isinstance(manifest.get('app_version'), str) or not manifest['app_version'].strip():
        raise ConsolidationError('Manifest application version is missing.')
    if not isinstance(payload.get('records'), list) or not isinstance(payload.get('files'), list):
        raise ConsolidationError('Package record or attachment inventory is invalid.')
    if manifest.get('total_records') != len(payload['records']) or report.get('total_records') != len(payload['records']):
        raise ConsolidationError('Package record count does not match its manifest.')
    if manifest.get('file_count') != len(payload['files']) or report.get('file_count') != len(payload['files']):
        raise ConsolidationError('Package attachment count does not match its manifest.')

    model_map = _installed_models()
    identities = set()
    records_by_identity = {}
    for record in payload['records']:
        if not isinstance(record, dict) or not isinstance(record.get('identity'), dict):
            raise ConsolidationError('Package contains a malformed record.')
        ident = record['identity']
        label = ident.get('model')
        model = model_map.get(label)
        if model is None:
            raise ConsolidationError(f'Package contains an unsupported entity type: {label!r}.')
        try:
            origin_instance_id = str(uuid.UUID(str(ident['instance_id'])))
            source_pk = str(ident['record_id'])
            model._meta.pk.to_python(source_pk)
        except (KeyError, ValueError, TypeError) as exc:
            raise ConsolidationError(f'Invalid source identity for {label}.') from exc
        if not source_pk or len(source_pk) > 100:
            raise ConsolidationError(f'Invalid source identity for {label}.')
        key = (label, origin_instance_id, source_pk)
        if key in identities:
            raise ConsolidationError(f'Duplicate source record identity in package: {label} {source_pk}.')
        identities.add(key)
        if not isinstance(record.get('fields'), dict) or not isinstance(record.get('m2m'), dict):
            raise ConsolidationError(f'Invalid field data for {label} {source_pk}.')
        source_hash = record.get('source_hash')
        file_hashes = record.get('file_hashes')
        if not isinstance(file_hashes, dict):
            raise ConsolidationError(f'Attachment checksums are invalid for {label} {source_pk}.')
        calculated_hash = _sha256(_json_bytes({
            'identity': ident, 'fields': record['fields'], 'm2m': record['m2m'], 'file_hashes': file_hashes,
        }))
        if not isinstance(source_hash, str) or not _SHA256.fullmatch(source_hash) or source_hash != calculated_hash:
            raise ConsolidationError(f'Record checksum is invalid for {label} {source_pk}.')
        concrete = {field.name: field for field in model._meta.concrete_fields if not field.primary_key}
        many_to_many = {field.name: field for field in model._meta.many_to_many}
        if set(record['fields']) != set(concrete) or set(record['m2m']) != set(many_to_many):
            raise ConsolidationError(f'Field inventory is incomplete or unexpected for {label} {source_pk}.')
        for name, value in record['fields'].items():
            field = concrete[name]
            if value is None and not field.null:
                raise ConsolidationError(f'Required field {label}.{name} is null for record {source_pk}.')
            if field.is_relation and field.remote_field:
                _validate_reference(value, label, name)
            elif label == 'document_management.document' and name in {'object_id', 'source_id'} and isinstance(value, dict):
                _validate_reference(value, label, name)
            elif isinstance(field, FileField) and value:
                _safe_storage_name(value)
            elif label == 'document_management.document' and name == 'file_path' and value:
                _safe_storage_name(value)
            elif value is not None:
                field.to_python(value)
        expected_hash_fields = {
            name for name, value in record['fields'].items()
            if (isinstance(model._meta.get_field(name), FileField)
                or (label == 'document_management.document' and name == 'file_path')) and value
        }
        if set(file_hashes) != expected_hash_fields or any(
                not isinstance(digest, str) or not _SHA256.fullmatch(digest)
                for digest in file_hashes.values()):
            raise ConsolidationError(
                f'Attachment checksums do not match {label} {source_pk}: '
                f'expected {sorted(expected_hash_fields)}, got {sorted(file_hashes)}; '
                f'file_path={record["fields"].get("file_path")!r}.'
            )
        for name, refs in record['m2m'].items():
            if not isinstance(refs, list):
                raise ConsolidationError(f'Invalid many-to-many data for {label}.{name}.')
            for ref in refs:
                _validate_reference(ref, label, name)
        records_by_identity[key] = record

    known_origins = _origin_rows()
    for record in payload['records']:
        model = model_map[record['identity']['model']]
        for name, value in record['fields'].items():
            field = model._meta.get_field(name)
            if field.is_relation and field.remote_field:
                _validate_reference_target(value, field.remote_field.model, identities, known_origins, model._meta.label, name)
            elif model._meta.label_lower == 'document_management.document' and name in {'object_id', 'source_id'} and isinstance(value, dict):
                content_type = record['fields'].get('content_type')
                if name == 'object_id':
                    if not content_type or 'natural' not in content_type:
                        raise ConsolidationError('Document generic object reference has no content type.')
                    target = apps.get_model(*content_type['natural'])
                else:
                    target_label = _document_source_model(record['fields'].get('source_type'))
                    target = apps.get_model(target_label) if target_label else None
                    if target is None:
                        raise ConsolidationError('Document source reference has an unsupported source type.')
                _validate_reference_target(value, target, identities, known_origins, model._meta.label, name)
        for name, refs in record['m2m'].items():
            field = model._meta.get_field(name)
            for ref in refs:
                _validate_reference_target(ref, field.remote_field.model, identities, known_origins,
                                           model._meta.label, name)

    file_ref_keys = set()
    for file_ref in payload['files']:
        if not isinstance(file_ref, dict):
            raise ConsolidationError('Package contains a malformed attachment reference.')
        identity_key = _identity_key(file_ref.get('identity'))
        key = (file_ref.get('model'), identity_key, file_ref.get('field'))
        if key in file_ref_keys:
            raise ConsolidationError('Package contains duplicate attachment references.')
        file_ref_keys.add(key)
        model = model_map.get(file_ref.get('model'))
        field = next((f for f in model._meta.concrete_fields if f.name == file_ref.get('field')), None) if model else None
        indexed_private_file = (
            file_ref.get('model') == 'document_management.document' and file_ref.get('field') == 'file_path'
        )
        if (not isinstance(field, FileField) and not indexed_private_file) or identity_key not in records_by_identity:
            raise ConsolidationError('Attachment references an unknown record or non-file field.')
        if file_ref.get('archive_path') not in file_entries or file_ref.get('sha256') != file_ref.get('archive_path', '').removeprefix('files/'):
            raise ConsolidationError('Attachment content path or checksum is invalid.')
        if (file_ref.get('size') != len(content_by_name[file_ref['archive_path']])
                or file_ref['size'] > MAX_FILE_BYTES):
            raise ConsolidationError('Attachment size does not match its manifest.')
        _safe_storage_name(file_ref.get('storage_name'))
        if file_ref.get('storage') not in {'private', 'media'}:
            raise ConsolidationError('Attachment storage type is unsupported.')
        if records_by_identity[identity_key]['fields'].get(file_ref['field']) != file_ref['storage_name']:
            raise ConsolidationError('Attachment reference does not match its model field.')
        if records_by_identity[identity_key]['file_hashes'].get(file_ref['field']) != file_ref['sha256']:
            raise ConsolidationError('Attachment checksum does not match its record version.')
        _validate_attachment_content(file_ref['storage_name'], content_by_name[file_ref['archive_path']])
    expected_file_refs = {
        (record['identity']['model'], _identity_key(record['identity']), field_name)
        for record in payload['records']
        for field_name, value in record['fields'].items()
        if value and (
    isinstance(model_map[record['identity']['model']]._meta.get_field(field_name), FileField)
    or (record['identity']['model'] == 'document_management.document' and field_name == 'file_path')
        )
    }
    if file_ref_keys != expected_file_refs:
        raise ConsolidationError('One or more database attachment references have no packaged file.')

    if verify_compatibility:
        package_migrations = manifest.get('schema_migrations')
        if (not isinstance(package_migrations, list) or any(not isinstance(item, str) for item in package_migrations)
                or len(package_migrations) != len(set(package_migrations))):
            raise ConsolidationError('Manifest migration inventory is invalid.')
        package_migrations = set(package_migrations)
        current_migrations = set(_migrations())
        known_migrations = set(backup_services.known_migrations())
        if current_migrations != known_migrations or package_migrations != current_migrations:
            raise ConsolidationError('Database schema is incompatible; source and destination migrations must match.')
    entity_counts = manifest.get('entity_counts')
    if not isinstance(entity_counts, dict) or report.get('entity_counts') != entity_counts:
        raise ConsolidationError('Entity-count summaries are missing or inconsistent.')
    actual_counts = {}
    for record in payload['records']:
        label = record['identity']['model']
        actual_counts[label] = actual_counts.get(label, 0) + 1
    if actual_counts != {label: count for label, count in entity_counts.items() if count}:
        raise ConsolidationError('Record counts do not match the entity summary.')
    if manifest.get('total_attachment_bytes') != sum(item['size'] for item in payload['files']):
        raise ConsolidationError('Attachment byte count does not match the manifest.')
    return {
        'manifest': manifest,
        'payload': payload,
        'report': report,
        'content_by_name': content_by_name,
        'source_instance_id': source_instance_id,
        'export_id': uuid.UUID(str(manifest['export_id'])),
    }


def _identity_key(identity):
    if not isinstance(identity, dict):
        raise ConsolidationError('Record reference identity is malformed.')
    try:
        return (identity['model'], str(uuid.UUID(str(identity['instance_id']))), str(identity['record_id']))
    except (KeyError, ValueError, TypeError) as exc:
        raise ConsolidationError('Record reference identity is malformed.') from exc


def _validate_reference(value, owner_label, field_name):
    if value is None:
        return
    if not isinstance(value, dict) or len(value) != 1:
        raise ConsolidationError(f'Invalid relation reference on {owner_label}.{field_name}.')
    if 'ref' in value:
        ref = value['ref']
        key = _identity_key(ref)
        if key[0] not in _installed_models():
            raise ConsolidationError(f'Unknown relation target {key[0]!r}.')
    elif 'natural' in value:
        natural = value['natural']
        if not isinstance(natural, list) or len(natural) != 2 or not all(isinstance(v, str) for v in natural):
            raise ConsolidationError(f'Invalid natural relation on {owner_label}.{field_name}.')
        if owner_label != 'document_management.document' and field_name != 'content_type':
            raise ConsolidationError(f'Unsupported natural relation on {owner_label}.{field_name}.')
    else:
        raise ConsolidationError(f'Invalid relation reference on {owner_label}.{field_name}.')


def _validate_reference_target(value, target_model, package_identities, known_origins, owner_label, field_name):
    if value is None:
        return
    if 'natural' in value:
        if target_model is not ContentType:
            raise ConsolidationError(f'Unexpected natural relation on {owner_label}.{field_name}.')
        try:
            ContentType.objects.get_by_natural_key(*value['natural'])
        except ContentType.DoesNotExist as exc:
            raise ConsolidationError(f'Unknown content type on {owner_label}.{field_name}.') from exc
        return
    ref = value['ref']
    if ref['model'] != target_model._meta.label_lower:
        raise ConsolidationError(f'Relation target mismatch on {owner_label}.{field_name}.')
    key = _identity_key(ref)
    if key not in package_identities and key not in known_origins:
        raise ConsolidationError(f'Missing related record {key[0]}#{key[2]} for {owner_label}.{field_name}.')


def _validate_attachment_content(name, content):
    lower_name = name.lower()
    if content.startswith((b'MZ', b'\x7fELF', b'#!')):
        raise ConsolidationError(f'Executable attachment content is not allowed: {name}.')
    signatures = {'.pdf': b'%PDF-', '.png': b'\x89PNG\r\n\x1a\n', '.jpg': b'\xff\xd8\xff',
                  '.jpeg': b'\xff\xd8\xff'}
    for extension, signature in signatures.items():
        if lower_name.endswith(extension) and not content.startswith(signature):
            raise ConsolidationError(f'Attachment content does not match its file type: {name}.')


def _origin_rows():
    return {
        (row.model_label, str(row.origin_instance_id), row.origin_record_id): row
        for row in RecordOrigin.objects.all()
    }


def _record_conflicts(model, record):
    conflicts = []
    fields = record['fields']
    constraints = []
    constraints.extend((field.name,) for field in model._meta.concrete_fields if field.unique and not field.primary_key)
    if not model._meta.pk.auto_created:
        constraints.append((model._meta.pk.name,))
    constraints.extend(tuple(group) for group in model._meta.unique_together)
    for constraint in model._meta.constraints:
        if getattr(constraint, 'fields', None) and not getattr(constraint, 'condition', None):
            constraints.append(tuple(constraint.fields))
    for names in constraints:
        values = {}
        supported = True
        for name in names:
            field = model._meta.get_field(name)
            value = record['identity']['record_id'] if field.primary_key else fields.get(name)
            if field.is_relation and field.remote_field:
                if value is None:
                    values[field.attname] = None
                else:
                    supported = False
                    break
            else:
                if value in (None, '') or isinstance(value, dict):
                    supported = False
                    break
                values[field.attname if field.primary_key else name] = value
        if supported and model._default_manager.filter(**values).exists():
            conflicts.append({'fields': list(names), 'values': {key: str(value) for key, value in values.items()}})
    return conflicts


def _potential_duplicates(model, record):
    watch = {'email', 'phone', 'employee_id', 'invoice_number', 'number', 'code', 'tax_id'}
    results = []
    for name in watch:
        value = record['fields'].get(name)
        if not isinstance(value, str) or not value.strip():
            continue
        field = model._meta.get_field(name) if name in {f.name for f in model._meta.fields} else None
        if field is None or field.is_relation:
            continue
        lookup = {f'{name}__iexact': value.strip()} if field.get_internal_type() in {'CharField', 'EmailField', 'SlugField'} else {name: value}
        match = model._default_manager.filter(**lookup).values_list(model._meta.pk.name, flat=True).first()
        if match is not None:
            results.append({'model': model._meta.label, 'field': name, 'value': value[:80], 'destination_pk': str(match)})
    return results


def _live_snapshot(model, obj, instance_id, origins):
    fields = {}
    file_hashes = {}
    for field in model._meta.concrete_fields:
        if field.primary_key:
            continue
        value = getattr(obj, field.attname)
        if model._meta.label_lower == 'document_management.document' and field.name == 'object_id' and value:
            content_type = obj.content_type
            target = apps.get_model(content_type.app_label, content_type.model) if content_type else None
            fields[field.name] = _ref_for(target, value, instance_id, origins) if target else value
        elif model._meta.label_lower == 'document_management.document' and field.name == 'source_id' and value:
            target_label = _document_source_model(obj.source_type)
            target = apps.get_model(target_label) if target_label else None
            fields[field.name] = _ref_for(target, value, instance_id, origins) if target else value
        elif field.is_relation and field.remote_field:
            fields[field.name] = _ref_for(field.remote_field.model, value, instance_id, origins)
        elif isinstance(field, FileField):
            fields[field.name] = getattr(obj, field.name).name
            if fields[field.name]:
                try:
                    _, content = _read_attachment(field, fields[field.name])
                    file_hashes[field.name] = _sha256(content)
                except (ConsolidationError, OSError):
                    file_hashes[field.name] = 'unavailable'
        elif model._meta.label_lower == 'document_management.document' and field.name == 'file_path' and value:
            fields[field.name] = _safe_storage_name(value)
            try:
                _, content = _read_attachment(None, value, storage=private_storage)
                file_hashes[field.name] = _sha256(content)
            except (ConsolidationError, OSError):
                file_hashes[field.name] = 'unavailable'
        else:
            fields[field.name] = value
    m2m = {
        field.name: [_ref_for(field.remote_field.model, pk, instance_id, origins)
                     for pk in getattr(obj, field.name).values_list('pk', flat=True)]
        for field in model._meta.many_to_many
    }
    return json.loads(_json_bytes({'fields': fields, 'm2m': m2m, 'file_hashes': file_hashes}))


def preview_package(path):
    try:
        return _preview_package(path)
    except ConsolidationError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, LookupError, OverflowError,
            ValidationError) as exc:
        raise ConsolidationError(f'Package contains invalid record data: {exc}') from exc


def _preview_package(path):
    parsed = _read_package(path)
    manifest, payload = parsed['manifest'], parsed['payload']
    source_id = parsed['source_instance_id']
    identity = InstanceIdentity.current()
    preview = {
        'source_instance': manifest['source_instance_name'],
        'source_instance_id': str(source_id),
        'export_id': str(parsed['export_id']),
        'exported_at': manifest['exported_at'],
        'package_version': manifest['format_version'],
        'app_version': manifest.get('app_version', 'unknown'),
        'total_records': len(payload['records']),
        'attachment_count': len(payload['files']),
        'validation_errors': [],
        'warnings': [],
        'entity_counts': manifest.get('entity_counts', {}),
        'new_records': 0,
        'existing_records': 0,
        'changed_source_records': 0,
        'source_conflicts': [],
        'unique_conflicts': [],
        'potential_duplicates': [],
        'blocked': False,
    }
    if source_id == identity.instance_id:
        preview['validation_errors'].append('This package was exported by this installation; self-import is blocked.')
    if MergePackage.objects.filter(source_instance_id=source_id, export_id=parsed['export_id'],
                                   status=MergePackage.MERGED).exists():
        preview['validation_errors'].append('This export package has already been merged.')

    origin_rows = _origin_rows()
    for record in payload['records']:
        ident = record['identity']
        key = (ident['model'], ident['instance_id'], ident['record_id'])
        existing = origin_rows.get(key)
        if existing:
            if existing.source_hash == record['source_hash']:
                preview['existing_records'] += 1
            else:
                preview['changed_source_records'] += 1
                model = _installed_models()[ident['model']]
                baseline = existing.source_snapshot or {}
                old_fields = baseline.get('fields', {})
                old_m2m = baseline.get('m2m', {})
                old_file_hashes = baseline.get('file_hashes', {})
                source_changed = sorted(
                    [name for name in set(old_fields) | set(record['fields'])
                     if old_fields.get(name) != record['fields'].get(name)]
                    + [f'{name} (relations)' for name in set(old_m2m) | set(record['m2m'])
                       if old_m2m.get(name) != record['m2m'].get(name)]
                    + [f'{name} (attachment)' for name in set(old_file_hashes) | set(record['file_hashes'])
                       if old_file_hashes.get(name) != record['file_hashes'].get(name)]
                )
                destination_obj = model._base_manager.filter(pk=existing.destination_pk).first()
                destination_changed = []
                destination_modified = None
                destination_hash = ''
                if destination_obj is not None:
                    current = _live_snapshot(model, destination_obj, identity.instance_id, _origin_index())
                    destination_hash = _sha256(_json_bytes({
                        'identity': ident, 'fields': current['fields'], 'm2m': current['m2m'],
                        'file_hashes': current['file_hashes'],
                    }))
                    destination_changed = sorted(
                        [name for name in set(old_fields) | set(current['fields'])
                         if old_fields.get(name) != current['fields'].get(name)]
                        + [f'{name} (relations)' for name in set(old_m2m) | set(current['m2m'])
                           if old_m2m.get(name) != current['m2m'].get(name)]
                        + [f'{name} (attachment)' for name in set(old_file_hashes) | set(current['file_hashes'])
                           if old_file_hashes.get(name) != current['file_hashes'].get(name)]
                    )
                    for modified_field in ('updated_at', 'modified_at', 'created_at'):
                        if hasattr(destination_obj, modified_field):
                            destination_modified = getattr(destination_obj, modified_field)
                            break
                preview['source_conflicts'].append({
                    'model': model._meta.label,
                    'record_id': ident['record_id'],
                    'destination_pk': existing.destination_pk,
                    'original_hash': existing.source_hash,
                    'source_hash': record['source_hash'],
                    'destination_hash': destination_hash,
                    'source_changed_fields': source_changed[:50],
                    'destination_changed_fields': destination_changed[:50],
                    'source_modified': record['fields'].get('updated_at') or record['fields'].get('modified_at')
                    or record['fields'].get('created_at', ''),
                    'destination_modified': destination_modified,
                })
            continue
        preview['new_records'] += 1
        model = _installed_models()[ident['model']]
        for conflict in _record_conflicts(model, record):
            preview['unique_conflicts'].append({
                'model': model._meta.label, 'record_id': ident['record_id'], **conflict,
            })
        preview['potential_duplicates'].extend(_potential_duplicates(model, record))
    if preview['changed_source_records']:
        preview['warnings'].append(
            'Previously imported source records have changed. The original source package is retained; '
            'the master copy will not be overwritten.'
        )
    if preview['potential_duplicates']:
        preview['warnings'].append('Possible duplicates were detected by exact email, phone, code, or reference matches.')
    if preview['unique_conflicts']:
        preview['validation_errors'].append(
            'Unique-key conflicts require manual resolution before any part of this package can be merged.'
        )
    preview['will_skip'] = preview['existing_records'] + preview['changed_source_records']
    if preview['validation_errors']:
        preview['blocked'] = True
    return parsed, preview


def create_import_preview(upload):
    if not upload.name or not upload.name.lower().endswith('.zip'):
        raise ConsolidationError('Only .zip export packages are accepted.')
    if upload.size > MAX_UPLOAD_BYTES:
        raise ConsolidationError(f'Package exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit.')
    incoming = backup_services.backup_dir() / 'imports'
    incoming.mkdir(parents=True, exist_ok=True)
    package_id = uuid.uuid4()
    path = incoming / f'{package_id}.zip'
    try:
        total = 0
        with open(path.with_suffix('.partial'), 'wb') as destination:
            for chunk in upload.chunks():
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise ConsolidationError('Package exceeds the upload limit.')
                destination.write(chunk)
        os.replace(path.with_suffix('.partial'), path)
        parsed, preview = preview_package(path)
        clean_name = get_valid_filename(Path(upload.name).name)[:255] or 'export.zip'
        return MergePackage.objects.create(
            id=package_id, source_instance_id=parsed['source_instance_id'], export_id=parsed['export_id'],
            source_instance_name=parsed['manifest']['source_instance_name'], uploaded_name=clean_name,
            path=str(path), manifest=parsed['manifest'], preview=preview,
        )
    except IntegrityError as exc:
        path.unlink(missing_ok=True)
        raise ConsolidationError('This source export is already registered for preview or merge.') from exc
    except Exception:
        path.unlink(missing_ok=True)
        path.with_suffix('.partial').unlink(missing_ok=True)
        raise


def _destination_pk(ref, origin_rows, new_rows):
    if ref is None:
        return None
    if 'natural' in ref:
        return ContentType.objects.get_by_natural_key(*ref['natural']).pk
    key = _identity_key(ref['ref'])
    if key in new_rows:
        return new_rows[key]
    row = origin_rows.get(key)
    if row:
        return row.destination_pk
    return None


def _file_index(payload):
    return {
        (item['model'], *_identity_key(item['identity'])[1:], item['field']): item
        for item in payload['files']
    }


def _store_attachments(parsed, saved_files, import_keys):
    payload = parsed['payload']
    file_index = _file_index(payload)
    path_map = {}
    created = []
    models_by_label = _installed_models()
    try:
        for (label, instance_id, source_pk, field_name), item in file_index.items():
            if (label, instance_id, source_pk) not in import_keys:
                continue
            model = models_by_label[label]
            field = model._meta.get_field(field_name)
            storage = private_storage if label == 'document_management.document' and field_name == 'file_path' else field.storage
            expected_kind = _storage_kind(storage)
            if expected_kind != item['storage']:
                raise ConsolidationError(f'Attachment storage type does not match {label}.{field_name}.')
            content = parsed['content_by_name'][item['archive_path']]
            source_name = _safe_storage_name(item['storage_name'])
            if storage.exists(source_name):
                try:
                    with storage.open(source_name, 'rb') as existing_file:
                        existing_sha = _sha256(existing_file.read(MAX_FILE_BYTES + 1))
                except OSError as exc:
                    raise ConsolidationError(f'Could not validate existing attachment {source_name}.') from exc
                if existing_sha == item['sha256']:
                    stored_name = source_name
                else:
                    stored_name = storage.save(source_name, ContentFile(content))
                    created.append((storage, stored_name))
            else:
                stored_name = storage.save(source_name, ContentFile(content))
                created.append((storage, stored_name))
            saved_files[(label, instance_id, source_pk, field_name)] = stored_name
            old_key = (item['storage'], source_name)
            prior = path_map.get(old_key)
            if prior is not None and prior != stored_name:
                raise ConsolidationError('Attachment filenames collide with different content; references are ambiguous.')
            path_map[old_key] = stored_name
        return file_index, path_map, created
    except Exception:
        for storage, name in created:
            storage.delete(name)
        raise


def _import_record(record, model, origin_rows, new_rows, file_index, saved_files, path_map):
    identity = record['identity']
    key = _identity_key(identity)
    values = {}
    unresolved_nullable = []
    for name, value in record['fields'].items():
        field = model._meta.get_field(name)
        if field.is_relation and field.remote_field:
            target_pk = _destination_pk(value, origin_rows, new_rows)
            if value is not None and target_pk is None:
                if not field.null:
                    return None, None
                unresolved_nullable.append((field, value))
                target_pk = None
            values[field.attname] = field.target_field.to_python(target_pk) if target_pk is not None else None
        elif isinstance(field, FileField):
            file_key = (identity['model'], identity['instance_id'], identity['record_id'], field.name)
            values[field.attname] = saved_files.get(file_key, '')
        elif (model._meta.label_lower == 'document_management.document' and name in {'object_id', 'source_id'}
              and isinstance(value, dict)):
            values[field.attname] = ''
        elif name == 'file_path' and isinstance(value, str) and value:
            values[field.attname] = path_map.get(('private', value), value)
        else:
            values[field.attname] = field.to_python(value)

    instance = model(**values)
    if not model._meta.pk.auto_created:
        instance.pk = model._meta.pk.to_python(identity['record_id'])
    instance.save_base(raw=True, force_insert=True, using='default')
    destination_pk = str(instance.pk)
    RecordOrigin.objects.create(
        origin_instance_id=identity['instance_id'], model_label=identity['model'],
        origin_record_id=identity['record_id'], destination_pk=destination_pk,
        source_hash=record['source_hash'],
        source_snapshot={
            'fields': record['fields'], 'm2m': record['m2m'], 'file_hashes': record['file_hashes'],
        },
    )
    new_rows[key] = destination_pk
    return instance, unresolved_nullable


def merge_package(package, keep_destination=False):
    if package.status == MergePackage.MERGED:
        raise ConsolidationError('This package was already merged.')
    parsed = _read_package(package.path)
    parsed['path'] = package.path
    parsed, preview = preview_package(package.path)
    if preview['blocked']:
        package.preview = preview
        package.save(update_fields=['preview'])
        raise ConsolidationError('Package validation or unique-key conflicts block the merge.')
    if preview['changed_source_records'] and not keep_destination:
        raise ConsolidationError('Changed source records require an explicit keep-destination decision.')

    payload = parsed['payload']
    pre_merge = backup_services.create_backup(
        kind=BackupRecord.PRE_MERGE,
        note=f'Automatic verified backup before merging {package.source_instance_name} export {package.export_id}',
        protected=True,
    )
    pre_merge_ok, pre_merge_problems = backup_services.verify(pre_merge)
    if not pre_merge_ok:
        raise ConsolidationError('Pre-merge backup failed verification; nothing was imported: '
                                 + '; '.join(pre_merge_problems))
    models_by_label = _installed_models()
    records = payload['records']
    origin_rows = _origin_rows()
    pending = [
        record for record in records
        if (record['identity']['model'], record['identity']['instance_id'], record['identity']['record_id'])
        not in origin_rows
    ]
    saved_files = {}
    import_keys = {
        (record['identity']['model'], record['identity']['instance_id'], record['identity']['record_id'])
        for record in pending
    }
    try:
        file_index, path_map, created_files = _store_attachments(parsed, saved_files, import_keys)
    except Exception:
        raise
    created_count = 0
    deferred_relations = []
    deferred_m2m = []
    try:
        with transaction.atomic():
            progress = True
            while pending and progress:
                progress = False
                remaining = []
                for record in pending:
                    ident = record['identity']
                    key = _identity_key(ident)
                    new_rows = {
                        (row.model_label, str(row.origin_instance_id), row.origin_record_id): row.destination_pk
                        for row in RecordOrigin.objects.all()
                    }
                    instance, unresolved = _import_record(
                        record, models_by_label[ident['model']], origin_rows, new_rows, file_index, saved_files, path_map,
                    )
                    if instance is None:
                        remaining.append(record)
                        continue
                    created_count += 1
                    for field, ref in unresolved:
                        deferred_relations.append((instance._meta.label_lower, str(instance.pk), field, ref))
                    deferred_m2m.append((instance, record['m2m']))
                    progress = True
                pending = remaining
                origin_rows = _origin_rows()
            if pending:
                labels = sorted({item['identity']['model'] for item in pending})
                raise ConsolidationError(
                    'Required foreign-key dependency cycle or missing reference prevents import: ' + ', '.join(labels)
                )

            origin_rows = _origin_rows()
            new_rows = {
                (row.model_label, str(row.origin_instance_id), row.origin_record_id): row.destination_pk
                for row in RecordOrigin.objects.all()
            }
            for label, pk, field, ref in deferred_relations:
                destination_pk = _destination_pk(ref, origin_rows, new_rows)
                if destination_pk is None:
                    raise ConsolidationError(f'Could not resolve optional relation {label}.{field.name}.')
                model = models_by_label[label]
                model._base_manager.filter(pk=pk).update(**{field.attname: field.target_field.to_python(destination_pk)})

            for instance, relations in deferred_m2m:
                for name, refs in relations.items():
                    target_ids = [_destination_pk(ref, origin_rows, new_rows) for ref in refs]
                    if any(value is None for value in target_ids):
                        raise ConsolidationError(f'Could not resolve a many-to-many relation on {instance._meta.label}.{name}.')
                    getattr(instance, name).set(target_ids)

            # GenericForeignKey targets and document source IDs are stored outside Django FK fields.
            for record in records:
                ident = record['identity']
                if ident['model'] != 'document_management.document':
                    continue
                fields = record['fields']
                updates = {}
                for field_name in ('object_id', 'source_id'):
                    reference = fields.get(field_name)
                    if not isinstance(reference, dict):
                        continue
                    mapped = _destination_pk(reference, origin_rows, new_rows)
                    if mapped is None:
                        raise ConsolidationError(f'Could not resolve generic document {field_name} reference.')
                    updates[field_name] = str(mapped)
                document_pk = new_rows[(ident['model'], ident['instance_id'], ident['record_id'])]
                if updates:
                    models_by_label[ident['model']]._base_manager.filter(pk=document_pk).update(**updates)

            package.status = MergePackage.MERGED
            package.imported_at = timezone.now()
            package.preview = {
                **preview, 'merged_records': created_count, 'skipped_existing_records': preview['existing_records']
                + preview['changed_source_records'],
            }
            package.save(update_fields=['status', 'imported_at', 'preview'])
            identity = InstanceIdentity.current()
            identity.last_import_at = package.imported_at
            identity.last_import_id = package.export_id
            identity.save(update_fields=['last_import_at', 'last_import_id'])
            from audit.recorder import log_manual
            log_manual('backup', 'MergePackage', package.pk, package.uploaded_name, 'import',
                       f'Additive import from {package.source_instance_name}',
                       {'records': [None, created_count], 'export_id': [None, str(package.export_id)]})
    except IntegrityError as exc:
        for storage, name in created_files:
            storage.delete(name)
        raise ConsolidationError(f'Destination database constraint rejected the merge; no records were imported: {exc}') from exc
    except Exception:
        for storage, name in created_files:
            storage.delete(name)
        raise
    return package
