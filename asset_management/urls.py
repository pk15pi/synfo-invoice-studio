from django.urls import path

from . import views

app_name = 'assets'

urlpatterns = [
    path('', views.dashboard, name='home'),
    path('register/', views.register, name='register'),
    path('register/new/', views.asset_new, name='asset_new'),
    path('register/<int:pk>/', views.asset_detail, name='asset_detail'),
    path('register/<int:pk>/edit/', views.asset_edit, name='asset_edit'),
    path('locations/', views.locations, name='locations'),
    path('locations/new/', views.location_new, name='location_new'),
    path('locations/<int:pk>/edit/', views.location_edit, name='location_edit'),
    path('assignments/', views.assignments, name='assignments'),
    path('assignments/new/', views.assignment_new, name='assignment_new'),
    path('assignments/<int:pk>/receipt/', views.assignment_receipt, name='assignment_receipt'),
    path('assignments/<int:pk>/return/', views.assignment_return, name='assignment_return'),
    path('leases/', views.leases, name='leases'),
    path('leases/new/', views.lease_new, name='lease_new'),
    path('leases/<int:pk>/update/', views.lease_update, name='lease_update'),
    path('maintenance/', views.maintenance, name='maintenance'),
    path('maintenance/new/', views.maintenance_new, name='maintenance_new'),
    path('maintenance/<int:pk>/update/', views.maintenance_update, name='maintenance_update'),
]
