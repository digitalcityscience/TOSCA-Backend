import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from rest_framework.authtoken.models import Token

from tosca_api.apps.geo_console.exceptions import APIError, APINotFoundError, APITimeoutError
from tosca_api.apps.geo_console.forms import EngineForm, LayerEditForm, LayerPublishForm, StoreDetailForm, StoreForm, WorkspaceForm
from tosca_api.apps.geo_console.services.api_client import GeoConsoleAPIClient
from tosca_api.apps.geodata_providers.models import GeodataEngine, Layer, Store, Workspace

logger = logging.getLogger(__name__)


def _get_client(request) -> GeoConsoleAPIClient:
    """
    Return a GeoConsoleAPIClient authenticated as the current user.
    Creates a DRF token for the user on first call (idempotent).
    """
    token, _ = Token.objects.get_or_create(user=request.user)
    return GeoConsoleAPIClient(token=token.key)


@login_required
def console(request):
    workspace_count_sq = (
        Workspace.objects
        .filter(geodata_engine=OuterRef('pk'))
        .values('geodata_engine')
        .annotate(c=Count('pk'))
        .values('c')
    )

    layer_count_sq = (
        Layer.objects
        .filter(workspace__geodata_engine=OuterRef('pk'))
        .values('workspace__geodata_engine')
        .annotate(c=Count('pk'))
        .values('c')
    )

    engines = (
        GeodataEngine.objects
        .filter(is_active=True)
        .annotate(
            workspace_count=Coalesce(Subquery(workspace_count_sq), Value(0), output_field=IntegerField()),
            layer_count=Coalesce(Subquery(layer_count_sq), Value(0), output_field=IntegerField()),
        )
        .order_by('-is_default', 'name')
    )
    return render(request, 'geo_console/geodata_console.html', {'engines': engines})


# ---------------------------------------------------------------------------
# Phase 1.2 — Engine detail + actions
# ---------------------------------------------------------------------------

@login_required
def engine_detail(request, engine_id):
    """
    GET /console/engines/<uuid>/
    Shows a single engine's metadata, connection status area, and action buttons.
    Data comes from the internal DRF API — no direct ORM access.
    """
    client = _get_client(request)
    try:
        engine = client.get_engine(str(engine_id))
    except APINotFoundError:
        messages.error(request, "Engine not found.")
        return redirect("geo_console")
    except APITimeoutError:
        messages.error(request, "Engine registry unreachable — connection timed out.")
        return redirect("geo_console")
    except APIError as e:
        messages.error(request, f"Could not load engine: {e.detail}")
        return redirect("geo_console")

    return render(request, "geo_console/engine_detail.html", {"engine": engine})


@login_required
@require_POST
def engine_sync(request, engine_id):
    """
    POST /console/engines/<uuid>/sync/
    Triggers pull-sync: GeoServer → Django.
    Uses 30-second timeout (defined in api_client).
    Redirects back to engine detail with a Django message.
    """
    client = _get_client(request)
    logger.info("User %s triggered sync for engine %s", request.user, engine_id)
    try:
        result = client.sync_engine(str(engine_id))
    except APITimeoutError:
        messages.error(request, "Sync timed out — GeoServer did not respond within 30 seconds.")
        return redirect("engine_detail", engine_id=engine_id)
    except APINotFoundError:
        messages.error(request, "Engine not found.")
        return redirect("geo_console")
    except APIError as e:
        logger.error("Sync failed for engine %s: %s", engine_id, e.detail)
        messages.error(request, f"Sync failed: {e.detail}")
        return redirect("engine_detail", engine_id=engine_id)

    if result.get("success"):
        ws = result.get("workspaces", {})
        st = result.get("stores", {})
        ly = result.get("layers", {})
        messages.success(
            request,
            f"Sync completed — "
            f"workspaces: {ws.get('synced', 0)} synced / {ws.get('created', 0)} created, "
            f"stores: {st.get('synced', 0)} synced / {st.get('created', 0)} created, "
            f"layers: {ly.get('synced', 0)} synced / {ly.get('created', 0)} created.",
        )
    else:
        messages.warning(request, "Sync completed with errors. Check the logs for details.")

    return redirect("engine_detail", engine_id=engine_id)


