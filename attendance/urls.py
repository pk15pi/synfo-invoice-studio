from django.urls import path

from employee_management.security import local_only

from . import views as v

app_name = 'att'


def p(route, view, name):
    return path(route, local_only(view), name=name)


urlpatterns = [
    p('attendance/', v.daily, 'daily'),
    p('attendance/employee/<int:pk>/', v.month_grid, 'month_grid'),
    p('attendance/summary/', v.summary, 'summary'),
    p('attendance/import/', v.import_view, 'import'),
    p('attendance/template.csv', v.template_csv, 'template'),
    p('attendance/holidays/', v.holidays, 'holidays'),
    p('attendance/holidays/new/', v.holiday_form, 'holiday_new'),
    p('attendance/holidays/<int:pk>/edit/', v.holiday_form, 'holiday_edit'),
    p('attendance/holidays/<int:pk>/delete/', v.holiday_delete, 'holiday_delete'),
    p('attendance/weekly-off/', v.weekly_off, 'weekly_off'),
    p('attendance/leave-types/', v.leave_types, 'leave_types'),
    p('attendance/leave-types/new/', v.leave_type_form, 'leave_type_new'),
    p('attendance/leave-types/<int:pk>/edit/', v.leave_type_form, 'leave_type_edit'),
    p('attendance/leave/', v.applications, 'applications'),
    p('attendance/leave/new/', v.application_new, 'application_new'),
    p('attendance/leave/<int:pk>/<str:action>/', v.application_action, 'application_action'),
    p('attendance/balances/', v.balances, 'balances'),
    p('attendance/balances/<int:pk>/edit/', v.balance_edit, 'balance_edit'),
    p('attendance/balances/carry-forward/', v.carry_forward_view, 'carry_forward'),
    p('attendance/reports/', v.reports, 'reports'),
    p('attendance/corrections/', v.corrections, 'corrections'),
    p('attendance/reports/register.<str:fmt>', v.register_export, 'register_export'),
    p('attendance/reports/balances.xlsx', v.balances_export, 'balances_export'),
]
