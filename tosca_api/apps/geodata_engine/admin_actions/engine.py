"""
Admin actions for GeodataEngine.
    sync_engines      — pull GeoServer → Django for selected engines
    test_connection   — validate connection, report latency + version
    set_as_default    — mark one engine as the default, unset others
"""
import time

from django.contrib import admin, messages

from ..engine_factory import EngineClientFactory
from ..exceptions import GeoServerConnectionError, GeodataEngineError
from ..models import GeodataEngine
from ..sync_service import GeoServerSyncService


@admin.action(description="Sync selected engines from GeoServer → Django")
def sync_engines(modeladmin, request, queryset):
    """Pull GeoServer state into Django for every selected engine."""
    for engine in queryset:
        try:
            service = GeoServerSyncService(engine)
            result = service.sync_all_resources(created_by=request.user)
        except GeoServerConnectionError as e:
            modeladmin.message_user(
                request,
                f"[{engine.name}] Engine unreachable: {e}",
                messages.ERROR,
            )
            continue
        except GeodataEngineError as e:
            modeladmin.message_user(
                request,
                f"[{engine.name}] Sync error: {e}",
                messages.ERROR,
            )
            continue

        if result.get('success'):
            ws = result.get('workspaces', {})
            st = result.get('stores', {})
            ly = result.get('layers', {})
            modeladmin.message_user(
                request,
                (
                    f"[{engine.name}] Sync complete — "
                    f"workspaces: {ws.get('created', 0)} created / {ws.get('deleted', 0)} deleted, "
                    f"stores: {st.get('created', 0)} created / {st.get('deleted', 0)} deleted, "
                    f"layers: {ly.get('created', 0)} created / {ly.get('deleted', 0)} deleted."
                ),
                messages.SUCCESS,
            )
        else:
            modeladmin.message_user(
                request,
                f"[{engine.name}] Sync failed: {result.get('error', 'unknown error')}",
                messages.ERROR,
            )


@admin.action(description="Test connection to selected engines")
def test_connection(modeladmin, request, queryset):
    """Call validate_connection() on each selected engine, report latency + version."""
    for engine in queryset:
        try:
            client = EngineClientFactory.create_client(engine)
            t0 = time.monotonic()
            result = client.validate_connection()
            latency_ms = int((time.monotonic() - t0) * 1000)
            version = result.get('version') or 'unknown'
            modeladmin.message_user(
                request,
                f"[{engine.name}] Connected — GeoServer {version}, latency {latency_ms} ms.",
                messages.SUCCESS,
            )
        except GeoServerConnectionError as e:
            modeladmin.message_user(
                request,
                f"[{engine.name}] Unreachable: {e}",
                messages.ERROR,
            )
        except GeodataEngineError as e:
            modeladmin.message_user(
                request,
                f"[{engine.name}] Error: {e}",
                messages.ERROR,
            )


@admin.action(description="Set selected engine as the default engine")
def set_as_default(modeladmin, request, queryset):
    """Set is_default=True on exactly one selected engine, unset all others."""
    if queryset.count() != 1:
        modeladmin.message_user(
            request,
            "Select exactly one engine to set as default.",
            messages.ERROR,
        )
        return

    engine = queryset.first()
    GeodataEngine.objects.filter(is_default=True).exclude(pk=engine.pk).update(is_default=False)
    engine.is_default = True
    engine.save(update_fields=['is_default'])
    modeladmin.message_user(
        request,
        f"'{engine.name}' is now the default engine.",
        messages.SUCCESS,
    )