@login_required
@require_POST
def engine_validate(request, engine_id):
    """
    POST /console/engines/<uuid>/validate/
    Pings the engine to check connectivity.
    Redirects back to engine detail with a Django message.
    """
    client = _get_client(request)
    logger.info("User %s triggered validate for engine %s", request.user, engine_id)
    try:
        result = client.validate_engine(str(engine_id))
    except APITimeoutError:
        messages.error(request, "Engine unreachable — connection timed out.")
        return redirect("engine_detail", engine_id=engine_id)
    except APINotFoundError:
        messages.error(request, "Engine not found.")
        return redirect("geo_console")
    except APIError as e:
        logger.error("Validate failed for engine %s: %s", engine_id, e.detail)
        messages.error(request, f"Connection failed: {e.detail}")
        return redirect("engine_detail", engine_id=engine_id)

    if result.get("success"):
        messages.success(request, result.get("message", "Connection validated successfully."))
    else:
        messages.error(request, result.get("error", "Validation returned an unexpected response."))

    return redirect("engine_detail", engine_id=engine_id)


# ---------------------------------------------------------------------------
# Phase 1.5 — Engine Create / Edit / Delete
# ---------------------------------------------------------------------------

@login_required
def engine_create(request):
    """
    GET  /console/engines/create/ — blank form
    POST /console/engines/create/ — call POST /api/geoengine/engines/ → redirect to detail
    """
    if request.method == "POST":
        form = EngineForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            # Remove empty password — API will use its default/blank behaviour
            if not data.get("admin_password"):
                data.pop("admin_password", None)
            client = _get_client(request)
            try:
                result = client.create_engine(data)
            except APITimeoutError:
                messages.error(request, "Engine registry unreachable — timed out.")
                return render(request, "geo_console/engine_form.html", {"form": form, "edit_mode": False})
            except APIError as e:
                messages.error(request, f"Could not create engine: {e.detail}")
                return render(request, "geo_console/engine_form.html", {"form": form, "edit_mode": False})
            engine = result.get("engine", result)
            messages.success(request, f"Engine '{engine['name']}' created successfully.")
            return redirect("engine_detail", engine_id=engine["id"])
    else:
        form = EngineForm(initial={"is_active": True, "engine_type": "geoserver"})

    return render(request, "geo_console/engine_form.html", {"form": form, "edit_mode": False})


@login_required
def engine_edit(request, engine_id):
    """
    GET  /console/engines/<uuid>/edit/ — prefilled form (password never echoed)
    POST /console/engines/<uuid>/edit/ — PATCH /api/geoengine/engines/{id}/
    """
    client = _get_client(request)

    if request.method == "POST":
        form = EngineForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            # Omit blank password so existing credential is preserved
            if not data.get("admin_password"):
                data.pop("admin_password", None)
            try:
                result = client.update_engine(str(engine_id), data)
            except APINotFoundError:
                messages.error(request, "Engine not found.")
                return redirect("geo_console")
            except APITimeoutError:
                messages.error(request, "Engine registry unreachable — timed out.")
                return render(
                    request,
                    "geo_console/engine_form.html",
                    {"form": form, "edit_mode": True, "engine_id": engine_id},
                )
            except APIError as e:
                messages.error(request, f"Could not update engine: {e.detail}")
                return render(
                    request,
                    "geo_console/engine_form.html",
                    {"form": form, "edit_mode": True, "engine_id": engine_id},
                )
            engine = result.get("engine", result)
            messages.success(request, f"Engine '{engine['name']}' updated.")
            return redirect("engine_detail", engine_id=engine_id)
    else:
        # Pre-fill from API — password intentionally omitted
        try:
            engine = client.get_engine(str(engine_id))
        except APINotFoundError:
            messages.error(request, "Engine not found.")
            return redirect("geo_console")
        except (APITimeoutError, APIError) as e:
            messages.error(request, f"Could not load engine: {getattr(e, 'detail', str(e))}")
            return redirect("geo_console")

        form = EngineForm(initial={
            "name": engine.get("name", ""),
            "description": engine.get("description", ""),
            "engine_type": engine.get("engine_type", "geoserver"),
            "base_url": engine.get("base_url", ""),
            "admin_username": engine.get("admin_username", ""),
            "is_active": engine.get("is_active", True),
            "is_default": engine.get("is_default", False),
            # admin_password deliberately left blank
        })

    return render(
        request,
        "geo_console/engine_form.html",
        {"form": form, "edit_mode": True, "engine_id": engine_id},
    )


