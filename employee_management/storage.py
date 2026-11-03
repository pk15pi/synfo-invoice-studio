"""Private storage for employee documents and photos.

Files live in settings.EMPLOYEE_PRIVATE_ROOT (default <project>/private_media), which is
outside STATIC_ROOT and MEDIA_ROOT and is never mapped to a URL. They are only
reachable through the controlled views in views_docs.py."""
import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateStorage(FileSystemStorage):
    """Location is read from settings on every access (so tests can override it)."""

    def __init__(self):
        super().__init__(base_url=None)

    @property
    def base_location(self):
        return str(settings.EMPLOYEE_PRIVATE_ROOT)

    @property
    def location(self):
        import os
        return os.path.abspath(self.base_location)


private_storage = PrivateStorage()


def get_private_storage():
    return private_storage


def document_upload_to(instance, filename):
    # Never trust the uploaded name: server-side random name + validated extension.
    ext = getattr(instance, '_validated_ext', None) or 'bin'
    return f'employees/{instance.employee_id}/documents/{uuid.uuid4().hex}.{ext}'


def photo_upload_to(instance, filename):
    ext = getattr(instance, '_validated_ext', None) or 'bin'
    return f'employees/{instance.pk or "new"}/photo/{uuid.uuid4().hex}.{ext}'


def signature_upload_to(instance, filename):
    ext = getattr(instance, '_validated_ext', None) or 'png'
    return f'settings/signature/{uuid.uuid4().hex}.{ext}'
