"""
Admin views for Workspace.
    workspace_sync_view  — POST …/<id>/sync/  → JSON
"""
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from ..engine_factory import EngineClientFactory
from ..exceptions import GeoServerConnectionError, GeodataEngineError
from ..models import Workspace


@require_POST
def workspace_sync_view(request, workspace_id):
    """
    POST /admin/geodata_providers/workspace/<id>/sync/
    Wrapped by admin_site.admin_view() in get_urls() — auth handled there.
    Syncs stores + layers for this workspace's engine.
    Returns JSON with store/layer delta counts.
    """
    if not request.user.is_staff:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    try:
        workspace = (
            Workspace.objects.select_related('geodata_engine')
            .filter(geodata_engine__is_active=True)
            .get(pk=workspace_id)
        )
    except Workspace.DoesNotExist:
        return JsonResponse({'error': 'Workspace not found.'}, status=404)

    engine = workspace.geodata_engine
    if not engine:
        return JsonResponse({'error': 'Workspace has no engine attached.'}, status=400)

    try:
        service = EngineClientFactory.create_sync_service(engine)
        results = service.sync_workspace_resources(workspace, created_by=request.user)
        store_result = results['stores']
        style_result = results['styles']
        layer_result = results['layers']
    except GeoServerConnectionError as e:
        return JsonResponse({'success': False, 'error': f'Provider unreachable: {e}'}, status=502)
    except GeodataEngineError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

    st_errors = store_result.get('errors', [])
    sy_errors = style_result.get('errors', [])
    ly_errors = layer_result.get('errors', [])
    success = len(st_errors) == 0 and len(sy_errors) == 0 and len(ly_errors) == 0

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
        'styles': {
            'created': style_result.get('created', 0),
            'updated': style_result.get('synced', 0),
            'deleted': style_result.get('deleted', 0),
        },
        'errors': st_errors + sy_errors + ly_errors,
    })
