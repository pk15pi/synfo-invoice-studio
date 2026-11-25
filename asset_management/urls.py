from django.urls import path

from . import views

app_name = 'assets'

urlpatterns = [
    path('', views.dashboard, name='home'),
    path('register/', views.register, name='register'),
    path('register/new/', views.asset_new, name='asset_new'),
    path('register/<int:pk>/', views.asset_detail, name='asset_detail'),
    path('register/<int:pk>/edit/', views.asset_edit, name='asset_edit'),
    path('register/<int:pk>/lost/', views.asset_mark_lost, name='asset_mark_lost'),
    path('register/<int:pk>/dispose/', views.asset_dispose, name='asset_dispose'),
    path('locations/', views.locations, name='locations'),
    path('locations/new/', views.location_new, name='location_new'),
    path('locations/<int:pk>/edit/', views.location_edit, name='location_edit'),
    path('assignments/', views.assignments, name='assignments'),
    path('assignments/new/', views.assignment_new, name='assignment_new'),
    path('assignments/<int:pk>/receipt/', views.assignment_receipt, name='assignment_receipt'),
    path('assignments/<int:pk>/return/', views.assignment_return, name='assignment_return'),
    path('assignments/<int:pk>/transfer/', views.assignment_transfer, name='assignment_transfer'),
    path('leases/', views.leases, name='leases'),
    path('leases/new/', views.lease_new, name='lease_new'),
    path('leases/<int:pk>/update/', views.lease_update, name='lease_update'),
    path('maintenance/', views.maintenance, name='maintenance'),
    path('maintenance/new/', views.maintenance_new, name='maintenance_new'),
    path('maintenance/<int:pk>/update/', views.maintenance_update, name='maintenance_update'),
    path('categories/', views.categories, name='categories'),
    path('categories/new/', views.category_new, name='category_new'),
    path('categories/<int:pk>/edit/', views.category_edit, name='category_edit'),
    path('consumables/', views.consumables, name='consumables'),
    path('consumables/new/', views.consumable_new, name='consumable_new'),
    path('consumables/<int:pk>/', views.consumable_detail, name='consumable_detail'),
    path('consumables/<int:pk>/edit/', views.consumable_edit, name='consumable_edit'),
    path('consumables/<int:pk>/restock/', views.consumable_restock, name='consumable_restock'),
    path('consumables/<int:pk>/issue/', views.consumable_issue, name='consumable_issue'),
]
