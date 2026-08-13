import logging
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ...models import LayerGroup

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LayerGroupPublicationResult:
    """Outcome of reconciling a group's publication state after an edit."""

    ok: bool
    error: str = ""


class LayerGroupService:
    """Owns layer-group publication state transitions.

    Editing surfaces (admin, and any future API or script) persist the group
    and its members, then call :meth:`reconcile_publication` so the sync-state
    machine lives in one place instead of the admin hook. Member validity rules
    themselves stay on ``LayerGroup.validate_members``; this service only maps
    their result onto the persisted state fields.
    """

    @classmethod
    def reconcile_publication(
        cls,
        *,
        group: LayerGroup,
        refresh_legend: bool = False,
    ) -> LayerGroupPublicationResult:
        """Validate members and mark the group SYNCED or FAILED.

        ``refresh_legend`` re-fingerprints the uploaded legend against the
        current composition first, for callers that changed the legend or
        confirmed it still current.
        """
        if refresh_legend:
            group.refresh_legend_composition_hash()

        try:
            group.validate_members()
        except ValidationError as exc:
            message = " | ".join(exc.messages)
            cls._mark_failed(group=group, message=message)
            return LayerGroupPublicationResult(ok=False, error=message)

        cls._mark_synced(group=group)
        return LayerGroupPublicationResult(ok=True)

    @staticmethod
    def _mark_failed(*, group: LayerGroup, message: str) -> None:
        with transaction.atomic():
            LayerGroup.objects.filter(pk=group.pk).update(
                publishing_state=LayerGroup.PublishingState.FAILED,
                publishing_error=message,
                sync_state=LayerGroup.SyncState.FAILED,
                last_sync_error=message,
                last_sync_at=timezone.now(),
            )

    @staticmethod
    def _mark_synced(*, group: LayerGroup) -> None:
        with transaction.atomic():
            LayerGroup.objects.filter(pk=group.pk).update(
                publishing_error="",
                last_sync_error="",
                sync_state=LayerGroup.SyncState.SYNCED,
                last_sync_at=timezone.now(),
            )
