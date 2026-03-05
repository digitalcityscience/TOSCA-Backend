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
    
    # Workspace functionality - active
    path('workspaces/', views.WorkspacesView.as_view(), name='workspaces'),
    path('workspaces/add/', views.WorkspaceCreateView.as_view(), name='workspace_create'),
    path('workspaces/<uuid:pk>/edit/', views.WorkspaceEditView.as_view(), name='workspace_edit'),
    path('workspaces/<uuid:pk>/delete/', views.WorkspaceDeleteView.as_view(), name='workspace_delete'),
    path('workspaces/sync/', views.sync_workspaces, name='sync_workspaces'),
    
    # Store management
    path('stores/', views.StoresView.as_view(), name='stores'),
    path('stores/add/', views.StoreCreateView.as_view(), name='store_create'),
    path('stores/<uuid:pk>/edit/', views.StoreEditView.as_view(), name='store_edit'),
    path('stores/<uuid:pk>/delete/', views.StoreDeleteView.as_view(), name='store_delete'),
    
    # Layer management
    path('layers/', views.LayersView.as_view(), name='layers'),
    path('layers/add/', views.LayerCreateView.as_view(), name='layer_create'),
    path('layers/<uuid:pk>/edit/', views.LayerEditView.as_view(), name='layer_edit'),
    path('layers/<uuid:pk>/delete/', views.LayerDeleteView.as_view(), name='layer_delete'),
    
    # TODO: Implement remaining views
    # path('styles/', views.StylesView.as_view(), name='styles'),
    # path('sync/', views.SyncView.as_view(), name='sync'),
    # path('jobs/', views.JobsView.as_view(), name='jobs'),
]