from django.urls import path
from . import views

app_name = 'console'

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('engines/', views.EnginesView.as_view(), name='engines'),
    path('engines/add/', views.EngineCreateView.as_view(), name='engine_create'),
    path('engines/<uuid:pk>/edit/', views.EngineEditView.as_view(), name='engine_edit'),
    path('engines/<uuid:pk>/delete/', views.EngineDeleteView.as_view(), name='engine_delete'),
    path('engines/sync/', views.sync_engines, name='sync_engines'),
    # TODO: Workspace, Store, Layer functionality - to be implemented later
    # path('workspaces/', views.WorkspacesView.as_view(), name='workspaces'),
    # path('stores/', views.StoresView.as_view(), name='stores'),
    # path('layers/', views.LayersView.as_view(), name='layers'),
    # path('styles/', views.StylesView.as_view(), name='styles'),
    # path('sync/', views.SyncView.as_view(), name='sync'),
    # path('jobs/', views.JobsView.as_view(), name='jobs'),
]