"""
Admin views for Workspace.
    workspace_sync_view  — POST …/<id>/sync/  → JSON
"""
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from ..exceptions import GeoServerConnectionError, GeodataEngineError
from ..models import Workspace
from ..sync_service import GeoServerSyncService


@require_POST
def workspace_sync_view(request, workspace_id):
    """
    POST /admin/geodata_engine/workspace/<id>/sync/
    Wrapped by admin_site.admin_view() in get_urls() — auth handled there.
    Syncs stores + layers for this workspace's engine.
    Returns JSON with store/layer delta counts.
    """
    if not request.user.is_staff:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    try:
        workspace = Workspace.objects.select_related('geodata_engine').get(pk=workspace_id)
    except Workspace.DoesNotExist:
        return JsonResponse({'error': 'Workspace not found.'}, status=404)

    engine = workspace.geodata_engine
    if not engine:
        return JsonResponse({'error': 'Workspace has no engine attached.'}, status=400)

    try:
        service = GeoServerSyncService(engine)
        store_result = service.sync_stores_for_workspace(workspace, created_by=request.user)
        layer_result = service.sync_layers_for_workspace(workspace, created_by=request.user)
    except GeoServerConnectionError as e:
        return JsonResponse({'success': False, 'error': f'Engine unreachable: {e}'}, status=502)
    except GeodataEngineError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

    st_errors = store_result.get('errors', [])
    ly_errors = layer_result.get('errors', [])
    success = len(st_errors) == 0 and len(ly_errors) == 0

    return JsonResponse({
        'success': success,
        'stores': {
            'created': store_result.get('created', 0),
            'updated': store_result.get('synced', 0),
            'deleted': store_result.get('deleted', 0),
        },
        'layers': {
            'created': layer_result.get('created', 0),
            'updated': layer_result.get('synced', 0),
            'deleted': layer_result.get('deleted', 0),
        },
        'errors': st_errors + ly_errors,
    })
