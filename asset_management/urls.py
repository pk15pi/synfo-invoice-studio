from django.urls import path

from . import views

app_name = 'assets'

urlpatterns = [
    path('', views.dashboard, name='home'),
    path('register/', views.register, name='register'),
    path('register/new/', views.placeholder, {'section': 'new'}, name='asset_new'),
    path('assignments/', views.placeholder, {'section': 'assignments'}, name='assignments'),
    path('leases/', views.placeholder, {'section': 'leases'}, name='leases'),
    path('maintenance/', views.placeholder, {'section': 'maintenance'}, name='maintenance'),
]
