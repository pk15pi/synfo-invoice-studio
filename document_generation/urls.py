from django.urls import path

from . import views

app_name = 'document_generation'
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('catalogue/', views.catalogue, name='catalogue'),
    path('settings/', views.settings, name='settings'),
    path('generate/', views.generate_document, name='generate'),
    path('template-preview/<int:version_id>/', views.template_preview, name='template_preview'),
    path('download/<int:document_id>/<str:output_format>/', views.download, name='download'),
    path('<slug:section>/', views.placeholder, name='placeholder'),
]
