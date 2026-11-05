from django.urls import path

from . import views as v
from . import views_advances as av
from . import views_hubs as h
from .security import local_only

app_name = 'em'


def p(route, view, name):
    return path(route, local_only(view), name=name)


urlpatterns = [
    p('hr/', v.hr_dashboard, 'home'),
    p('payroll/advances/', av.advances, 'advances'),
    p('payroll/advances/new/', av.advance_new, 'advance_new'),
    p('payroll/advances/<int:pk>/', av.advance_detail, 'advance_detail'),
    p('payroll/advances/<int:pk>/<str:action>/', av.advance_action, 'advance_action'),
    p('payroll/advances/employee/<int:pk>/', av.employee_ledger, 'advance_ledger'),
    p('departments/', v.departments, 'departments'),
    p('departments/new/', v.department_form, 'department_new'),
    p('departments/<int:pk>/edit/', v.department_form, 'department_edit'),
    p('departments/<int:pk>/toggle/', v.department_toggle, 'department_toggle'),
    p('departments/<int:pk>/delete/', v.department_delete, 'department_delete'),
    p('employees/', v.employee_list, 'employees'),
    p('employees/new/', v.employee_form, 'employee_new'),
    p('employees/<int:pk>/', v.employee_detail, 'employee_detail'),
    p('employees/<int:pk>/edit/', v.employee_form, 'employee_edit'),
    p('employees/<int:pk>/photo/', v.employee_photo, 'employee_photo'),
    p('employees/<int:pk>/contacts/new/', v.contact_form, 'contact_new'),
    p('employees/<int:pk>/contacts/<int:cid>/edit/', v.contact_form, 'contact_edit'),
    p('employees/<int:pk>/contacts/<int:cid>/delete/', v.contact_delete, 'contact_delete'),
    p('employees/<int:pk>/documents/new/', v.document_upload, 'document_new'),
    p('employees/<int:pk>/documents/<int:replace_id>/replace/', v.document_upload, 'document_replace'),
    p('employees/<int:pk>/documents/<int:did>/view/', v.document_view, 'document_view'),
    p('employees/<int:pk>/documents/<int:did>/download/', v.document_download, 'document_download'),
    p('employees/<int:pk>/documents/<int:did>/history/', v.document_history, 'document_history'),
    p('employees/<int:pk>/documents/<int:did>/delete/', v.document_delete, 'document_delete'),
    p('employees/<int:pk>/bank/new/', v.bank_add, 'bank_new'),
    p('employees/<int:pk>/salary/new/', v.salary_new, 'salary_new'),
    p('employees/<int:pk>/salary/<int:from_id>/revise/', v.salary_new, 'salary_revise'),
    p('payroll/', v.payroll_dashboard, 'payroll'),
    p('payroll/generate/', v.payroll_generate, 'payroll_generate'),
    p('payroll/period/<int:pid>/refresh-drafts/', v.payroll_refresh_drafts, 'payroll_refresh_drafts'),
    p('payroll/period/<int:pid>/update/', v.period_update, 'period_update'),
    p('payroll/period/<int:pid>/finalize/', v.payroll_finalize, 'payroll_finalize'),
    p('payroll/period/<int:pid>/payslips.zip', v.payslip_zip, 'payslip_zip'),
    p('payroll/payslips/financial-year/<int:fy_start>.zip', v.payslip_year_zip, 'payslip_year_zip'),
    p('payroll/period/<int:pid>/register.xlsx', v.export_register, 'export_register'),
    p('payroll/period/<int:pid>/bank.<str:fmt>', v.export_bank, 'export_bank'),
    p('payroll/record/<int:rid>/', v.payroll_detail, 'payroll_detail'),
    p('payroll/record/<int:rid>/edit/', v.payroll_edit, 'payroll_edit'),
    p('payroll/record/<int:rid>/finalize/', v.payroll_finalize_one, 'payroll_finalize_one'),
    p('payroll/record/<int:rid>/delete/', v.payroll_delete_draft, 'payroll_delete'),
    p('payroll/record/<int:rid>/revise/', v.payroll_revise, 'payroll_revise'),
    p('payroll/record/<int:rid>/refresh-attendance/', v.payroll_refresh_attendance, 'payroll_refresh_attendance'),
    p('payroll/record/<int:rid>/payslip.pdf', v.payslip_pdf, 'payslip'),
    p('payroll/record/<int:rid>/salary-payments/add/', v.salary_payment_add, 'salary_payment_add'),
    p('payroll/salary-payments/<int:spid>/cancel/', v.salary_payment_cancel, 'salary_payment_cancel'),
    p('payroll/period/<int:pid>/pay-all/', v.salary_pay_all, 'salary_pay_all'),
    p('personal-details/', h.personal_details, 'personal_details'),
    p('documents/', h.documents, 'documents'),
    p('bank-details/', h.bank_details, 'bank_details'),
    p('payroll/salary-structures/', h.salary_structures, 'salary_structures'),
    p('payroll/payslips/', h.payslips, 'payslips'),
    p('storage/', h.storage, 'storage'),
    p('payroll/period/<int:pid>/register.csv', v.export_register_csv, 'export_register_csv'),
    p('payroll/reports/', v.reports, 'reports'),
    p('payroll/settings/', v.payroll_settings, 'settings'),
    p('payroll/settings/rules/new/', v.rule_form, 'rule_new'),
    p('payroll/settings/rules/<int:pk>/edit/', v.rule_form, 'rule_edit'),
]