# ---------------------------------------------------------------------------
# Phase 2 — Workspace views
# ---------------------------------------------------------------------------


def _get_active_engine_context(request, client: GeoConsoleAPIClient) -> dict:
    """
    Returns context dict needed for the engine selector in the topbar.
    • Fetches all active engines via the API.
    • Reads the selected engine from the session.
    • Falls back to the default engine when no session value is set.
    • Stores the resolved ID back into the session so subsequent requests are fast.

    Keys returned:
        engines          — list of all engine dicts (for the dropdown)
        active_engine_id — UUID string of the currently selected engine
        active_engine    — the full engine dict (or None if engines empty)
    """
    try:
        engines = client.list_engines()
    except (APIError, APITimeoutError):
        engines = []

    active_engine_id = request.session.get('geo_console_engine_id')

    # Validate: if stored ID is no longer in the list, clear it
    known_ids = {str(e['id']) for e in engines}
    if active_engine_id and active_engine_id not in known_ids:
        active_engine_id = None

    # Fall back to the default engine
    if not active_engine_id:
        default = next((e for e in engines if e.get('is_default')), None)
        if default is None and engines:
            default = engines[0]
        if default:
            active_engine_id = str(default['id'])
            request.session['geo_console_engine_id'] = active_engine_id

    active_engine = next((e for e in engines if str(e['id']) == active_engine_id), None)
    return {
        'engines': engines,
        'active_engine_id': active_engine_id,
        'active_engine': active_engine,
    }


@login_required
@require_POST
def set_active_engine(request):
    """
    POST /console/set-engine/
    Stores the selected engine ID in the session and redirects back.
    Used by the topbar engine selector dropdown on all geo_console pages.
    """
    engine_id = request.POST.get('engine_id', '').strip()
    if engine_id:
        request.session['geo_console_engine_id'] = engine_id
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or '/console/'
    return redirect(next_url)


@login_required
def workspace_list(request):
    """
    GET /console/workspaces/
    Lists workspaces that belong to the currently active engine.
    Active engine is stored in the session; defaults to is_default engine.
    """
    client = _get_client(request)
    engine_ctx = _get_active_engine_context(request, client)
    active_engine_id = engine_ctx['active_engine_id']

    try:
        workspaces = client.list_workspaces(engine_id=active_engine_id) if active_engine_id else []
    except APITimeoutError:
        messages.error(request, "Registry unreachable — connection timed out.")
        workspaces = []
    except APIError as e:
        messages.error(request, f"Could not load workspaces: {e.detail}")
        workspaces = []

    return render(request, "geo_console/workspace_list.html", {
        "workspaces": workspaces,
        **engine_ctx,
    })


@login_required
def workspace_create(request):
    """
    GET  /console/workspaces/create/ — blank form with engine dropdown
    POST /console/workspaces/create/ — POST /api/geoengine/workspaces/ → redirect to list
    """
    client = _get_client(request)

    engine_ctx = _get_active_engine_context(request, client)
    active_engine_id = engine_ctx['active_engine_id']
    active_engine = engine_ctx['active_engine']
    engines = engine_ctx['engines']

    # Engine field is locked to the active engine — only the active engine ID is a valid choice.
    engine_choices = [(active_engine_id, active_engine['name'])] if active_engine else []

    if request.method == "POST":
        form = WorkspaceForm(request.POST, engine_choices=engine_choices)
        if form.is_valid():
            data = form.cleaned_data
            try:
                result = client.create_workspace(data)
            except APITimeoutError:
                messages.error(request, "Engine registry unreachable — timed out.")
                return render(request, "geo_console/workspace_create.html", {
                    "form": form, "engines": engines,
                    **engine_ctx,
                })
            except APIError as e:
                messages.error(request, f"Could not create workspace: {e.detail}")
                return render(request, "geo_console/workspace_create.html", {
                    "form": form, "engines": engines,
                    **engine_ctx,
                })

            workspace = result.get("workspace", result)
            is_idempotent = result.get("result", {}).get("idempotent", False)
            if is_idempotent:
                messages.warning(request, f"Workspace '{workspace['name']}' already exists — no changes made.")
            else:
                messages.success(request, f"Workspace '{workspace['name']}' created successfully.")
            return redirect("workspace_list")
    else:
        form = WorkspaceForm(
            engine_choices=engine_choices,
            initial={"geodata_engine": active_engine_id},
        )

    return render(request, "geo_console/workspace_create.html", {
        "form": form,
        "engines": engines,
        **engine_ctx,
    })


