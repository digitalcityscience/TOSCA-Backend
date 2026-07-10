"""Structural checks for indexes added on FK/status columns used in the
public catalog hot path (CatalogVisibilityService).

Asserted against model metadata rather than via EXPLAIN, since query
planner index choice on an empty/small test database is data-volume
dependent and not a reliable signal in CI.
"""

from tosca_api.apps.geodata_providers.models import (
    GeodataEngine,
    Layer,
    Store,
    Style,
    Workspace,
)


def test_layer_workspace_is_public_publishing_state_composite_index_declared():
    """Matches CatalogVisibilityService's Exists subquery and direct filter:
    workspace + is_public + publishing_state.
    """
    index_field_sets = {tuple(index.fields) for index in Layer._meta.indexes}
    assert ("workspace", "is_public", "publishing_state") in index_field_sets


def test_geodata_engine_is_active_is_indexed():
    # Filtered directly in CatalogVisibilityService.get_visible_provider()
    # and via geodata_engine__is_active joins on every catalog request.
    assert GeodataEngine._meta.get_field("is_active").db_index is True


def test_sync_state_is_indexed_on_every_sync_state_mixin_model():
    # sync_state (SyncStateMixin) is filtered via .exclude(sync_state__in=...)
    # on Layer, Store, and Workspace directly, and Style shares the same
    # mixin for consistency even though it isn't queried by the catalog yet.
    for model in (Workspace, Store, Layer, Style):
        assert model._meta.get_field("sync_state").db_index is True, model
