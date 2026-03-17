"""
Admin views for GeodataEngine.
    engine_test_connection_view  — POST …/<id>/test-connection/  → JSON
    engine_sync_view             — POST …/<id>/sync/             → JSON
"""
import time

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from ..engine_factory import EngineClientFactory
from ..exceptions import GeoServerConnectionError, GeodataEngineError
from ..models import GeodataEngine
from ..sync_service import GeoServerSyncService


@require_POST
def engine_test_connection_view(request, engine_id):
    """
    POST /admin/geodata_engine/geodataengine/<id>/test-connection/
    Wrapped by admin_site.admin_view() in get_urls() — auth handled there.
    Returns JSON: {success, version, latency_ms} or {success: false, error}.
    """
    if not request.user.is_staff:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    try:
        engine = GeodataEngine.objects.get(pk=engine_id)
    except GeodataEngine.DoesNotExist:
        return JsonResponse({'error': 'Engine not found.'}, status=404)

    try:
        client = EngineClientFactory.create_client(engine)
        t0 = time.monotonic()
        result = client.validate_connection()
        latency_ms = int((time.monotonic() - t0) * 1000)
        return JsonResponse({
            'success': True,
            'version': result.get('version') or 'unknown',
            'latency_ms': latency_ms,
        })
    except GeoServerConnectionError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=502)
    except GeodataEngineError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
def engine_sync_view(request, engine_id):
    """
    POST /admin/geodata_engine/geodataengine/<id>/sync/
    Wrapped by admin_site.admin_view() in get_urls() — auth handled there.
    Returns JSON with workspace/store/layer delta counts.
    """
    if not request.user.is_staff:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    try:
        engine = GeodataEngine.objects.get(pk=engine_id)
    except GeodataEngine.DoesNotExist:
        return JsonResponse({'error': 'Engine not found.'}, status=404)

    try:
        service = GeoServerSyncService(engine)
        result = service.sync_all_resources(created_by=request.user)
    except GeoServerConnectionError as e:
        return JsonResponse({'success': False, 'error': f'Engine unreachable: {e}'}, status=502)
    except GeodataEngineError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

    if not result.get('success'):
        return JsonResponse(
            {'success': False, 'error': result.get('error', 'Sync failed.')},
            status=500,
        )

    ws = result.get('workspaces', {})
    st = result.get('stores', {})
    ly = result.get('layers', {})
    return JsonResponse({
        'success': True,
        'workspaces': {'created': ws.get('created', 0), 'deleted': ws.get('deleted', 0), 'synced': ws.get('synced', 0)},
        'stores':     {'created': st.get('created', 0), 'deleted': st.get('deleted', 0), 'synced': st.get('synced', 0)},
        'layers':     {'created': ly.get('created', 0), 'deleted': ly.get('deleted', 0), 'synced': ly.get('synced', 0)},
    })
