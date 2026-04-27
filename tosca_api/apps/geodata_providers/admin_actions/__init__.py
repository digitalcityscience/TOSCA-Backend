"""
admin_actions package — re-exports all actions so admin.py imports stay unchanged.

  from .admin_actions import sync_engines, test_connection, set_as_default
  from .admin_actions import sync_workspaces          # Phase 2
  from .admin_actions import publish_layer, unpublish_layer  # Phase 4
"""
from .engine import (
    deactivate_engines,
    reactivate_engines,
    set_as_default,
    sync_engines,
    test_connection,
)
from .workspace import sync_workspaces
from .store import clone_store
from .layer import publish_layer, unpublish_layer

__all__ = [
    # Engine (Phase 1)
    'sync_engines',
    'test_connection',
    'set_as_default',
    'deactivate_engines',
    'reactivate_engines',
    # Workspace (Phase 2)
    'sync_workspaces',
    # Store (Phase 3)
    'clone_store',
    # Layer (Phase 4)
    'publish_layer',
    'unpublish_layer',
]
