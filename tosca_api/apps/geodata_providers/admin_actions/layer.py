# Phase 4 — Layer admin actions
import logging

from django.contrib import admin, messages

from ..exceptions import GeoServerConnectionError, GeoServerPublishError
from ..services.commands.layer_service import LayerService

logger = logging.getLogger(__name__)


@admin.action(description='Publish selected layers to GeoServer')
def publish_layer(modeladmin, request, queryset):
    """
    For each selected DRAFT/UNPUBLISHED layer:
      1. Pre-check: not already published in GeoServer
      2. publish_featuretype in GeoServer
      3. verify_featuretype
      4. set publishing_state = PUBLISHED in Django
    Skips layers that are already PUBLISHED.
    """
    for layer in queryset.select_related('workspace__geodata_engine', 'store'):
        if layer.publishing_state == 'PUBLISHED':
            modeladmin.message_user(
                request,
                f"Layer '{layer.name}' is already PUBLISHED — skipped.",
                messages.WARNING,
            )
            continue

        try:
            result = LayerService.publish_existing_layer(layer)
            level = messages.SUCCESS
            if result.get('already_exists'):
                level = messages.WARNING
            elif not result.get('success'):
                level = messages.ERROR

            modeladmin.message_user(
                request,
                result.get('error', result.get('message', f"Layer '{layer.name}' publish failed.")),
                level,
            )

        except GeoServerConnectionError as exc:
            modeladmin.message_user(
                request,
                f"Layer '{layer.name}': engine unreachable — {exc}",
                messages.ERROR,
            )
        except GeoServerPublishError as exc:
            modeladmin.message_user(
                request,
                f"Layer '{layer.name}': GeoServer publish failed — {exc}",
                messages.ERROR,
            )


@admin.action(description='Unpublish selected layers from GeoServer')
def unpublish_layer(modeladmin, request, queryset):
    """
    For each selected PUBLISHED layer:
      1. delete_layer in GeoServer
      2. verify deletion (verify_featuretype returns False)
      3. set publishing_state = UNPUBLISHED in Django
    Skips layers that are not PUBLISHED.
    """
    for layer in queryset.select_related('workspace__geodata_engine', 'store'):
        if layer.publishing_state != 'PUBLISHED':
            modeladmin.message_user(
                request,
                f"Layer '{layer.name}' is not PUBLISHED (state: {layer.publishing_state}) — skipped.",
                messages.WARNING,
            )
            continue

        try:
            result = LayerService.unpublish_layer(layer)
            level = messages.SUCCESS if result.get('success') else messages.ERROR
            modeladmin.message_user(
                request,
                result.get('error', result.get('message', f"Layer '{layer.name}' unpublish failed.")),
                level,
            )

        except GeoServerConnectionError as exc:
            modeladmin.message_user(
                request,
                f"Layer '{layer.name}': engine unreachable — {exc}",
                messages.ERROR,
            )
        except GeoServerPublishError as exc:
            modeladmin.message_user(
                request,
                f"Layer '{layer.name}': GeoServer delete failed — {exc}",
                messages.ERROR,
            )
