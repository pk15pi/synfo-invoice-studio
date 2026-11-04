from django.urls import path

from . import views

app_name = 'backup'
urlpatterns = [
    path('', views.home, name='home'),
    path('create/', views.create, name='create'),
    path('export/', views.export_package, name='export'),
    path('import/', views.import_package, name='import'),
    path('import/<uuid:package_id>/', views.merge_preview, name='merge_preview'),
    path('import/<uuid:package_id>/confirm/', views.confirm_merge, name='confirm_merge'),
    path('import/<uuid:package_id>/download/', views.download_import_package, name='download_import_package'),
    path('retention/', views.retention, name='retention'),
    path('<int:pk>/', views.detail, name='detail'),
    path('<int:pk>/verify/', views.verify, name='verify'),
]
