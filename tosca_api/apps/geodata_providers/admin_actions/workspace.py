"""
Admin actions for Workspace.
    sync_workspaces — pull provider state into the local catalog for selected workspaces' engines
"""
from django.contrib import admin, messages

from ..engine_factory import EngineClientFactory
from ..exceptions import GeoServerConnectionError, GeodataEngineError


@admin.action(description="Sync selected workspaces into the local catalog")
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
            service = EngineClientFactory.create_sync_service(engine)
            results = service.sync_workspace_resources(workspace, created_by=request.user)
            store_result = results['stores']
            style_result = results['styles']
            layer_result = results['layers']
        except GeoServerConnectionError as e:
            modeladmin.message_user(
                request,
                f"[{workspace.name}] Provider unreachable: {e}",
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
        sy = style_result
        ly = layer_result
        modeladmin.message_user(
            request,
            (
                f"[{workspace.name}] Sync complete — "
                f"stores: +{st.get('created', 0)} / −{st.get('deleted', 0)} / ~{st.get('synced', 0)}, "
                f"styles: +{sy.get('created', 0)} / −{sy.get('deleted', 0)} / ~{sy.get('synced', 0)}, "
                f"layers: +{ly.get('created', 0)} / −{ly.get('deleted', 0)} / ~{ly.get('synced', 0)}. "
                "Provider state synced into the local catalog."
            ),
            messages.SUCCESS,
        )
