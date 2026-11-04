from django.contrib import admin

from .models import (
    AuditChecklist,
    AuditChecklistResult,
    ComplianceSubmission,
    ComplianceTemplate,
    ExportManifest,
    ExportPackage,
    ExportPackageItem,
    FinancialPeriod,
    ReconciliationIssue,
    ReconciliationRun,
    ReportCategory,
    ReportDefinition,
    ReportRun,
)


@admin.register(FinancialPeriod)
class FinancialPeriodAdmin(admin.ModelAdmin):
    list_display = ('name', 'period_type', 'start_date', 'end_date', 'financial_year')
    list_filter = ('period_type',)
    search_fields = ('name',)
    readonly_fields = ('created_at', 'updated_at')
    date_hierarchy = 'start_date'


@admin.register(ReportCategory)
class ReportCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'display_order', 'active_status')
    list_filter = ('active_status',)
    search_fields = ('name', 'code', 'description')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ReportDefinition)
class ReportDefinitionAdmin(admin.ModelAdmin):
    list_display = ('report_name', 'report_code', 'category', 'source_application', 'active_status')
    list_filter = ('category', 'source_application', 'active_status')
    search_fields = ('report_name', 'report_code', 'description', 'source_model_or_service')
    autocomplete_fields = ('category',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ReportRun)
class ReportRunAdmin(admin.ModelAdmin):
    list_display = ('report_definition', 'from_date', 'to_date', 'status', 'record_count', 'generated_at')
    list_filter = ('status', 'output_format', 'generated_at')
    search_fields = ('report_definition__report_name', 'report_definition__report_code', 'error_details')
    autocomplete_fields = ('report_definition', 'financial_period')
    readonly_fields = tuple(
        field.name for field in ReportRun._meta.fields if field.name != 'generated_file'
    ) + ('generated_file_name',)
    exclude = ('generated_file',)
    date_hierarchy = 'generated_at'

    @admin.display(description='Private generated file')
    def generated_file_name(self, obj):
        return obj.generated_file.name if obj.generated_file else '-'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ExportPackageItemInline(admin.TabularInline):
    model = ExportPackageItem
    extra = 0
    can_delete = False
    readonly_fields = tuple(field.name for field in ExportPackageItem._meta.fields)
    show_change_link = True


@admin.register(ExportPackage)
class ExportPackageAdmin(admin.ModelAdmin):
    list_display = ('package_number', 'package_type', 'financial_period', 'generation_status', 'generated_at')
    list_filter = ('package_type', 'generation_status')
    search_fields = ('package_number',)
    autocomplete_fields = ('financial_period',)
    readonly_fields = tuple(
        field.name for field in ExportPackage._meta.fields if field.name != 'zip_file'
    ) + ('zip_file_name',)
    exclude = ('zip_file',)
    inlines = (ExportPackageItemInline,)
    date_hierarchy = 'created_at'

    @admin.display(description='Private ZIP file')
    def zip_file_name(self, obj):
        return obj.zip_file.name if obj.zip_file else '-'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ExportPackageItem)
class ExportPackageItemAdmin(admin.ModelAdmin):
    list_display = ('package', 'item_type', 'file_name', 'record_count', 'file_size', 'included')
    list_filter = ('item_type', 'included')
    search_fields = ('package__package_number', 'file_name', 'sha256')
    autocomplete_fields = ('package', 'report_run')
    readonly_fields = tuple(field.name for field in ExportPackageItem._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReconciliationRun)
class ReconciliationRunAdmin(admin.ModelAdmin):
    list_display = (
        'reconciliation_type', 'start_date', 'end_date', 'status',
        'total_records', 'matched_records', 'unmatched_records', 'generated_at',
    )
    list_filter = ('reconciliation_type', 'status', 'generated_at')
    search_fields = ('reconciliation_type', 'error_details')
    autocomplete_fields = ('financial_period',)
    readonly_fields = tuple(field.name for field in ReconciliationRun._meta.fields)
    date_hierarchy = 'generated_at'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReconciliationIssue)
class ReconciliationIssueAdmin(admin.ModelAdmin):
    list_display = ('reconciliation_run', 'source_reference', 'issue_category', 'resolution_status', 'updated_at')
    list_filter = ('issue_category', 'resolution_status', 'source_application')
    search_fields = ('source_reference', 'resolution_remarks', 'reconciliation_run__reconciliation_type')
    autocomplete_fields = ('reconciliation_run',)
    readonly_fields = ('created_at', 'updated_at', 'expected_value', 'actual_value', 'difference')
    fields = (
        'reconciliation_run', 'source_application', 'source_reference', 'issue_category',
        'expected_value', 'actual_value', 'difference', 'resolution_status',
        'resolution_remarks', 'created_at', 'updated_at',
    )


@admin.register(AuditChecklist)
class AuditChecklistAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'category', 'display_order', 'active_status')
    list_filter = ('category', 'active_status')
    search_fields = ('name', 'code', 'description')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(AuditChecklistResult)
class AuditChecklistResultAdmin(admin.ModelAdmin):
    list_display = ('checklist', 'financial_period', 'status', 'checked_at')
    list_filter = ('status', 'checklist__category')
    search_fields = ('checklist__name', 'summary', 'resolution_remarks')
    autocomplete_fields = ('checklist', 'financial_period')
    readonly_fields = ('checklist', 'financial_period', 'status', 'summary', 'details', 'checked_at')
    fields = ('checklist', 'financial_period', 'status', 'summary', 'details', 'resolution_remarks', 'checked_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ComplianceTemplate)
class ComplianceTemplateAdmin(admin.ModelAdmin):
    list_display = (
        'template_name', 'template_code', 'compliance_type', 'applicable_financial_year',
        'legal_framework', 'tax_regime', 'form_version', 'active_status',
    )
    list_filter = ('compliance_type', 'legal_framework', 'tax_regime', 'active_status')
    search_fields = ('template_name', 'template_code', 'form_version')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ComplianceSubmission)
class ComplianceSubmissionAdmin(admin.ModelAdmin):
    list_display = ('compliance_template', 'reporting_period', 'status', 'filing_date', 'acknowledgement_number')
    list_filter = ('status', 'compliance_template__compliance_type')
    search_fields = ('compliance_template__template_name', 'acknowledgement_number')
    autocomplete_fields = ('compliance_template', 'reporting_period')
    readonly_fields = ('created_at', 'updated_at', 'uploaded_acknowledgement_name')
    exclude = ('uploaded_acknowledgement',)

    @admin.display(description='Private acknowledgement file')
    def uploaded_acknowledgement_name(self, obj):
        return obj.uploaded_acknowledgement.name if obj.uploaded_acknowledgement else '-'


@admin.register(ExportManifest)
class ExportManifestAdmin(admin.ModelAdmin):
    list_display = ('package', 'version', 'sha256', 'created_at')
    search_fields = ('package__package_number', 'sha256')
    autocomplete_fields = ('package',)
    readonly_fields = tuple(field.name for field in ExportManifest._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