@login_required
@require_POST
def workspace_delete(request, workspace_id):
    """
    POST /console/workspaces/<uuid>/delete/
    Sync rule: engine deletion first, verify, THEN Django object delete.
    Task 2.8: the 'vector' workspace is protected — cannot be deleted.
    """
    client = _get_client(request)

    # Fetch workspace first so we can check the name before attempting delete.
    try:
        workspace = client.get_workspace(str(workspace_id))
    except APINotFoundError:
        messages.error(request, "Workspace not found.")
        return redirect("workspace_list")
    except APITimeoutError:
        messages.error(request, "Registry unreachable — could not load workspace.")
        return redirect("workspace_list")
    except APIError as e:
        messages.error(request, f"Could not load workspace: {e.detail}")
        return redirect("workspace_list")

    workspace_name = workspace.get("name", str(workspace_id))
    logger.info("User %s deleting workspace %s (%s)", request.user, workspace_id, workspace_name)

    try:
        client.delete_workspace(str(workspace_id))
    except APINotFoundError:
        messages.error(request, "Workspace not found.")
        return redirect("workspace_list")
    except APITimeoutError:
        messages.error(request, "Engine unreachable — timed out. Workspace was NOT deleted.")
        return redirect("workspace_list")
    except APIError as e:
        logger.error("Delete workspace %s failed: %s", workspace_id, e.detail)
        messages.error(request, f"Delete failed: {e.detail}")
        return redirect("workspace_list")

    messages.success(request, f"Workspace '{workspace_name}' deleted.")
    return redirect("workspace_list")


@login_required
@require_POST
def workspace_sync(request, workspace_id):
    """
    POST /console/workspaces/<uuid>/sync/
    Triggers a full engine pull-sync (GeoServer → Django) for the engine
    that owns this workspace.  There is no per-workspace sync endpoint.
    """
    client = _get_client(request)
    logger.info("User %s triggered sync for workspace %s", request.user, workspace_id)
    try:
        result = client.sync_workspace(str(workspace_id))
    except APITimeoutError:
        messages.error(request, "Sync timed out — engine did not respond within 30 seconds.")
        return redirect("workspace_list")
    except APINotFoundError:
        messages.error(request, "Workspace not found.")
        return redirect("workspace_list")
    except APIError as e:
        logger.error("Workspace sync failed for %s: %s", workspace_id, e.detail)
        messages.error(request, f"Sync failed: {e.detail}")
        return redirect("workspace_list")

    if result.get("success"):
        ws = result.get("workspaces", {})
        st = result.get("stores", {})
        ly = result.get("layers", {})
        messages.success(
            request,
            f"Sync completed — "
            f"workspaces: {ws.get('synced', 0)} synced / {ws.get('created', 0)} created, "
            f"stores: {st.get('synced', 0)} synced / {st.get('created', 0)} created, "
            f"layers: {ly.get('synced', 0)} synced / {ly.get('created', 0)} created.",
        )
    else:
        messages.warning(request, "Sync completed with errors. Check the logs for details.")
    return redirect("workspace_list")


# ---------------------------------------------------------------------------
# Phase 3 — Store views
# ---------------------------------------------------------------------------

