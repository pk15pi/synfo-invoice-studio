from django.urls import path
from . import views
urlpatterns = [
 path('', views.dashboard, name='dashboard'), path('business/', views.profile_edit, name='profile_edit'),
 path('customers/', views.clients, name='clients'), path('customers/add/', views.client_edit, name='client_add'), path('customers/<int:pk>/edit/', views.client_edit, name='client_edit'), path('customers/<int:pk>/delete/', views.client_delete, name='client_delete'),
 path('customers/<int:client_id>/projects/', views.projects, name='projects'), path('customers/<int:client_id>/projects/add/', views.project_edit, name='project_add'), path('customers/<int:client_id>/projects/<int:pk>/edit/', views.project_edit, name='project_edit'), path('customers/<int:client_id>/projects/<int:pk>/delete/', views.project_delete, name='project_delete'),
 path('invoice/new/', views.new_invoice, name='new_invoice'), path('invoice/<int:pk>/pdf/', views.invoice_pdf, name='invoice_pdf'), path('invoice/<int:pk>/pdf/<str:manual_number>/', views.invoice_pdf, name='invoice_pdf_manual'),
]
