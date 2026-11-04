from django.contrib import admin

from .models import (
    Asset,
    AssetAcknowledgement,
    AssetAssignment,
    AssetCategory,
    AssetEvent,
    AssetLease,
    AssetLocation,
    AssetMaintenance,
    AssetSettings,
)


@admin.register(AssetCategory)
class AssetCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'is_active', 'created_at', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'code', 'description')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(AssetLocation)
class AssetLocationAdmin(admin.ModelAdmin):
    list_display = ('name', 'location_type', 'parent', 'is_active', 'updated_at')
    list_filter = ('location_type', 'is_active')
    search_fields = ('name', 'address', 'parent__name')
    autocomplete_fields = ('parent',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = (
        'asset_code', 'name', 'category', 'ownership_type', 'usage_type',
        'current_status', 'current_employee', 'current_location',
    )
    list_filter = ('category', 'ownership_type', 'usage_type', 'current_status', 'condition')
    search_fields = (
        'asset_code', 'name', 'brand', 'model_number', 'serial_number',
        'current_employee__employee_id', 'current_employee__first_name',
        'current_employee__last_name', 'current_location__name',
    )
    autocomplete_fields = ('category', 'current_location')
    readonly_fields = ('created_at', 'updated_at')
    list_select_related = ('category', 'current_employee', 'current_location')


@admin.register(AssetLease)
class AssetLeaseAdmin(admin.ModelAdmin):
    list_display = (
        'asset', 'lessor_name', 'agreement_number', 'start_date', 'end_date',
        'monthly_rent', 'status',
    )
    list_filter = ('status', 'billing_frequency', 'purchase_option', 'return_required')
    search_fields = ('asset__asset_code', 'asset__name', 'lessor_name', 'agreement_number')
    autocomplete_fields = ('asset',)
    readonly_fields = ('created_at', 'updated_at')
    list_select_related = ('asset', 'agreement_document')
    date_hierarchy = 'start_date'


@admin.register(AssetAssignment)
class AssetAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        'asset', 'employee', 'issue_date', 'expected_return_date',
        'actual_return_date', 'status',
    )
    list_filter = ('status', 'issue_date')
    search_fields = (
        'asset__asset_code', 'asset__name', 'employee__employee_id',
        'employee__first_name', 'employee__last_name',
    )
    autocomplete_fields = ('asset',)
    readonly_fields = ('created_at', 'updated_at')
    list_select_related = ('asset', 'employee')
    date_hierarchy = 'issue_date'


@admin.register(AssetAcknowledgement)
class AssetAcknowledgementAdmin(admin.ModelAdmin):
    list_display = (
        'assignment', 'acknowledgement_type', 'acknowledgement_name',
        'acknowledged_at', 'document',
    )
    list_filter = ('acknowledgement_type', 'acknowledged_at')
    search_fields = (
        'acknowledgement_name', 'assignment__asset__asset_code',
        'assignment__employee__employee_id', 'assignment__employee__first_name',
        'assignment__employee__last_name', 'confirmation_text',
    )
    autocomplete_fields = ('assignment',)
    readonly_fields = ('created_at',)
    list_select_related = ('assignment__asset', 'assignment__employee', 'document')
    date_hierarchy = 'acknowledged_at'


@admin.register(AssetEvent)
class AssetEventAdmin(admin.ModelAdmin):
    list_display = (
        'asset', 'event_type', 'event_date', 'from_employee', 'to_employee',
        'from_location', 'to_location',
    )
    list_filter = ('event_type', 'event_date')
    search_fields = (
        'asset__asset_code', 'asset__name', 'remarks', 'reference_type', 'reference_id',
        'from_employee__employee_id', 'to_employee__employee_id',
        'from_location__name', 'to_location__name',
    )
    list_select_related = (
        'asset', 'from_employee', 'to_employee', 'from_location', 'to_location',
    )
    date_hierarchy = 'event_date'
    readonly_fields = tuple(field.name for field in AssetEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AssetMaintenance)
class AssetMaintenanceAdmin(admin.ModelAdmin):
    list_display = (
        'asset', 'service_date', 'service_provider', 'status',
        'repair_cost', 'warranty_claim', 'completion_date',
    )
    list_filter = ('status', 'warranty_claim', 'service_date')
    search_fields = (
        'asset__asset_code', 'asset__name', 'service_provider',
        'issue_description', 'work_performed',
    )
    autocomplete_fields = ('asset',)
    readonly_fields = ('created_at', 'updated_at')
    list_select_related = ('asset',)
    date_hierarchy = 'service_date'


@admin.register(AssetSettings)
class AssetSettingsAdmin(admin.ModelAdmin):
    list_display = (
        'asset_prefix', 'next_asset_sequence', 'handover_prefix',
        'next_handover_sequence', 'default_currency',
    )

    def has_add_permission(self, request):
        return not AssetSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