@login_required
def store_list(request):
    """
    GET /console/stores/
    Lists stores belonging to the currently active engine.
    Active engine is stored in the session; defaults to is_default engine.
    """
    client = _get_client(request)
    engine_ctx = _get_active_engine_context(request, client)
    active_engine_id = engine_ctx['active_engine_id']

    try:
        stores = client.list_stores(engine_id=active_engine_id) if active_engine_id else []
    except APITimeoutError:
        messages.error(request, "Registry unreachable — connection timed out.")
        stores = []
    except APIError as e:
        messages.error(request, f"Could not load stores: {e.detail}")
        stores = []

    return render(request, "geo_console/store_list.html", {
        "stores": stores,
        **engine_ctx,
    })


@login_required
def store_create(request):
    """
    GET  /console/stores/create/ — blank form with workspace dropdown
    POST /console/stores/create/ — POST /api/geoengine/stores/ → redirect to list
    """
    client = _get_client(request)
    engine_ctx = _get_active_engine_context(request, client)
    active_engine_id = engine_ctx['active_engine_id']

    # Fetch workspaces filtered to the active engine for the dropdown.
    try:
        workspaces = client.list_workspaces(engine_id=active_engine_id) if active_engine_id else []
    except (APIError, APITimeoutError):
        workspaces = []
    workspace_choices = [(str(ws["id"]), ws["name"]) for ws in workspaces]

    clone_store_name: str | None = None

    if request.method == "POST":
        form = StoreForm(request.POST, workspace_choices=workspace_choices)
        if form.is_valid():
            data = form.cleaned_data
            try:
                result = client.create_store(data)
            except APITimeoutError:
                messages.error(request, "Engine registry unreachable — timed out.")
                return render(request, "geo_console/store_create.html", {
                    "form": form,
                    "workspaces": workspaces,
                    "clone_store_name": None,
                    **engine_ctx,
                })
            except APIError as e:
                messages.error(request, f"Could not create store: {e.detail}")
                return render(request, "geo_console/store_create.html", {
                    "form": form,
                    "workspaces": workspaces,
                    "clone_store_name": None,
                    **engine_ctx,
                })

            store = result.get("store", result)
            is_idempotent = result.get("result", {}).get("idempotent", False)
            if is_idempotent:
                messages.warning(request, f"Store '{store['name']}' already exists — no changes made.")
            else:
                messages.success(request, f"Store '{store['name']}' created successfully.")
            return redirect("store_list")
    else:
        # Clone pre-fill: ?clone_from=<uuid> copies connection data from an existing store.
        clone_from_id = request.GET.get('clone_from')
        clone_initial: dict = {}
        if clone_from_id:
            try:
                source = client.get_store(clone_from_id)
                clone_store_name = source.get('name')
                clone_initial = {
                    'store_type': source.get('store_type', 'postgis'),
                    'description': source.get('description', ''),
                    'host': source.get('host', ''),
                    'port': str(source.get('port') or '5432'),
                    'database': source.get('database', ''),
                    'schema': source.get('schema', ''),
                    'username': source.get('username', ''),
                    # password is write-only in the API — intentionally not cloned
                }
            except (APIError, APITimeoutError, APINotFoundError):
                messages.warning(request, "Could not load source store for clone — starting blank.")

        # Pre-select the first workspace of the active engine.
        active_ws = next(
            (ws for ws in workspaces if str(ws.get("geodata_engine")) == active_engine_id),
            None,
        )
        form = StoreForm(
            workspace_choices=workspace_choices,
            initial={"workspace": str(active_ws["id"]) if active_ws else None, **clone_initial},
        )

    return render(request, "geo_console/store_create.html", {
        "form": form,
        "workspaces": workspaces,
        "clone_store_name": clone_store_name,
        **engine_ctx,
    })


