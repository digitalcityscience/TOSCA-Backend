"""
Admin views for Layer (Phase 4).

    publish_postgis_view       — GET/POST /admin/geodata_providers/layer/publish-postgis/
    stores_for_workspace_view  — GET      /admin/geodata_providers/layer/stores-for-workspace/?workspace_id=<uuid>
    tables_for_store_view      — GET      /admin/geodata_providers/layer/tables-for-store/?store_id=<uuid>
"""
import logging

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from ..admin_forms import PublishPostGISForm
from ..engine_factory import EngineClientFactory
from ..exceptions import GeoServerConnectionError, GeoServerPublishError
from ..models import Layer, Store, Workspace
from ..postgis_inspector import PostGISInspectorError, get_geometry_tables, get_table_bbox
from ..services.commands.layer_service import LayerService

logger = logging.getLogger(__name__)


def publish_postgis_view(request):
    """
    GET  — render PublishPostGISForm
    POST — run full publish flow (mirrors LayerViewSet.publish_postgis)
           GeoServer-first: pre-check → publish → verify → Django persist
    """
    if not request.user.is_staff:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    if request.method == 'POST':
        form = PublishPostGISForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            workspace: Workspace = cd['workspace']
            store: Store = cd['store']
            table_name: str = cd['table_name']
            layer_name: str = (cd.get('layer_name') or table_name).strip()
            title: str = (cd.get('title') or layer_name).strip()
            description: str = cd.get('description', '')
            geometry_column: str = cd.get('geometry_column') or 'geom'
            geometry_type: str = cd['geometry_type']
            srid: int = cd['srid']

            try:
                result = LayerService.publish_postgis(
                    workspace=workspace,
                    store=store,
                    table_name=table_name,
                    layer_name=layer_name,
                    title=title,
                    description=description,
                    geometry_column=geometry_column,
                    geometry_type=geometry_type,
                    srid=srid,
                    user=request.user,
                )
            except GeoServerPublishError as exc:
                form.add_error(None, f'GeoServer publish failed: {exc}')
            except GeoServerConnectionError as exc:
                form.add_error(None, f'Engine unreachable: {exc}')
            except Exception as exc:
                form.add_error(None, str(exc))
            else:
                if not result.get('success'):
                    if result.get('error_code') == 'LAYER_ALREADY_EXISTS':
                        form.add_error('layer_name', result.get('error'))
                    elif result.get('error'):
                        form.add_error(None, result.get('error'))
                    else:
                        form.add_error(None, result.get('message', 'Layer publish failed.'))
                else:
                    messages.success(
                        request,
                        f"Layer '{layer_name}' published from table '{table_name}' "
                        f"in workspace '{workspace.name}'.",
                    )
                    try:
                        service = EngineClientFactory.create_sync_service(workspace.geodata_engine)
                        service.sync_styles_for_scope(workspace, created_by=request.user)
                        sync_result = service.sync_layers_for_workspace(workspace, created_by=request.user)
                        if sync_result.get('errors'):
                            messages.warning(
                                request,
                                f"Layer sync completed with issues: {' | '.join(sync_result.get('errors', [])[:2])}",
                            )
                        else:
                            messages.success(
                                request,
                                f"Workspace '{workspace.name}' layer sync completed.",
                            )
                    except Exception as exc:
                        messages.warning(
                            request,
                            f"Layer publish succeeded but sync failed: {exc}",
                        )
                    return redirect(reverse('admin:geodata_providers_layer_changelist'))
    else:
        form = PublishPostGISForm()

    return render(request, 'admin/geodata_providers/layer/publish_postgis.html', {
        'form': form,
        'title': 'Publish Layer from PostGIS',
        'opts': Layer._meta,
    })


def stores_for_workspace_view(request):
    """
    GET /admin/geodata_providers/layer/stores-for-workspace/?workspace_id=<uuid>
    Returns JSON list of stores belonging to the workspace.
    Used by PublishPostGISForm JS to filter store dropdown.
    """
    if not request.user.is_staff:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    workspace_id = request.GET.get('workspace_id', '').strip()
    if not workspace_id:
        return JsonResponse({'error': 'workspace_id is required.'}, status=400)

    try:
        stores = (
            Store.objects
            .filter(
                workspace_id=workspace_id,
                workspace__geodata_engine__is_active=True,
            )
            .values('id', 'name', 'store_type', 'host', 'schema')
            .order_by('name')
        )
        return JsonResponse({'stores': list(stores)})
    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)


def tables_for_store_view(request):
    """
    GET /admin/geodata_providers/layer/tables-for-store/?store_id=<uuid>[&workspace_id=<uuid>]
    Returns JSON list of geometry tables/views available in the selected store.
    Used by PublishPostGISForm JS to populate the table dropdown and geometry metadata.
    """
    if not request.user.is_staff:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    store_id = request.GET.get('store_id', '').strip()
    workspace_id = request.GET.get('workspace_id', '').strip()
    if not store_id:
        return JsonResponse({'error': 'store_id is required.'}, status=400)

    try:
        store = (
            Store.objects.select_related('workspace__geodata_engine')
            .filter(workspace__geodata_engine__is_active=True)
            .get(pk=store_id)
        )
    except Store.DoesNotExist:
        return JsonResponse({'error': 'Store not found.'}, status=404)

    if workspace_id and str(store.workspace_id) != workspace_id:
        return JsonResponse({'error': 'Store does not belong to the selected workspace.'}, status=400)

    if store.store_type != 'postgis':
        return JsonResponse(
            {'error': f"Store type '{store.store_type}' does not support PostGIS table preview."},
            status=400,
        )

    geoserver_available = []
    geoserver_error = ''
    engine = store.workspace.geodata_engine if store.workspace else None
    if engine:
        try:
            client = EngineClientFactory.create_client(engine)
            geoserver_available = client.get_available_featuretypes(
                workspace=store.workspace.name,
                store_name=store.name,
            )
        except (GeoServerConnectionError, GeoServerPublishError) as exc:
            geoserver_error = str(exc)
        except Exception as exc:
            geoserver_error = str(exc)

    try:
        usable_password = store.decrypted_password
    except (ValueError, Exception):
        usable_password = ''

    if not usable_password:
        return JsonResponse(
            {
                'store': {
                    'id': str(store.pk),
                    'name': store.name,
                    'workspace_id': str(store.workspace_id) if store.workspace_id else '',
                    'schema': store.schema or 'public',
                },
                'tables': [],
                'geoserver_available': [
                    {'table_name': name}
                    for name in geoserver_available
                ],
                'warning': 'Set connection credentials first to inspect PostGIS geometry metadata.',
                'geoserver_error': geoserver_error,
            }
        )

    try:
        tables = get_geometry_tables(
            host=store.host,
            port=store.port or 5432,
            database=store.database,
            username=store.username,
            password=usable_password,
            schema=store.schema or 'public',
        )
    except PostGISInspectorError as exc:
        return JsonResponse({'error': f'PostGIS connection failed: {exc}'}, status=502)
    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)

    seen_table_names = {table.get('table_name') for table in tables if table.get('table_name')}
    geoserver_only = [
        {'table_name': name}
        for name in geoserver_available
        if name not in seen_table_names
    ]

    return JsonResponse(
        {
            'store': {
                'id': str(store.pk),
                'name': store.name,
                'workspace_id': str(store.workspace_id) if store.workspace_id else '',
                'schema': store.schema or 'public',
            },
            'tables': tables,
            'geoserver_available': geoserver_only,
            'geoserver_error': geoserver_error,
        }
    )
