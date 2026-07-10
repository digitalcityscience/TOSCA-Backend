"""Shared engine/client access and sync-state bookkeeping for every
resource-specific syncer (Workspace/Store/Style/Layer).
"""
from typing import Dict

from django.utils import timezone

from ..geoserver.client import GeoServerClient
from ..models import GeodataEngine


class BaseSyncer:
    """Common base for WorkspaceSyncer/StoreSyncer/StyleSyncer/LayerSyncer.

    Holds the engine + client every syncer needs, plus the small set of
    bookkeeping helpers (mark synced/failed, build the "inactive" result
    shape) that were previously duplicated as private methods on the single
    GeoServerSyncService god class.
    """

    def __init__(self, engine: GeodataEngine, client: GeoServerClient):
        self.engine = engine
        self.client = client

    def _inactive_section_result(self) -> Dict:
        return {
            'synced': 0,
            'created': 0,
            'deleted': 0,
            'errors': [],
            'success': True,
            'skipped': True,
            'reason': f"Engine '{self.engine.name}' is inactive.",
        }

    def _sync_success_defaults(
        self,
        *,
        remote_identifier: str = "",
        remote_hash: str = "",
    ) -> dict:
        return {
            "sync_state": "SYNCED",
            "last_sync_at": timezone.now(),
            "last_sync_error": "",
            "remote_identifier": remote_identifier,
            "remote_hash": remote_hash,
        }

    def _mark_sync_failed(self, obj, error: str) -> None:
        if not obj or not getattr(obj, "pk", None):
            return
        obj.__class__.objects.filter(pk=obj.pk).update(
            sync_state="FAILED",
            last_sync_error=error,
            last_sync_at=timezone.now(),
        )

    def _mark_resource_synced(
        self,
        obj,
        *,
        remote_identifier: str = "",
        remote_hash: str = "",
    ) -> None:
        if not obj or not getattr(obj, "pk", None):
            return
        obj.__class__.objects.filter(pk=obj.pk).update(
            **self._sync_success_defaults(
                remote_identifier=remote_identifier,
                remote_hash=remote_hash,
            )
        )

    def _mark_queryset_sync_failed(self, queryset, error: str) -> None:
        queryset.update(
            sync_state="FAILED",
            last_sync_error=error,
            last_sync_at=timezone.now(),
        )
