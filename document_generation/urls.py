from django.urls import path

from . import views

app_name = 'document_generation'
urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('catalogue/', views.catalogue, name='catalogue'),
    path('settings/', views.settings, name='settings'),
    path('library/', views.library, name='library'),
    path('library/categories/', views.category_library, name='categories'),
    path('library/types/', views.type_library, name='types'),
    path('library/templates/', views.template_library, name='templates'),
    path('library/versions/', views.template_versions, name='template_versions'),
    path('library/templates/new/', views.template_edit, name='template_new'),
    path('library/templates/<int:template_id>/', views.template_detail, name='template_detail'),
    path('library/templates/<int:template_id>/edit/', views.template_edit, name='template_edit'),
    path('library/templates/<int:template_id>/duplicate/', views.template_duplicate, name='template_duplicate'),
    path('library/templates/<int:template_id>/versions/new/', views.template_new_version, name='template_new_version'),
    path('library/versions/<int:version_id>/edit/', views.template_version_edit, name='template_version_edit'),
    path('library/versions/<int:version_id>/<str:action>/', views.template_version_action, name='template_version_action'),
    path('library/clauses/', views.clause_library, name='clauses'),
    path('library/clauses/new/', views.clause_edit, name='clause_new'),
    path('library/clauses/<int:clause_id>/edit/', views.clause_edit, name='clause_edit'),
    path('library/clauses/<int:clause_id>/versions/new/', views.clause_new_version, name='clause_new_version'),
    path('library/clauses/<int:clause_id>/<str:action>/', views.clause_action, name='clause_action'),
    path('generate/', views.generate_document, name='generate'),
    path('template-preview/<int:version_id>/', views.template_preview, name='template_preview'),
    path('download/<int:document_id>/<str:output_format>/', views.download, name='download'),
    path('<slug:section>/', views.placeholder, name='placeholder'),
]
