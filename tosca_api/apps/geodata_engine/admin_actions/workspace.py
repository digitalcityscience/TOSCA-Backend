"""
Admin actions for Workspace.
    sync_workspaces — pull GeoServer → Django for selected workspaces' engines
"""
from django.contrib import admin, messages

from ..exceptions import GeoServerConnectionError, GeodataEngineError
from ..sync_service import GeoServerSyncService


@admin.action(description="Sync selected workspaces from GeoServer → Django")
def sync_workspaces(modeladmin, request, queryset):
    """
    For each unique engine represented by the selected workspace(s),
    sync stores and layers for that workspace.
    """
    processed_engines = set()

    for workspace in queryset.select_related('geodata_engine'):
        engine = workspace.geodata_engine
        if not engine:
            modeladmin.message_user(
                request,
                f"[{workspace.name}] Skipped — no engine attached.",
                messages.WARNING,
            )
            continue

        # Avoid syncing the same engine more than once per action call
        if engine.pk in processed_engines:
            continue
        processed_engines.add(engine.pk)

        try:
            service = GeoServerSyncService(engine)
            store_result = service.sync_stores_for_workspace(
                workspace, created_by=request.user
            )
            layer_result = service.sync_layers_for_workspace(
                workspace, created_by=request.user
            )
        except GeoServerConnectionError as e:
            modeladmin.message_user(
                request,
                f"[{workspace.name}] Engine unreachable: {e}",
                messages.ERROR,
            )
            continue
        except GeodataEngineError as e:
            modeladmin.message_user(
                request,
                f"[{workspace.name}] Sync error: {e}",
                messages.ERROR,
            )
            continue

        st = store_result
        ly = layer_result
        modeladmin.message_user(
            request,
            (
                f"[{workspace.name}] Sync complete — "
                f"stores: +{st.get('created', 0)} / −{st.get('deleted', 0)} / ~{st.get('synced', 0)}, "
                f"layers: +{ly.get('created', 0)} / −{ly.get('deleted', 0)} / ~{ly.get('synced', 0)}."
            ),
            messages.SUCCESS,
        )
