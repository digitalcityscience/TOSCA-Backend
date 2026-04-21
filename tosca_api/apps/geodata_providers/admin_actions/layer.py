# Phase 4 — Layer admin actions
import logging

from django.contrib import admin, messages
from django.utils import timezone

from ..engine_factory import EngineClientFactory
from ..exceptions import GeoServerConnectionError, GeoServerPublishError

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

        engine = layer.workspace.geodata_engine if layer.workspace else None
        if not engine:
            modeladmin.message_user(
                request,
                f"Layer '{layer.name}' has no engine — skipped.",
                messages.WARNING,
            )
            continue

        try:
            client = EngineClientFactory.create_client(engine)

            # Step 1: pre-check
            already = client.verify_featuretype(
                workspace=layer.workspace.name,
                store_name=layer.store.name,
                featuretype_name=layer.name,
            )
            if already:
                modeladmin.message_user(
                    request,
                    f"Layer '{layer.name}' already exists in GeoServer — updating state to PUBLISHED.",
                    messages.WARNING,
                )
                layer.publishing_state = 'PUBLISHED'
                layer.save(update_fields=['publishing_state'])
                continue

            # Step 2: publish
            client.publish_featuretype(
                store_name=layer.store.name,
                workspace=layer.workspace.name,
                pg_table=layer.table_name,
                srid=layer.srid,
                geometry_type=layer.geometry_type,
                layer_name=layer.name,
                title=layer.title or layer.name,
            )

            # Step 3: verify
            verified = client.verify_featuretype(
                workspace=layer.workspace.name,
                store_name=layer.store.name,
                featuretype_name=layer.name,
            )
            if not verified:
                modeladmin.message_user(
                    request,
                    f"Layer '{layer.name}': publish reported success but verification failed.",
                    messages.ERROR,
                )
                continue

            # Step 4: persist
            layer.publishing_state = 'PUBLISHED'
            layer.published_at = timezone.now()
            layer.publishing_error = ''
            layer.save(update_fields=['publishing_state', 'published_at', 'publishing_error'])

            modeladmin.message_user(
                request,
                f"Layer '{layer.name}' published successfully.",
                messages.SUCCESS,
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

        engine = layer.workspace.geodata_engine if layer.workspace else None
        if not engine:
            modeladmin.message_user(
                request,
                f"Layer '{layer.name}' has no engine — skipped.",
                messages.WARNING,
            )
            continue

        try:
            client = EngineClientFactory.create_client(engine)

            # Step 1: delete from GeoServer
            client.delete_layer(layer.workspace.name, layer.name)

            # Step 2: verify gone
            still_there = client.verify_featuretype(
                workspace=layer.workspace.name,
                store_name=layer.store.name,
                featuretype_name=layer.name,
            )
            if still_there:
                modeladmin.message_user(
                    request,
                    f"Layer '{layer.name}': delete reported success but layer still exists in GeoServer.",
                    messages.ERROR,
                )
                continue

            # Step 3: update Django
            layer.publishing_state = 'UNPUBLISHED'
            layer.save(update_fields=['publishing_state'])

            modeladmin.message_user(
                request,
                f"Layer '{layer.name}' unpublished successfully.",
                messages.SUCCESS,
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
