"""
Admin views for Layer (Phase 4).

    publish_postgis_view       — GET/POST /admin/geodata_engine/layer/publish-postgis/
    stores_for_workspace_view  — GET      /admin/geodata_engine/layer/stores-for-workspace/?workspace_id=<uuid>
"""
import logging

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from ..admin_forms import PublishPostGISForm
from ..engine_factory import EngineClientFactory
from ..exceptions import GeoServerConnectionError, GeoServerPublishError
from ..models import Layer, Store, Workspace
from ..postgis_inspector import PostGISInspectorError, get_table_bbox

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
            layer_name: str = cd['layer_name']
            title: str = cd['title']
            description: str = cd.get('description', '')
            geometry_column: str = cd.get('geometry_column') or 'geom'
            geometry_type: str = cd['geometry_type']
            srid: int = cd['srid']

            engine = workspace.geodata_engine
            if not engine:
                form.add_error('workspace', 'This workspace has no associated GeoServer engine.')
            else:
                client = EngineClientFactory.create_client(engine)

                # Step 1: pre-check — table already published?
                try:
                    already_published = client.verify_featuretype(
                        workspace=workspace.name,
                        store_name=store.name,
                        table_name=table_name,
                    )
                except (GeoServerConnectionError, GeoServerPublishError) as exc:
                    form.add_error(None, f'Cannot check existing publications: {exc}')
                    already_published = False

                if already_published:
                    form.add_error(
                        'table_name',
                        f"Table '{table_name}' is already published in workspace '{workspace.name}'. "
                        f"Delete the existing layer first.",
                    )
                else:
                    # Step 2: Optional — retrieve bbox (non-fatal)
                    bbox = None
                    if store.store_type == 'postgis' and store.host:
                        try:
                            bbox = get_table_bbox(
                                host=store.host,
                                port=store.port or 5432,
                                database=store.database,
                                username=store.username,
                                password=store.decrypted_password,
                                schema=store.schema or 'public',
                                table=table_name,
                                geometry_column=geometry_column,
                            )
                        except (PostGISInspectorError, Exception) as exc:
                            logger.warning('Could not retrieve bbox for %s.%s: %s', store.schema, table_name, exc)

                    # Step 3: Publish in GeoServer
                    try:
                        client.publish_featuretype(
                            store_name=store.name,
                            workspace=workspace.name,
                            pg_table=table_name,
                            srid=srid,
                            geometry_type=geometry_type,
                            layer_name=layer_name,
                        )
                    except GeoServerPublishError as exc:
                        form.add_error(None, f'GeoServer publish failed: {exc}')
                    except GeoServerConnectionError as exc:
                        form.add_error(None, f'Engine unreachable: {exc}')
                    else:
                        # Step 4: Verify in GeoServer
                        verified = client.verify_featuretype(
                            workspace=workspace.name,
                            store_name=store.name,
                            table_name=table_name,
                        )
                        if not verified:
                            form.add_error(
                                None,
                                'Publish reported success but layer could not be verified in GeoServer.',
                            )
                        else:
                            # Step 5: Persist in Django
                            layer, created = Layer.objects.get_or_create(
                                workspace=workspace,
                                name=layer_name,  # Changed from table_name to layer_name
                                defaults={
                                    'store': store,
                                    'title': title,
                                    'description': description,
                                    'table_name': table_name,
                                    'geometry_column': geometry_column,
                                    'geometry_type': geometry_type,
                                    'srid': srid,
                                    'is_public': True,
                                    'publishing_state': 'PUBLISHED',
                                    'published_url': '',
                                    'published_at': timezone.now(),
                                    'created_by': request.user,
                                },
                            )
                            if not created:
                                Layer.objects.filter(pk=layer.pk).update(
                                    publishing_state='PUBLISHED',
                                    published_url='',
                                    published_at=timezone.now(),
                                    publishing_error='',
                                )

                            messages.success(
                                request,
                                f"Layer '{layer_name}' published from table '{table_name}' "
                                f"in workspace '{workspace.name}'.",
                            )
                            return redirect('/admin/geodata_engine/layer/')
    else:
        form = PublishPostGISForm()

    return render(request, 'admin/geodata_engine/layer/publish_postgis.html', {
        'form': form,
        'title': 'Publish Layer from PostGIS',
        'opts': Layer._meta,
    })


def stores_for_workspace_view(request):
    """
    GET /admin/geodata_engine/layer/stores-for-workspace/?workspace_id=<uuid>
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
            .filter(workspace_id=workspace_id)
            .values('id', 'name', 'store_type', 'host', 'schema')
            .order_by('name')
        )
        return JsonResponse({'stores': list(stores)})
    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)
