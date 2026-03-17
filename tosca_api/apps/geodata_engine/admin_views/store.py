"""
Admin views for Store.
    store_postgis_tables_view  — GET …/<id>/postgis-tables/  → JSON
    store_clone_view           — GET/POST …/<id>/clone/      → HTML form
"""
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET

from ..engine_factory import EngineClientFactory
from ..exceptions import GeoServerConnectionError, GeoServerPublishError, GeodataEngineError
from ..models import Store, Workspace
from ..postgis_inspector import PostGISInspectorError, get_geometry_tables


@require_GET
def store_postgis_tables_view(request, store_id):
    """
    GET /admin/geodata_engine/store/<id>/postgis-tables/
    Wrapped by admin_site.admin_view() — auth handled there.
    Decrypts credentials from Store, queries PostGIS geometry_columns view,
    returns JSON list of geometry tables.
    """
    if not request.user.is_staff:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    try:
        store = Store.objects.select_related('workspace__geodata_engine').get(pk=store_id)
    except Store.DoesNotExist:
        return JsonResponse({'error': 'Store not found.'}, status=404)

    if store.store_type != 'postgis':
        return JsonResponse(
            {'error': f"Store type '{store.store_type}' does not support PostGIS table preview."},
            status=400,
        )

    # Use decrypted_password — NOT store.password — to catch the case where an
    # encrypted token exists but decrypt fails (corrupt key / wrong key rotation).
    # Matches the check in StoreViewSet.postgis_tables (geo_console path, §3.10.2).
    try:
        usable_password = store.decrypted_password
    except (ValueError, Exception):
        usable_password = ''

    if not usable_password:
        return JsonResponse(
            {'error': 'no_password', 'message': 'Set connection credentials first.'},
            status=400,
        )

    try:
        tables = get_geometry_tables(
            host=store.host,
            port=store.port or 5432,
            database=store.database,
            username=store.username,
            password=usable_password,   # already decrypted + validated above
            schema=store.schema or 'public',
        )
    except PostGISInspectorError as e:
        return JsonResponse({'error': f'PostGIS connection failed: {e}'}, status=502)
    except GeodataEngineError as e:
        return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'tables': tables})


def store_clone_view(request, store_id):
    """
    GET  /admin/geodata_engine/store/<id>/clone/ — render pre-filled clone form
    POST /admin/geodata_engine/store/<id>/clone/ — validate → GeoServer → Django → redirect

    Clone sequence (CREATE sync philosophy):
        1. Check if store name already exists in target workspace (engine + Django)
        2. Create in GeoServer
        3. Verify creation
        4. Persist in Django
    """
    if not request.user.is_staff:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    # Import form here to avoid circular import (form lives in admin.py)
    from tosca_api.apps.geodata_engine.admin import StoreCloneForm

    source = get_object_or_404(
        Store.objects.select_related('workspace__geodata_engine'),
        pk=store_id,
    )

    if request.method == 'POST':
        form = StoreCloneForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            target_ws: Workspace = cd['workspace']
            new_name: str = cd['name']
            engine = target_ws.geodata_engine

            # ── 1. Check Django uniqueness ────────────────────────────
            if Store.objects.filter(workspace=target_ws, name=new_name).exists():
                form.add_error('name', f"A store named '{new_name}' already exists in workspace '{target_ws.name}'.")
            else:
                # ── 2. Create in GeoServer (PostGIS only) ─────────────
                engine_result = {'success': True, 'message': 'No engine attached — DB only.'}
                if engine and source.store_type == 'postgis':
                    try:
                        client = EngineClientFactory.create_client(engine)
                        engine_result = client.create_postgis_store(
                            name=new_name,
                            workspace=target_ws.name,
                            host=cd.get('host') or source.host,
                            port=cd.get('port') or source.port or 5432,
                            database=cd.get('database') or source.database,
                            username=cd.get('username') or source.username,
                            password=cd['password'],
                            schema=cd.get('schema') or source.schema or 'public',
                        )
                    except GeoServerPublishError as e:
                        form.add_error(None, f'GeoServer create failed: {e}')
                        return render(request, 'admin/geodata_engine/store/clone.html', {
                            'form': form,
                            'source': source,
                            'title': f'Clone store: {source.name}',
                            'opts': Store._meta,
                        })
                    except GeoServerConnectionError as e:
                        form.add_error(None, f'Engine unreachable: {e}')
                        return render(request, 'admin/geodata_engine/store/clone.html', {
                            'form': form,
                            'source': source,
                            'title': f'Clone store: {source.name}',
                            'opts': Store._meta,
                        })

                if not engine_result.get('success', True):
                    form.add_error(None, f"GeoServer error: {engine_result.get('error', 'unknown')}")
                else:
                    # ── 3. Persist in Django ───────────────────────────
                    new_store = Store.objects.create(
                        workspace=target_ws,
                        geodata_engine=engine,
                        name=new_name,
                        store_type=source.store_type,
                        host=cd.get('host') or source.host,
                        port=cd.get('port') or source.port or 5432,
                        database=cd.get('database') or source.database,
                        username=cd.get('username') or source.username,
                        password=cd['password'],
                        schema=cd.get('schema') or source.schema or 'public',
                        file_path=source.file_path,
                        charset=source.charset,
                        description=cd.get('description') or '',
                        created_by=request.user,
                    )
                    messages.success(
                        request,
                        f"Store '{new_name}' cloned from '{source.name}' "
                        f"into workspace '{target_ws.name}' successfully.",
                    )
                    return redirect(
                        f'/admin/geodata_engine/store/{new_store.pk}/change/'
                    )
    else:
        # Pre-fill from source, blank out name and password
        form = StoreCloneForm(initial={
            'name': f'{source.name}_copy',
            'workspace': source.workspace,
            'description': source.description,
            'host': source.host,
            'port': source.port,
            'database': source.database,
            'schema': source.schema,
            'username': source.username,
            # password intentionally not pre-filled
        })

    return render(request, 'admin/geodata_engine/store/clone.html', {
        'form': form,
        'source': source,
        'title': f'Clone store: {source.name}',
        'opts': Store._meta,
    })
