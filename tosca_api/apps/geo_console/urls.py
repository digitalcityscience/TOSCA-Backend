from django.urls import path

from .views import (
    console,
    engine_create,
    engine_delete,
    engine_detail,
    engine_edit,
    engine_sync,
    engine_validate,
    set_active_engine,
    store_create,
    store_delete,
    store_list,
    store_test_connection,
    workspace_create,
    workspace_delete,
    workspace_list,
    workspace_sync,
)

urlpatterns = [
    path('', console, name='geo_console'),

    # Engine selector (topbar dropdown — all geo_console pages)
    path('set-engine/', set_active_engine, name='set_active_engine'),

    # Phase 1.5 — Engine create (static path before UUID patterns)
    path('engines/create/', engine_create, name='engine_create'),

    # Phase 1.2 — Engine detail + actions
    path('engines/<uuid:engine_id>/', engine_detail, name='engine_detail'),
    path('engines/<uuid:engine_id>/sync/', engine_sync, name='engine_sync'),
    path('engines/<uuid:engine_id>/validate/', engine_validate, name='engine_validate'),

    # Phase 1.5 — Engine edit + delete
    path('engines/<uuid:engine_id>/edit/', engine_edit, name='engine_edit'),
    path('engines/<uuid:engine_id>/delete/', engine_delete, name='engine_delete'),

    # Phase 2 — Workspaces
    path('workspaces/', workspace_list, name='workspace_list'),
    path('workspaces/create/', workspace_create, name='workspace_create'),
    path('workspaces/<uuid:workspace_id>/delete/', workspace_delete, name='workspace_delete'),
    path('workspaces/<uuid:workspace_id>/sync/', workspace_sync, name='workspace_sync'),

    # Phase 3 — Stores
    path('stores/', store_list, name='store_list'),
    path('stores/create/', store_create, name='store_create'),
    path('stores/<uuid:store_id>/delete/', store_delete, name='store_delete'),
    path('stores/<uuid:store_id>/test/', store_test_connection, name='store_test_connection'),
]