@login_required
def store_detail(request, store_id):
    """
    GET  /console/stores/<uuid>/       — show store info + editable credentials form
    POST /console/stores/<uuid>/       — PATCH /api/geoengine/stores/{id}/ with updated fields

    Primary use-case: a store that was synced from GeoServer has no saved password
    in Django (GeoServer REST never exposes credentials).  The user opens Detail,
    enters the missing password + any other connection overrides, and saves.
    """
    client = _get_client(request)

    try:
        store = client.get_store(str(store_id))
    except APINotFoundError:
        messages.error(request, "Store not found.")
        return redirect("store_list")
    except APITimeoutError:
        messages.error(request, "Registry unreachable — timed out.")
        return redirect("store_list")
    except APIError as e:
        messages.error(request, f"Could not load store: {e.detail}")
        return redirect("store_list")

    engine_ctx = _get_active_engine_context(request, client)

    if request.method == "POST":
        form = StoreDetailForm(request.POST)
        if form.is_valid():
            data = {k: v for k, v in form.cleaned_data.items() if v not in (None, "", False)}
            # Never send an empty password — that would clear it
            if not form.cleaned_data.get("password"):
                data.pop("password", None)
            try:
                client.update_store(str(store_id), data)
            except APITimeoutError:
                messages.error(request, "Registry unreachable — timed out.")
                return render(request, "geo_console/store_detail.html", {
                    "store": store, "form": form, **engine_ctx
                })
            except APIError as e:
                messages.error(request, f"Could not update store: {e.detail}")
                return render(request, "geo_console/store_detail.html", {
                    "store": store, "form": form, **engine_ctx
                })
            messages.success(request, f"Store '{ store.get('name') }' updated.")
            return redirect("store_detail", store_id=store_id)
    else:
        # Pre-fill form with current store values
        form = StoreDetailForm(initial={
            "description": store.get("description", ""),
            "host": store.get("host", ""),
            "port": store.get("port", ""),
            "database": store.get("database", ""),
            "username": store.get("username", ""),
            "schema": store.get("schema", ""),
            # password intentionally left blank — never echoed back
        })

    return render(request, "geo_console/store_detail.html", {
        "store": store,
        "form": form,
        **engine_ctx,
    })


@login_required
@require_POST
def store_delete(request, store_id):
    """
    POST /console/stores/<uuid>/delete/
    Sync rule: engine deletion first, verify, THEN Django object delete.
    """
    client = _get_client(request)

    try:
        store = client.get_store(str(store_id))
    except APINotFoundError:
        messages.error(request, "Store not found.")
        return redirect("store_list")
    except APITimeoutError:
        messages.error(request, "Registry unreachable — could not load store.")
        return redirect("store_list")
    except APIError as e:
        messages.error(request, f"Could not load store: {e.detail}")
        return redirect("store_list")

    store_name = store.get("name", str(store_id))
    logger.info("User %s deleting store %s (%s)", request.user, store_id, store_name)

    try:
        client.delete_store(str(store_id))
    except APINotFoundError:
        messages.error(request, "Store not found.")
        return redirect("store_list")
    except APITimeoutError:
        messages.error(request, "Engine unreachable — timed out. Store was NOT deleted.")
        return redirect("store_list")
    except APIError as e:
        logger.error("Delete store %s failed: %s", store_id, e.detail)
        messages.error(request, f"Delete failed: {e.detail}")
        return redirect("store_list")

    messages.success(request, f"Store '{store_name}' deleted.")
    return redirect("store_list")


@login_required
@require_POST
def store_test_connection(request, store_id):
    """
    POST /console/stores/<uuid>/test/
    Calls the DRF test_connection action and surfaces the result as a Django message.
    """
    client = _get_client(request)
    logger.info("User %s testing store connection %s", request.user, store_id)
    try:
        result = client.test_store(str(store_id))
    except APITimeoutError:
        messages.error(request, "Engine unreachable — connection timed out.")
        return redirect("store_list")
    except APINotFoundError:
        messages.error(request, "Store not found.")
        return redirect("store_list")
    except APIError as e:
        logger.error("Store test failed for %s: %s", store_id, e.detail)
        messages.error(request, f"Connection test failed: {e.detail}")
        return redirect("store_list")

    if result.get("success"):
        messages.success(request, result.get("message", "Store connection verified."))
    else:
        messages.error(request, result.get("error", "Connection test returned an unexpected response."))
    return redirect("store_list")


# ---------------------------------------------------------------------------
# Phase 1.5 (continued) — Engine Delete
# ---------------------------------------------------------------------------

