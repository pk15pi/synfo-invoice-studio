import json

from django import forms
from django.db.models import Count, F, Q
from django.utils import timezone

from employee_management.models import Employee

from .models import (
    Asset, AssetAcknowledgement, AssetAssignment, AssetCategory, AssetLease, AssetLocation, AssetMaintenance,
)


DATE = forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')


class StyledForm:
    """Apply the shared field styling used across the application."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, (forms.CheckboxInput, forms.ClearableFileInput)):
                field.widget.attrs.setdefault('class', 'field')


def _comma_list(value):
    return [part.strip() for part in (value or '').split(',') if part.strip()]


def _assignable_assets():
    """Assets with at least one unit left to issue."""
    return (
        Asset.objects.filter(current_status=Asset.AVAILABLE)
        .annotate(
            active_unit_count=Count(
                'assignments', filter=Q(assignments__status=AssetAssignment.ACTIVE),
            ),
        )
        .filter(quantity__gt=F('active_unit_count'))
    )


class AssetForm(StyledForm, forms.ModelForm):
    class Meta:
        model = Asset
        fields = [
            'name', 'category', 'brand', 'model_number', 'serial_number', 'quantity', 'ownership_type',
            'usage_type', 'condition', 'current_location', 'purchase_date', 'purchase_price', 'vendor_name',
            'invoice_number', 'warranty_expiry', 'description',
        ]
        widgets = {
            'quantity': forms.NumberInput(attrs={'min': 1}),
            'purchase_date': DATE,
            'warranty_expiry': DATE,
            'description': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        categories = AssetCategory.objects.filter(is_active=True)
        if self.instance.pk and self.instance.category_id:
            categories = categories | AssetCategory.objects.filter(pk=self.instance.category_id)
        self.fields['category'].queryset = categories
        self.fields['current_location'].queryset = AssetLocation.objects.filter(is_active=True)
        self.fields['current_location'].empty_label = 'No specific location'
        self.fields['current_location'].help_text = (
            'Physical place where the asset is kept. Add or rename places in Asset Management \u2192 Locations.'
        )
        self.fields['category'].help_text = (
            'Select the category. The asset ID is generated from its code when the asset is saved.'
        )
        self.fields['quantity'].required = False
        self.fields['quantity'].label = 'Number of items'
        self.fields['quantity'].help_text = (
            'For items without a serial number, enter how many units this record covers. '
            'Items with a serial number are always tracked as one item.'
        )
        if not self.is_bound and not self.instance.pk:
            self.fields['quantity'].initial = 1

    def clean(self):
        cleaned = super().clean()
        if (cleaned.get('serial_number') or '').strip():
            cleaned['quantity'] = 1
        elif not cleaned.get('quantity'):
            cleaned['quantity'] = 1
        return cleaned


class AssetLocationForm(StyledForm, forms.ModelForm):
    class Meta:
        model = AssetLocation
        fields = ['name', 'location_type', 'parent', 'address', 'is_active']
        widgets = {'address': forms.Textarea(attrs={'rows': 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        parents = AssetLocation.objects.filter(is_active=True)
        if self.instance.pk:
            parents = parents.exclude(pk=self.instance.pk)
        self.fields['parent'].queryset = parents
        self.fields['parent'].help_text = 'Optional. Nest a floor or room under its office or building.'


class AssetReceiptForm(StyledForm, forms.Form):
    file = forms.FileField(
        label='Signed receipt', help_text='PDF, JPG or PNG up to 10 MB, signed by the employee.',
    )
    note = forms.CharField(
        label='Note', required=False, max_length=240,
        help_text='Optional, for example "Signed copy received from Asha on 12-11-2026".',
    )


class AssignmentForm(StyledForm, forms.ModelForm):
    accessories = forms.CharField(
        label='Accessories issued', required=False,
        widget=forms.TextInput(attrs={'autocomplete': 'off'}),
        help_text=(
            'Type to filter registered assets and select one, or type free-text accessories separated by commas. '
            'Selected assets are also assigned to this employee with the same dates, condition and notes.'
        ),
    )
    accessory_assets = forms.ModelMultipleChoiceField(
        required=False,
        queryset=Asset.objects.none(),
        widget=forms.MultipleHiddenInput(),
    )
    receipt = forms.FileField(
        label='Signed receipt (optional)', required=False,
        help_text='PDF, JPG or PNG up to 10 MB. Stored privately and indexed in Documents.',
    )
    acknowledgement_type = forms.ChoiceField(
        label='Handover acknowledgement', required=False,
        choices=[('', 'No acknowledgement recorded')] + AssetAcknowledgement.TYPES,
        help_text='Record the signed handover or the employee confirmation captured in this app.',
    )
    acknowledgement_name = forms.CharField(
        label='Acknowledged by', required=False, max_length=180,
        help_text='Defaults to the employee name when left blank.',
    )
    confirmation_text = forms.CharField(
        label='Employee confirmation', required=False,
        widget=forms.Textarea(attrs={'rows': 2}),
        help_text='Required for a digital acknowledgement.',
    )

    class Meta:
        model = AssetAssignment
        fields = ['asset', 'employee', 'issue_date', 'expected_return_date', 'issue_condition', 'issue_notes']
        widgets = {
            'issue_date': DATE,
            'expected_return_date': DATE,
            'issue_notes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        available = _assignable_assets().select_related('category')
        self.fields['asset'].queryset = available
        self.fields['asset'].help_text = 'Only assets with items available can be assigned.'
        self.fields['accessory_assets'].queryset = available
        options = [
            {
                'id': str(asset.pk), 'code': asset.asset_code, 'name': asset.name,
                'available': asset.available_quantity,
            }
            for asset in available.order_by('asset_code')
        ]
        self.fields['accessories'].widget.attrs.update({
            'data-accessory-picker': '1',
            'data-accessory-options': json.dumps(options, separators=(',', ':')),
        })
        self.has_accessory_picker = True
        self.fields['employee'].queryset = Employee.objects.filter(status__in=Employee.EMPLOYED)
        if not self.is_bound and not self.initial.get('issue_date'):
            self.initial['issue_date'] = timezone.localdate()

    def clean(self):
        cleaned = super().clean()
        asset = cleaned.get('asset')
        issue_date = cleaned.get('issue_date')
        expected = cleaned.get('expected_return_date')
        if issue_date and expected and expected < issue_date:
            self.add_error('expected_return_date', 'The expected return date cannot be before the issue date.')
        if asset:
            if asset.current_status != Asset.AVAILABLE:
                self.add_error('asset', 'Only an available asset can be assigned.')
            elif asset.available_quantity < 1:
                self.add_error(
                    'asset',
                    'This asset already has an active assignment.'
                    if asset.is_serialized else 'All items of this asset are already assigned.',
                )
        accessory_assets = cleaned.get('accessory_assets')
        if accessory_assets:
            if asset and asset in accessory_assets:
                self.add_error(
                    'accessory_assets', 'The main asset cannot also be listed as an accessory.',
                )
            for accessory in accessory_assets:
                if accessory.current_status != Asset.AVAILABLE:
                    self.add_error(
                        'accessory_assets',
                        f'{accessory.asset_code} is not available to assign as an accessory.',
                    )
                    break
                if accessory.available_quantity < 1:
                    self.add_error(
                        'accessory_assets',
                        f'{accessory.asset_code} has no items available to assign as an accessory.',
                    )
                    break
        if cleaned.get('acknowledgement_type') == AssetAcknowledgement.DIGITAL:
            if not (cleaned.get('acknowledgement_name') or '').strip():
                self.add_error('acknowledgement_name', 'Enter the name of the person confirming receipt.')
            if not (cleaned.get('confirmation_text') or '').strip():
                self.add_error('confirmation_text', 'An explicit employee confirmation is required.')
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.accessories_issued = _comma_list(self.cleaned_data.get('accessories'))
        if commit:
            obj.save()
        return obj


class ReturnForm(StyledForm, forms.ModelForm):
    returned_accessories = forms.CharField(
        label='Accessories returned', required=False,
        help_text='Separate items with commas. Leave blank when none were returned.',
    )

    class Meta:
        model = AssetAssignment
        fields = ['actual_return_date', 'return_condition', 'return_notes']
        widgets = {
            'actual_return_date': DATE,
            'return_notes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial['actual_return_date'] = timezone.localdate()
            self.initial['returned_accessories'] = ', '.join(self.instance.accessories_issued or [])

    def clean_actual_return_date(self):
        value = self.cleaned_data.get('actual_return_date')
        if value and self.instance.issue_date and value < self.instance.issue_date:
            raise forms.ValidationError('The return date cannot be before the issue date.')
        return value


class LeaseForm(StyledForm, forms.ModelForm):
    class Meta:
        model = AssetLease
        fields = [
            'asset', 'lessor_name', 'agreement_number', 'start_date', 'end_date', 'monthly_rent',
            'security_deposit', 'billing_frequency', 'purchase_option', 'return_required',
        ]
        widgets = {'start_date': DATE, 'end_date': DATE}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['asset'].queryset = Asset.objects.select_related('category')
        if not self.is_bound:
            self.initial['start_date'] = timezone.localdate()

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('start_date'), cleaned.get('end_date')
        if start and end and end < start:
            self.add_error('end_date', 'The lease end date cannot be before the start date.')
        return cleaned


class LeaseStatusForm(StyledForm, forms.Form):
    status = forms.ChoiceField(choices=AssetLease.STATUSES, label='Lease status')
    remarks = forms.CharField(
        required=False, widget=forms.Textarea(attrs={'rows': 2}),
        label='Note for the lease history',
    )


class MaintenanceForm(StyledForm, forms.ModelForm):
    class Meta:
        model = AssetMaintenance
        fields = ['asset', 'service_date', 'service_provider', 'issue_description', 'warranty_claim']
        widgets = {
            'service_date': DATE,
            'issue_description': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['asset'].queryset = Asset.objects.select_related('category')
        if not self.is_bound:
            self.initial['service_date'] = timezone.localdate()


class MaintenanceUpdateForm(StyledForm, forms.ModelForm):
    class Meta:
        model = AssetMaintenance
        fields = ['status', 'completion_date', 'work_performed', 'repair_cost', 'remarks']
        widgets = {
            'completion_date': DATE,
            'work_performed': forms.Textarea(attrs={'rows': 3}),
            'remarks': forms.Textarea(attrs={'rows': 2}),
        }

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get('status')
        completion = cleaned.get('completion_date')
        service_date = self.instance.service_date
        if status == 'completed' and not completion:
            self.add_error('completion_date', 'Enter the date the work was completed.')
        if completion and service_date and completion < service_date:
            self.add_error('completion_date', 'The completion date cannot be before the service date.')
        return cleaned
