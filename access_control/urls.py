from django.urls import path

from . import views, views_password_reset, views_self_service

app_name = 'access_control'

urlpatterns = [
    path('login/', views.AppLoginView.as_view(), name='login'),
    path('logout/', views.AppLogoutView.as_view(), name='logout'),
    path('password-reset/', views_password_reset.identify, name='password_reset'),
    path('password-reset/choose/', views_password_reset.choose, name='password_reset_choose'),
    path('password-reset/verify/', views_password_reset.verify, name='password_reset_verify'),
    path('users/', views.user_list, name='users'),
    path('users/new/', views.user_create, name='user_create'),
    path('users/<int:pk>/edit/', views.user_edit, name='user_edit'),
    path('roles/', views.role_list, name='roles'),
    path('roles/new/', views.role_create, name='role_create'),
    path('roles/<int:pk>/edit/', views.role_edit, name='role_edit'),
    path('me/', views_self_service.dashboard, name='me'),
    path('me/attendance/', views_self_service.attendance, name='me_attendance'),
    path('me/leave/', views_self_service.leave, name='me_leave'),
    path('me/payslips/', views_self_service.payslips, name='me_payslips'),
    path('me/payslips/<int:pk>/pdf', views_self_service.payslip_pdf, name='me_payslip'),
]