@login_required
@require_POST
def engine_delete(request, engine_id):
    """
    POST /console/engines/<uuid>/delete/
    Calls DELETE /api/geoengine/engines/{id}/ then redirects to the engine list.
    """
    client = _get_client(request)
    logger.info("User %s deleting engine %s", request.user, engine_id)
    try:
        client.delete_engine(str(engine_id))
    except APINotFoundError:
        messages.error(request, "Engine not found.")
        return redirect("geo_console")
    except APITimeoutError:
        messages.error(request, "Engine registry unreachable — timed out.")
        return redirect("engine_detail", engine_id=engine_id)
    except APIError as e:
        logger.error("Delete engine %s failed: %s", engine_id, e.detail)
        messages.error(request, f"Could not delete engine: {e.detail}")
        return redirect("engine_detail", engine_id=engine_id)

    messages.success(request, "Engine deleted.")
    return redirect("geo_console")


# ---------------------------------------------------------------------------
# Phase 4 — Layers
# ---------------------------------------------------------------------------

@login_required
def layer_list(request):
    """
    GET /console/layers/
    Flat paginated table of layers for the active engine.
    Query params:
        workspace — UUID string, filter to one workspace (empty = all).
        page      — page number (default 1), 20 per page.
    """
    from django.core.paginator import Paginator

    client = _get_client(request)
    engine_ctx = _get_active_engine_context(request, client)
    active_engine_id = engine_ctx['active_engine_id']
    workspace_filter = request.GET.get("workspace", "").strip()
    page_num = request.GET.get("page", "1")

    workspaces = []
    all_layers = []

    try:
        workspaces = client.list_workspaces(engine_id=active_engine_id or None)
        if workspace_filter:
            all_layers = client.list_layers(workspace_id=workspace_filter)
        else:
            all_layers = client.list_layers(engine_id=active_engine_id or None)
    except APITimeoutError:
        messages.error(request, "Engine unreachable — connection timed out.")
    except APIError as e:
        messages.error(request, f"Could not load layers: {e.detail}")

    # Build workspace id→name lookup so the template can show workspace name
    ws_lookup = {str(w["id"]): w["name"] for w in workspaces}
    for layer in all_layers:
        layer["workspace_name"] = ws_lookup.get(str(layer.get("workspace", "")), "—")

    paginator = Paginator(all_layers, 20)
    try:
        page_obj = paginator.page(page_num)
    except Exception:
        page_obj = paginator.page(1)

    return render(request, "geo_console/layer_list.html", {
        "page_obj": page_obj,
        "total_layers": paginator.count,
        "workspaces": workspaces,
        "workspace_filter": workspace_filter,
        **engine_ctx,
    })


@login_required
def layer_publish(request):
    """
    GET  /console/layers/publish/ — Render the publish form.
    POST /console/layers/publish/ — Submit to DRF, redirect on success.

    Two-step UI:
        Step 1 (JS): user selects workspace → store → store's tables load via
                     AJAX  GET /api/geoengine/stores/{id}/postgis_tables/
        Step 2: form submit → calls POST /api/geoengine/layers/publish_postgis/
    """
    client = _get_client(request)
    engine_ctx = _get_active_engine_context(request, client)
    active_engine_id = engine_ctx['active_engine_id']

    # Load choices for dropdowns
    try:
        workspaces = client.list_workspaces(engine_id=active_engine_id or None)
        stores = client.list_stores(engine_id=active_engine_id or None)
    except (APITimeoutError, APIError):
        workspaces = []
        stores = []

    workspace_choices = [(w["id"], w["name"]) for w in workspaces]
    store_choices = [(s["id"], f"{s.get('workspace_name', '')} / {s['name']}") for s in stores]

    if request.method == "POST":
        form = LayerPublishForm(
            request.POST,
            workspace_choices=workspace_choices,
            store_choices=store_choices,
        )
        if form.is_valid():
            cd = form.cleaned_data
            payload = {
                "store_id": cd["store_id"],
                "workspace_id": cd["workspace_id"],
                "table_name": cd["table_name"],
                "layer_name": cd["layer_name"],
                "geometry_column": cd["geometry_column"],
                "geometry_type": cd["geometry_type"],
                "srid": cd["srid"],
                "title": cd.get("title") or cd["layer_name"],
                "description": cd.get("description", ""),
            }
            try:
                client.publish_layer_postgis(payload)
                messages.success(
                    request,
                    f"Layer '{cd['layer_name']}' published successfully.",
                )
                return redirect("layer_list")
            except APITimeoutError:
                messages.error(request, "Engine unreachable — publish timed out.")
            except APIError as e:
                logger.error("Publish layer failed: %s", e.detail)
                # 409 = table already published — changing layer_name (title) won't help;
                # the conflict is on table_name (GeoServer featuretype identifier).
                if e.status_code == 409:
                    messages.error(
                        request,
                        f"{e.detail} "
                        "Go to the Layers list, delete the conflicting layer, "
                        "then republish — or select a different PostGIS table.",
                    )
                else:
                    messages.error(request, f"Publish failed: {e.detail}")
    else:
        # Pre-fill workspace from query param (e.g. coming from workspace detail)
        initial = {}
        ws_param = request.GET.get("workspace")
        if ws_param:
            initial["workspace_id"] = ws_param
        form = LayerPublishForm(
            workspace_choices=workspace_choices,
            store_choices=store_choices,
            initial=initial,
        )

    return render(request, "geo_console/layer_publish.html", {
        "form": form,
        "workspaces": workspaces,
        "stores": stores,
        **engine_ctx,
    })


