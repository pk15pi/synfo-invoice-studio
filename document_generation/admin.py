from django.contrib import admin
from django.utils import timezone

from .models import (
    CompanyDocumentProfile,
    CompanyDocumentSignatory,
    DocumentAcknowledgement,
    DocumentActivityLog,
    DocumentApproval,
    DocumentCategory,
    DocumentClause,
    DocumentFieldDefinition,
    DocumentNumberSequence,
    DocumentParty,
    DocumentSignature,
    DocumentTemplate,
    DocumentTemplateClause,
    DocumentTemplateVersion,
    DocumentType,
    GeneratedDocument,
)


@admin.register(CompanyDocumentProfile)
class CompanyDocumentProfileAdmin(admin.ModelAdmin):
    list_display = ('legal_entity_name', 'brand_name', 'llpin', 'updated_at')
    readonly_fields = ('created_at', 'updated_at', 'private_logo_name')
    exclude = ('logo',)

    @admin.display(description='Private logo file')
    def private_logo_name(self, obj):
        return obj.logo.name if obj.logo else '-'

    def has_add_permission(self, request):
        return not CompanyDocumentProfile.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CompanyDocumentSignatory)
class CompanyDocumentSignatoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'designation', 'is_authorized', 'is_active', 'valid_from', 'valid_to')
    list_filter = ('is_authorized', 'is_active')
    search_fields = ('name', 'designation', 'email')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(DocumentCategory)
class DocumentCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'display_order', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'code', 'description')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(DocumentType)
class DocumentTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'number_prefix', 'approval_required', 'is_active')
    list_filter = ('categories', 'approval_required', 'is_active', 'reset_frequency')
    search_fields = ('name', 'code', 'description', 'number_prefix')
    filter_horizontal = ('categories',)
    readonly_fields = ('created_at', 'updated_at')


class DocumentTemplateVersionInline(admin.TabularInline):
    model = DocumentTemplateVersion
    extra = 0
    can_delete = False
    fields = ('version_number', 'status', 'created_at', 'approved_at')
    readonly_fields = fields
    show_change_link = True


@admin.register(DocumentTemplate)
class DocumentTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'document_type', 'active_version', 'is_active', 'updated_at')
    list_filter = ('document_type', 'is_active')
    search_fields = ('name', 'document_type__name', 'document_type__code')
    readonly_fields = ('created_at', 'updated_at')
    inlines = (DocumentTemplateVersionInline,)


@admin.register(DocumentTemplateVersion)
class DocumentTemplateVersionAdmin(admin.ModelAdmin):
    list_display = ('template', 'version_number', 'status', 'approved_at', 'created_at')
    list_filter = ('status', 'template__document_type')
    search_fields = ('template__name', 'template__document_type__name')
    readonly_fields = ('created_at',)

    def get_readonly_fields(self, request, obj=None):
        if obj and (obj.status == DocumentTemplateVersion.APPROVED or obj.approved_at):
            return tuple(field.name for field in obj._meta.fields)
        return (*self.readonly_fields, 'approved_by', 'approved_at')

    def get_form(self, request, obj=None, **kwargs):
        parent_form = super().get_form(request, obj, **kwargs)

        class RequestApprovalForm(parent_form):
            def clean(self):
                cleaned = super().clean()
                if cleaned.get('status') == DocumentTemplateVersion.APPROVED:
                    self.instance.approved_by = request.user
                    self.instance.approved_at = self.instance.approved_at or timezone.now()
                return cleaned

        return RequestApprovalForm

    def save_model(self, request, obj, form, change):
        if obj.status == DocumentTemplateVersion.APPROVED and not obj.approved_at:
            obj.approved_by = request.user
            obj.approved_at = timezone.now()
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        if obj.status == DocumentTemplateVersion.APPROVED:
            obj.template.active_version = obj
            obj.template.save(update_fields=('active_version', 'updated_at'))

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DocumentClause)
class DocumentClauseAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'category', 'version', 'approval_status', 'is_active')
    list_filter = ('category', 'approval_status', 'is_active', 'applicable_document_types')
    search_fields = ('name', 'code', 'clause_text')
    filter_horizontal = ('applicable_document_types',)
    readonly_fields = ('created_at', 'updated_at', 'approved_by', 'approved_at')

    def get_readonly_fields(self, request, obj=None):
        if obj and (obj.approval_status == DocumentClause.APPROVED or obj.approved_at):
            return tuple(field.name for field in obj._meta.fields) + ('applicable_document_types',)
        return self.readonly_fields

    def get_form(self, request, obj=None, **kwargs):
        parent_form = super().get_form(request, obj, **kwargs)

        class RequestApprovalForm(parent_form):
            def clean(self):
                cleaned = super().clean()
                if cleaned.get('approval_status') == DocumentClause.APPROVED:
                    self.instance.approved_by = request.user
                    self.instance.approved_at = self.instance.approved_at or timezone.now()
                return cleaned

        return RequestApprovalForm

    def save_model(self, request, obj, form, change):
        if obj.approval_status == DocumentClause.APPROVED and not obj.approved_at:
            obj.approved_by = request.user
            obj.approved_at = timezone.now()
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DocumentTemplateClause)
class DocumentTemplateClauseAdmin(admin.ModelAdmin):
    list_display = ('template_version', 'clause', 'display_order')
    list_filter = ('clause__category',)
    search_fields = ('template_version__template__name', 'clause__name', 'clause__code')

    def get_readonly_fields(self, request, obj=None):
        if obj and (obj.template_version.status == DocumentTemplateVersion.APPROVED
                    or obj.template_version.approved_at):
            return tuple(field.name for field in obj._meta.fields)
        return ()

    def has_delete_permission(self, request, obj=None):
        if obj and (obj.template_version.status == DocumentTemplateVersion.APPROVED
                    or obj.template_version.approved_at):
            return False
        return super().has_delete_permission(request, obj)


