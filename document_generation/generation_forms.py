import json
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import F
from django.utils import timezone

from asset_management.models import Asset
from employee_management.models import Employee
from expense_management.models import Vendor
from invoice_app.models import Client

from .field_resolver import resolve_source_value
from .models import (
    CompanyDocumentProfile,
    CompanyDocumentSignatory,
    DocumentCategory,
    DocumentFieldDefinition,
    DocumentTemplateVersion,
    DocumentType,
)
from .models import GeneratedDocument


ENTITY_MODELS = {
    'employee': (Employee, ('department', 'reporting_manager')),
    'client': (Client, ()),
    'vendor': (Vendor, ()),
    'asset': (Asset, ('category', 'current_employee', 'current_location')),
}


def _approved_document_types():
    return DocumentType.objects.filter(
        is_active=True,
        templates__is_active=True,
        templates__active_version__status=DocumentTemplateVersion.APPROVED,
    ).distinct()


def _condition_is_met(condition, values):
    if not condition:
        return True
    key = condition.get('field')
    if not key:
        return False
    value = values.get(key, '')
    operator = condition.get('operator', 'truthy')
    if operator == 'equals':
        expected = condition.get('value', '')
        if isinstance(value, bool) or isinstance(expected, bool):
            return _as_boolean(value) == _as_boolean(expected)
        return str(value) == str(expected)
    if operator == 'not_equals':
        expected = condition.get('value', '')
        if isinstance(value, bool) or isinstance(expected, bool):
            return _as_boolean(value) != _as_boolean(expected)
        return str(value) != str(expected)
    if operator == 'present':
        return value not in (None, '', False)
    if operator == 'empty':
        return value in (None, '', False)
    return bool(value)