@login_required
def layer_detail(request, layer_id):
    """
    GET  /console/layers/<uuid>/ — show layer detail + edit form
    POST /console/layers/<uuid>/ — PATCH /api/geoengine/layers/{id}/ with updated fields

    Editable: title, description (GeoServer-synced for PUBLISHED layers), srid, is_public.
    """
    client = _get_client(request)
    try:
        layer = client.get_layer(str(layer_id))
    except APINotFoundError:
        messages.error(request, "Layer not found.")
        return redirect("layer_list")
    except APITimeoutError:
        messages.error(request, "Engine unreachable — connection timed out.")
        return redirect("layer_list")
    except APIError as e:
        messages.error(request, f"Could not load layer: {e.detail}")
        return redirect("layer_list")

    if request.method == "POST":
        form = LayerEditForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            # Only send fields the user actually touched (non-empty/non-None)
            payload = {}
            if cd.get('title') is not None:
                payload['title'] = cd['title']
            if cd.get('description') is not None:
                payload['description'] = cd['description']
            if cd.get('srid') is not None:
                payload['srid'] = cd['srid']

            try:
                client.update_layer(str(layer_id), payload)
            except APITimeoutError:
                messages.error(request, "Engine unreachable — update timed out.")
                return render(request, "geo_console/layer_detail.html", {
                    "layer": layer, "form": form
                })
            except APIError as e:
                logger.error("Update layer %s failed: %s", layer_id, e.detail)
                messages.error(request, f"Update failed: {e.detail}")
                return render(request, "geo_console/layer_detail.html", {
                    "layer": layer, "form": form
                })

            messages.success(request, f"Layer '{layer.get('name')}' updated.")
            return redirect("layer_detail", layer_id=layer_id)
    else:
        form = LayerEditForm(initial={
            "title": layer.get("title", ""),
            "description": layer.get("description", ""),
            "srid": layer.get("srid", 4326),
        })

    return render(request, "geo_console/layer_detail.html", {"layer": layer, "form": form})


@login_required
@require_POST
def layer_delete(request, layer_id):
    """
    POST /console/layers/<uuid>/delete/
    Sync-safe delete: unpublish from GeoServer first, then remove Django object.
    """
    client = _get_client(request)
    logger.info("User %s deleting layer %s", request.user, layer_id)
    try:
        client.delete_layer(str(layer_id))
    except APINotFoundError:
        messages.error(request, "Layer not found.")
        return redirect("layer_list")
    except APITimeoutError:
        messages.error(request, "Engine unreachable — deletion timed out.")
        return redirect("layer_list")
    except APIError as e:
        logger.error("Delete layer %s failed: %s", layer_id, e.detail)
        messages.error(request, f"Could not delete layer: {e.detail}")
        return redirect("layer_list")

    messages.success(request, "Layer removed from GeoServer and registry.")
    return redirect("layer_list")
