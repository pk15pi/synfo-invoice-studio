"""Simple, generated Django-admin registrations for every project model that has no hand-written admin.

* list_display / list_filter / search_fields are picked from the model's own fields.
* Aadhaar, PAN, passport, UAN, ESIC and bank-account numbers are never shown in full:
  they are removed from the edit form and shown masked (last 4 only).
* File / image fields are not editable or linked here (private documents stay behind the app's own
  views); the stored file name is shown instead.
* Append-only / ledger history models are registered read-only.
"""
import re

from django.apps import apps
from django.contrib import admin
from django.db import models

SENSITIVE = re.compile(r'(aadha?ar|(^|_)pan($|_number)|passport|(^|_)uan|esic|account_number|bank_account_number'
                       r'|^bank_account$)', re.I)
TEXTY = (models.CharField, models.EmailField, models.SlugField)


def mask(value):
    v = '' if value is None else str(value).strip()
    if not v:
        return '-'
    return ('\u2022' * max(len(v) - 4, 2)) + v[-4:] if len(v) > 4 else '\u2022' * len(v)


def is_sensitive(f):
    return isinstance(f, (models.CharField, models.TextField)) and bool(SENSITIVE.search(f.name)) \
        and not f.name.endswith('_masked') and 'masked' not in f.name


def _masked_reader(name, label):
    def reader(self, obj):
        return mask(getattr(obj, name, ''))
    reader.short_description = f'{label} (masked)'
    reader.__name__ = f'{name}_masked_display'
    return reader


def _file_reader(name, label):
    def reader(self, obj):
        f = getattr(obj, name, None)
        return (f.name.rsplit('/', 1)[-1] if f else '-')
    reader.short_description = f'{label} (file name)'
    reader.__name__ = f'{name}_file_display'
    return reader


def build_admin(model, read_only=False):
    fields = [f for f in model._meta.concrete_fields if not f.primary_key]
    attrs, readonly, exclude, display, filters, search, fks = {}, [], [], ['__str__'], [], [], []
    for f in fields:
        label = str(f.verbose_name).capitalize()
        if is_sensitive(f):
            fn = _masked_reader(f.name, label)
            attrs[fn.__name__] = fn
            readonly.append(fn.__name__)
            exclude.append(f.name)
            if len(display) < 7:
                display.append(fn.__name__)
            continue
        if isinstance(f, models.FileField):
            fn = _file_reader(f.name, label)
            attrs[fn.__name__] = fn
            readonly.append(fn.__name__)
            exclude.append(f.name)
            continue
        if isinstance(f, (models.ForeignKey, models.OneToOneField)):
            fks.append(f.name)
        if len(display) < 7 and not isinstance(f, (models.TextField, models.JSONField, models.BinaryField)) \
                and not (isinstance(f, models.CharField) and f.max_length and f.max_length > 200):
            display.append(f.name)
        if (isinstance(f, models.BooleanField) or (f.choices and not isinstance(f, models.ForeignKey))) and len(filters) < 5:
            filters.append(f.name)
        elif isinstance(f, (models.DateField, models.DateTimeField)) and len(filters) < 5 and not f.auto_now:
            filters.append(f.name)
        if isinstance(f, TEXTY) and not f.choices and len(search) < 6:
            search.append(f.name)
        if getattr(f, 'auto_now', False) or getattr(f, 'auto_now_add', False):
            readonly.append(f.name)
    attrs.update(list_display=tuple(display), list_filter=tuple(filters), search_fields=tuple(search),
                 readonly_fields=tuple(readonly), exclude=tuple(exclude), raw_id_fields=tuple(fks),
                 list_select_related=tuple(f for f in display if f in fks), list_per_page=30, show_full_result_count=False)
    if read_only:
        attrs.update(has_add_permission=lambda self, request: False,
                     has_change_permission=lambda self, request, obj=None: False,
                     has_delete_permission=lambda self, request, obj=None: False)
    return type(f'{model.__name__}AutoAdmin', (admin.ModelAdmin,), attrs)


def register_all(app_label, read_only=()):
    """Register every concrete model of ``app_label`` that is not registered yet."""
    done = []
    for model in apps.get_app_config(app_label).get_models():
        if model._meta.abstract or admin.site.is_registered(model):
            continue
        admin.site.register(model, build_admin(model, read_only=model.__name__ in read_only))
        done.append(model.__name__)
    return done