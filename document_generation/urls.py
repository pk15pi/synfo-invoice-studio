from django.urls import path

from . import views

app_name = 'document_generation'
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('catalogue/', views.catalogue, name='catalogue'),
    path('settings/', views.settings, name='settings'),
    path('generate/', views.generate_document, name='generate'),
    path('download/<int:document_id>/<str:output_format>/', views.download, name='download'),
    path('<slug:section>/', views.placeholder, name='placeholder'),
]