@admin.register(DocumentFieldDefinition)
class DocumentFieldDefinitionAdmin(admin.ModelAdmin):
    list_display = (
        'label', 'field_key', 'document_type', 'value_type', 'source_module',
        'is_auto_populated', 'is_required', 'is_active',
    )
    list_filter = ('document_type', 'value_type', 'source_module', 'is_auto_populated', 'is_required', 'is_active')
    search_fields = ('label', 'field_key', 'document_type__name')
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (None, {'fields': ('document_type', 'field_key', 'label', 'value_type', 'is_active')}),
        ('Form behavior', {'fields': (
            'is_required', 'default_value', 'placeholder', 'help_text', 'choices',
            'validation_rules', 'visibility_condition', 'display_order',
        )}),
        ('Automatic population', {'fields': ('source_module', 'source_field', 'is_auto_populated')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at')}),
    )


@admin.register(DocumentNumberSequence)
class DocumentNumberSequenceAdmin(admin.ModelAdmin):
    list_display = ('document_type', 'period_key', 'next_value', 'updated_at')
    list_filter = ('document_type',)
    search_fields = ('document_type__name', 'period_key')
    readonly_fields = tuple(field.name for field in DocumentNumberSequence._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GeneratedDocument)
class GeneratedDocumentAdmin(admin.ModelAdmin):
    list_display = ('document_number', 'document_type', 'status', 'issue_date', 'expiry_date', 'created_at')
    list_filter = ('status', 'document_type')
    search_fields = ('document_number', 'document_type__name')
    readonly_fields = tuple(field.name for field in GeneratedDocument._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DocumentParty)
class DocumentPartyAdmin(admin.ModelAdmin):
    list_display = ('generated_document', 'display_name', 'role', 'display_order')
    list_filter = ('role',)
    search_fields = ('generated_document__document_number', 'display_name')
    readonly_fields = tuple(field.name for field in DocumentParty._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DocumentApproval)
class DocumentApprovalAdmin(admin.ModelAdmin):
    list_display = ('generated_document', 'approver_name', 'status', 'requested_at', 'decided_at')
    list_filter = ('status',)
    search_fields = ('generated_document__document_number', 'approver_name')
    readonly_fields = tuple(field.name for field in DocumentApproval._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DocumentAcknowledgement)
class DocumentAcknowledgementAdmin(admin.ModelAdmin):
    list_display = ('generated_document', 'acknowledgement_type', 'acknowledging_name', 'acknowledged_at')
    list_filter = ('acknowledgement_type',)
    search_fields = ('generated_document__document_number', 'acknowledging_name')
    readonly_fields = tuple(field.name for field in DocumentAcknowledgement._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DocumentSignature)
class DocumentSignatureAdmin(admin.ModelAdmin):
    list_display = ('generated_document', 'signer_name', 'method', 'status', 'signed_at')
    list_filter = ('method', 'status')
    search_fields = ('generated_document__document_number', 'signer_name', 'provider_reference')
    readonly_fields = tuple(field.name for field in DocumentSignature._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DocumentActivityLog)
class DocumentActivityLogAdmin(admin.ModelAdmin):
    list_display = ('generated_document', 'action', 'actor_label', 'created_at')
    list_filter = ('action', 'created_at')
    search_fields = ('generated_document__document_number', 'actor_label')
    readonly_fields = tuple(field.name for field in DocumentActivityLog._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
