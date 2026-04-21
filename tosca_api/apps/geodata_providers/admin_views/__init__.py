"""
admin_views package — re-exports all views so admin.py imports stay unchanged.

  from .admin_views import engine_test_connection_view, engine_sync_view
  from .admin_views import store_postgis_tables_view, store_tables_for_workspace  # Phase 3
  from .admin_views import publish_postgis_view                                   # Phase 4
"""
from .engine import engine_test_connection_view, engine_sync_view
from .workspace import workspace_sync_view
from .store import store_postgis_tables_view, store_clone_view
from .layer import publish_postgis_view, stores_for_workspace_view, tables_for_store_view

__all__ = [
    # Engine (Phase 1)
    'engine_test_connection_view',
    'engine_sync_view',
    # Workspace (Phase 2)
    'workspace_sync_view',
    # Store (Phase 3)
    'store_postgis_tables_view',
    'store_clone_view',
    # Layer (Phase 4)
    'publish_postgis_view',
    'stores_for_workspace_view',
    'tables_for_store_view',
]