def _as_boolean(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


class UniversalDocumentForm(forms.Form):
    category = forms.ModelChoiceField(
        queryset=DocumentCategory.objects.none(), required=True, empty_label='Select a category',
    )
    document_type = forms.ModelChoiceField(
        queryset=DocumentType.objects.none(), required=True, empty_label='Select a document type',
    )
    template_version = forms.ModelChoiceField(
        queryset=DocumentTemplateVersion.objects.none(), required=True,
        empty_label='Select an approved template',
    )
    document_date = forms.DateField(
        initial=timezone.localdate, widget=forms.DateInput(attrs={'type': 'date'}),
    )
    effective_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={'type': 'date'}),
    )
    related_record = forms.ChoiceField(required=False, choices=(('', 'Enter party details manually'),))
    related_asset = forms.ChoiceField(required=False, choices=(('', 'Select an asset (optional)'),))
    authorised_signatory = forms.ModelChoiceField(
        queryset=CompanyDocumentSignatory.objects.none(), required=False,
        empty_label='Select authorised signatory',
    )
    revision_of = forms.IntegerField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, document_type=None, related_record=None, related_asset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.document_type = document_type
        self.field_definitions = []
        self.selected_record_module = ''
        self.selected_record = None
        self.selected_records = {}

        self.fields['category'].queryset = DocumentCategory.objects.filter(is_active=True)
        category_id = self.data.get('category') or self.initial.get('category')
        types = _approved_document_types()
        if category_id:
            types = types.filter(categories__id=category_id)
        self.fields['document_type'].queryset = types
        if document_type:
            self.fields['document_type'].initial = document_type.pk
            self.fields['category'].initial = document_type.categories.filter(is_active=True).values_list(
                'id', flat=True,
            ).first()
            self.fields['template_version'].queryset = DocumentTemplateVersion.objects.filter(
                template__document_type=document_type,
                template__is_active=True,
                status=DocumentTemplateVersion.APPROVED,
                template__active_version_id=F('pk'),
            ).select_related('template').order_by('template__name')
            self.field_definitions = list(document_type.field_definitions.filter(is_active=True))
            self._set_related_records(
                document_type,
                related_record,
                related_asset or self.data.get('related_asset') or self.initial.get('related_asset'),
            )
        self.fields['authorised_signatory'].queryset = CompanyDocumentSignatory.objects.filter(
            is_active=True, is_authorized=True,
        )
        self._add_dynamic_fields()

    def _allowed_modules(self, document_type):
        categories = set(document_type.categories.filter(is_active=True).values_list('code', flat=True))
        modules = set(document_type.field_definitions.filter(
            is_active=True, is_auto_populated=True,
        ).values_list('source_module', flat=True))
        if 'employee' in categories:
            modules.add('employee')
        if 'client' in categories:
            modules.add('client')
        if 'vendor' in categories or 'vendor' in document_type.code:
            modules.add('vendor')
        if 'asset' in document_type.code or 'asset' in document_type.name.lower():
            modules.add('asset')
        return modules & set(ENTITY_MODELS)

    def _set_related_records(self, document_type, selected, selected_asset):
        choices = [('', 'Enter party details manually')]
        allowed = self._allowed_modules(document_type)
        for module in ('employee', 'client', 'vendor', 'asset'):
            if module not in allowed or module == 'asset':
                continue
            model, related = ENTITY_MODELS[module]
            queryset = model.objects.all()
            if module == 'employee':
                queryset = queryset.filter(status__in=Employee.EMPLOYED)
            elif module == 'client':
                queryset = queryset.filter(active=True)
            elif module == 'vendor':
                queryset = queryset.filter(status=Vendor.ACTIVE)
            elif module == 'asset':
                queryset = queryset.exclude(current_status='disposed')
            queryset = queryset.select_related(*related).order_by()[:500]
            choices.extend(
                (f'{module}:{obj.pk}', f'{module.title()}: {obj}')
                for obj in queryset
            )
        self.fields['related_record'].choices = choices
        selected = selected or self.data.get('related_record') or self.initial.get('related_record')
        if selected:
            module, separator, object_id = str(selected).partition(':')
            model_data = ENTITY_MODELS.get(module)
            if separator and model_data and module in allowed:
                model, related = model_data
                try:
                    self.selected_record = model.objects.select_related(*related).get(pk=object_id)
                except (model.DoesNotExist, ValueError):
                    self.selected_record = None
                else:
                    self.selected_record_module = module
                    self.selected_records[module] = self.selected_record
                    self.fields['related_record'].initial = selected
        asset_choices = [('', 'Select an asset (optional)')]
        if 'asset' in allowed:
            asset_model, asset_related = ENTITY_MODELS['asset']
            assets = asset_model.objects.exclude(current_status='disposed').select_related(
                *asset_related,
            ).order_by('asset_code')[:500]
            asset_choices.extend(
                (str(asset.pk), f'{asset.asset_code}: {asset.name}')
                for asset in assets
            )
        self.fields['related_asset'].choices = asset_choices
        if selected_asset and 'asset' in allowed:
            try:
                asset = Asset.objects.select_related(*ENTITY_MODELS['asset'][1]).get(pk=selected_asset)
            except (Asset.DoesNotExist, ValueError):
                asset = None
            if asset:
                self.selected_records['asset'] = asset
                self.fields['related_asset'].initial = str(asset.pk)

    def _add_dynamic_fields(self):
        values = {
            key: self.data.get(key) for key in self.data
        } if self.is_bound else self.initial
        current_values = dict(values)
        for definition in self.field_definitions:
            visible = _condition_is_met(definition.visibility_condition, current_values)
            required = definition.is_required and visible
            rules = definition.validation_rules
            common = {
                'required': required,
                'label': definition.label,
                'help_text': definition.help_text,
            }
            if definition.value_type == 'date':
                field = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), **common)
            elif definition.value_type in {'decimal', 'currency'}:
                field = forms.DecimalField(
                    max_digits=16, decimal_places=2,
                    min_value=Decimal(str(rules['min'])) if 'min' in rules else None,
                    max_value=Decimal(str(rules['max'])) if 'max' in rules else None,
                    **common,
                )
            elif definition.value_type == 'integer':
                field = forms.IntegerField(
                    min_value=int(rules['min']) if 'min' in rules else None,
                    max_value=int(rules['max']) if 'max' in rules else None,
                    **common,
                )
            elif definition.value_type == 'boolean':
                field = forms.BooleanField(required=False, label=definition.label, help_text=definition.help_text)
            elif definition.value_type == 'email':
                field = forms.EmailField(**common)
            elif definition.value_type == 'multiline':
                field = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), **common)
            elif definition.value_type == 'select':
                field = forms.ChoiceField(
                    choices=[('', 'Select an option')] + [(item, item) for item in definition.choices],
                    **common,
                )
            else:
                field = forms.CharField(**common)
            field.widget.attrs.update({
                'data-field-key': definition.field_key,
                'data-condition': json.dumps(definition.visibility_condition),
                'placeholder': definition.placeholder,
            })
            field.condition = json.dumps(definition.visibility_condition)
            if rules.get('max_length') and isinstance(field, forms.CharField):
                field.max_length = int(rules['max_length'])
            if rules.get('min_length') and isinstance(field, forms.CharField):
                field.min_length = int(rules['min_length'])
            self.fields[definition.field_key] = field

            if self.is_bound:
                continue
            value = definition.default_value
            if definition.is_auto_populated and definition.source_module == 'company':
                value = resolve_source_value('company', definition.source_field, CompanyDocumentProfile.current())
            elif (definition.is_auto_populated
                    and definition.source_module in self.selected_records):
                value = resolve_source_value(
                    definition.source_module, definition.source_field,
                    self.selected_records[definition.source_module],
                )
            if value not in (None, ''):
                self.initial[definition.field_key] = value

    def clean(self):
        cleaned = super().clean()
        document_type = cleaned.get('document_type')
        template_version = cleaned.get('template_version')
        category = cleaned.get('category')
        if document_type and category and not document_type.categories.filter(pk=category.pk).exists():
            self.add_error('document_type', 'Select a type in the selected category.')
        if template_version and document_type and template_version.template.document_type_id != document_type.pk:
            self.add_error('template_version', 'Select a template for the chosen document type.')
        revision_id = cleaned.get('revision_of')
        if revision_id:
            previous = GeneratedDocument.objects.filter(pk=revision_id).first()
            if not previous or previous.status != 'draft':
                self.add_error('revision_of', 'Only an existing draft can be revised.')
            elif document_type and previous.document_type_id != document_type.pk:
                self.add_error('revision_of', 'A revised document must keep its document type.')
        current_values = dict(cleaned)
        for definition in self.field_definitions:
            if definition.field_key in cleaned and not _condition_is_met(
                definition.visibility_condition, current_values,
            ):
                cleaned[definition.field_key] = ''
        return cleaned

    def field_values(self):
        return {
            definition.field_key: self.cleaned_data.get(definition.field_key, '')
            for definition in self.field_definitions
            if _condition_is_met(definition.visibility_condition, self.cleaned_data)
        }

    def party_snapshot(self):
        from .field_resolver import source_snapshot
        records = {
            module: source_snapshot(module, record)
            for module, record in self.selected_records.items()
        }
        manual_values = {
            definition.field_key: self.cleaned_data.get(definition.field_key, '')
            for definition in self.field_definitions
            if definition.source_module == 'manual'
        }
        return {'records': records, 'manual_fields': manual_values}
