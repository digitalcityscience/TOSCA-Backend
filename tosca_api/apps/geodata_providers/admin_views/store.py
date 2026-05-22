"""
Admin views for Store.
    store_postgis_tables_view  — GET …/<id>/postgis-tables/  → JSON
    store_clone_view           — GET/POST …/<id>/clone/      → HTML form
"""
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from ..engine_factory import EngineClientFactory
from ..exceptions import GeodataEngineError
from ..models import Store, Workspace
from ..postgis_inspector import PostGISInspectorError, get_geometry_tables
from ..services.commands.store_service import StoreService


@require_GET
def store_postgis_tables_view(request, store_id):
    """
    GET /admin/geodata_providers/store/<id>/postgis-tables/
    Wrapped by admin_site.admin_view() — auth handled there.
    Decrypts credentials from Store, queries PostGIS geometry_columns view,
    returns JSON list of geometry tables.
    """
    if not request.user.is_staff:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    try:
        store = (
            Store.objects.select_related('workspace__geodata_engine')
            .filter(workspace__geodata_engine__is_active=True)
            .get(pk=store_id)
        )
    except Store.DoesNotExist:
        return JsonResponse({'error': 'Store not found.'}, status=404)

    if store.store_type != 'postgis':
        return JsonResponse(
            {'error': f"Store type '{store.store_type}' does not support PostGIS table preview."},
            status=400,
        )

    # Use decrypted_password — NOT store.password — to catch the case where an
    # encrypted token exists but decrypt fails (corrupt key / wrong key rotation).
    # Matches the validation logic used by the provider API path (§3.10.2).
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
    GET  /admin/geodata_providers/store/<id>/clone/ — render pre-filled clone form
    POST /admin/geodata_providers/store/<id>/clone/ — validate → GeoServer → Django → redirect

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
    from tosca_api.apps.geodata_providers.admin import StoreCloneForm

    source = get_object_or_404(
        Store.objects.select_related('workspace__geodata_engine').filter(
            workspace__geodata_engine__is_active=True,
        ),
        pk=store_id,
    )

    if request.method == 'POST':
        form = StoreCloneForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            target_ws: Workspace = cd['workspace']
            new_name: str = cd['name']
            result = StoreService.clone_store(
                source_store=source,
                target_workspace=target_ws,
                name=new_name,
                user=request.user,
                description=cd.get('description') or '',
                host=cd.get('host') or source.host,
                port=cd.get('port') or source.port or 5432,
                database=cd.get('database') or source.database,
                username=cd.get('username') or source.username,
                password=cd['password'],
                schema=cd.get('schema') or source.schema or 'public',
            )
            if result.get('already_exists'):
                form.add_error('name', f"A store named '{new_name}' already exists in workspace '{target_ws.name}'.")
            elif not result.get('success', True):
                form.add_error(None, result.get('error') or result.get('message') or 'Store clone failed.')
            else:
                new_store = result['resource']
                messages.success(
                    request,
                    f"Store '{new_name}' cloned from '{source.name}' "
                    f"into workspace '{target_ws.name}' successfully.",
                )
                sync_result = result.get('sync_result', {})
                if sync_result.get('errors'):
                    messages.warning(
                        request,
                        f"Store sync completed with issues: {' | '.join(sync_result.get('errors', [])[:2])}",
                    )
                elif sync_result.get('success'):
                    messages.success(
                        request,
                        f"Workspace '{target_ws.name}' store sync completed.",
                    )
                elif sync_result.get('error'):
                    messages.warning(
                        request,
                        f"Store clone succeeded but sync failed: {sync_result.get('error')}",
                    )
                return redirect(reverse('admin:geodata_providers_store_change', args=[new_store.pk]))
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

    return render(request, 'admin/geodata_providers/store/clone.html', {
        'form': form,
        'source': source,
        'title': f'Clone store: {source.name}',
        'opts': Store._meta,
    })
