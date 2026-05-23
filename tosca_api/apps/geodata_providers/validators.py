"""Shared validators for layer-related cross-app integration."""

from __future__ import annotations

from django.core.exceptions import ValidationError

from tosca_api.apps.geodata_providers.models import Layer


def validate_layer_is_public_and_published(layer: Layer | None) -> None:
    """
    Reject any layer that is not both public and published.

    Used by GeoStoryLayer / EventLayer / FeedbackLayer through-model `clean()`
    so consumer apps (geostories, events, feedback) only attach layers that
    are safe to render to public consumers and that are actually live in the
    underlying engine (GeoServer / Martin / pg_tileserv).
    """
    if layer is None:
        return

    errors: dict[str, str] = {}
    if not layer.is_public:
        errors["layer"] = (
            f"Layer '{layer}' is not public and cannot be attached to a "
            "GeoStory, Event, or GeoFeedback."
        )
    elif layer.publishing_state != Layer.PublishingState.PUBLISHED:
        errors["layer"] = (
            f"Layer '{layer}' must be PUBLISHED to be attached to a "
            f"GeoStory, Event, or GeoFeedback (current state: "
            f"{layer.publishing_state})."
        )
    elif layer.sync_state in {Layer.SyncState.FAILED, Layer.SyncState.STALE}:
        errors["layer"] = (
            f"Layer '{layer}' is not currently synchronized with its provider "
            f"(sync state: {layer.sync_state})."
        )

    if errors:
        raise ValidationError(errors)
