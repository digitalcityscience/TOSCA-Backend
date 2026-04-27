"""
Admin actions for GeodataEngine.
    sync_engines      — pull provider state into the local catalog for selected engines
    test_connection   — validate connection, report latency + version
    set_as_default    — mark one engine as the default, unset others
    deactivate_engines / reactivate_engines — toggle engine activity state
"""
import time

from django.contrib import admin, messages

from ..engine_factory import EngineClientFactory
from ..exceptions import GeoServerConnectionError, GeodataEngineError
from ..models import GeodataEngine
from ..services.commands.geodata_engine_service import GeodataEngineService
from ..sync_service import GeoServerSyncService


@admin.action(description="Sync selected providers into the local catalog")
def sync_engines(modeladmin, request, queryset):
    """Pull provider state into the local catalog for every selected engine."""
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
                    f"layers: {ly.get('created', 0)} created / {ly.get('deleted', 0)} deleted. "
                    "Provider state synced into the local catalog."
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
                f"[{engine.name}] Connected — provider version {version}, latency {latency_ms} ms.",
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


@admin.action(description="Deactivate selected engines")
def deactivate_engines(modeladmin, request, queryset):
    for engine in queryset:
        result = GeodataEngineService.deactivate_engine(engine)
        modeladmin.message_user(
            request,
            result['message'],
            messages.WARNING if not engine.is_active else messages.INFO,
        )


@admin.action(description="Reactivate selected engines")
def reactivate_engines(modeladmin, request, queryset):
    for engine in queryset:
        result = GeodataEngineService.reactivate_engine(engine)
        modeladmin.message_user(
            request,
            result['message'],
            messages.SUCCESS,
        )
