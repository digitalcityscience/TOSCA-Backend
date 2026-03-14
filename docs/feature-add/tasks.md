# TOSCA — Console Development Task Tracker

**App name in codebase:** `geo_console`
**API prefix:** `/api/`
**Console prefix:** `/console/`
**Last updated:** March 2026

---

## Legend
- ✅ Done
- 🔄 In Progress
- ⬜ Not Started

---

## CURRENT CODEBASE STATE

Before touching anything, this is what already exists:

### `geodata_engine` app — Backend Foundation ✅
- Models: `GeodataEngine`, `Workspace`, `Store`, `Layer` (UUIDs, full fields)
- DRF API ViewSets registered:
  - `GET/POST /api/engines/`
  - `POST /api/engines/{id}/sync/`
  - `POST /api/engines/{id}/validate/`
  - `GET/POST /api/workspaces/`
  - `GET/POST /api/stores/`
  - `GET/POST /api/layers/`
- `geoserver/client.py` — GeoServer REST client
- `sync_service.py` — Pull-based sync (GeoServer → Django)
- `engine_factory.py` — Client factory

### `geo_console` app — Console UI (Partial)
- `apps.py`, `urls.py`, `views.py` ✅
- One view: `console()` — engine list with workspace/layer counts ✅
- One template: `templates/geo_console/geodata_console.html` ✅
- No forms, no exceptions, no services directory

### What does NOT exist yet
- Console sub-pages (workspaces, stores, layers, styles, jobs)
- `forms.py`, `exceptions.py`
- `services/api_client.py` (thin wrapper over internal DRF API)
- Layer import / GDAL inspection
- Style upload and validation (SLD / MBStyle)
- Async job tracking (Django-Q)
- Rate limiting, audit logs
- Push sync direction (Django → GeoServer) in UI

---

## PHASE 0 — Console Bootstrap ✅

**Branch:** `feature/console-bootstrap`
**Goal:** Minimal shell with navigation is in place.

| # | Task | Status |
|---|------|--------|
| 0.1 | Create `geo_console` app (`apps.py`, `urls.py`, `views.py`) | ✅ |
| 0.2 | Register app in `INSTALLED_APPS` | ✅ |
| 0.3 | Wire `/console/` URL in main `urls.py` | ✅ |
| 0.4 | `base.html` — dark layout, sidebar nav following UI-UX_Rules.md | ✅ |
| 0.5 | `geodata_console.html` — engine list (name, type, status, workspace count, layer count) | ✅ |
| 0.6 | `@login_required` on all views | ✅ |

**Milestone check:** `/console/` loads, engines are listed, no admin breakage. ✅

---

## PHASE 1 — Engines + Sync UI ✅

**Branch:** `feature/console-engines-sync`
**Goal:** Full engine management page with working sync buttons. This is the most critical phase.

### 1.1 — Console Service Layer ✅

The console views must NEVER touch models directly.
All data goes through `geo_console/services/api_client.py` which calls the internal DRF API.

| # | Task | Status |
|---|------|--------|
| 1.1.1 | Create `geo_console/services/__init__.py` | ✅ |
| 1.1.2 | Create `geo_console/services/api_client.py` — `GeoConsoleAPIClient` class | ✅ |
| 1.1.3 | Add methods: `list_engines()`, `get_engine(id)`, `sync_engine(id)`, `validate_engine(id)` | ✅ |
| 1.1.4 | Create `geo_console/exceptions.py` — `APIError`, `APITimeoutError`, `APINotFoundError` | ✅ |

**Extra changes made alongside 1.1:**
- `INTERNAL_API_BASE_URL` added to `tosca_api/settings/base.py` (default: `http://localhost:8000/api/geoengine`)
- `rest_framework.authentication.TokenAuthentication` added to DRF auth classes (needed for internal calls)
- `requests>=2.32.0` added to `pyproject.toml` as explicit dependency

Implementation note for `api_client.py`:
- Use `requests` session with `Authorization: Token` header
- Base URL from `settings.INTERNAL_API_BASE_URL`
- Always raise typed exceptions, never let raw HTTP errors bubble up
- Timeout: 10s default, 30s for sync operations

### 1.2 — Engine Detail & Actions ✅

| # | Task | Status |
|---|------|--------|
| 1.2.1 | Add `engine_detail(request, engine_id)` view | ✅ |
| 1.2.2 | Add `engine_sync(request, engine_id)` POST view | ✅ |
| 1.2.3 | Add `engine_validate(request, engine_id)` POST view | ✅ |
| 1.2.4 | Wire to `urls.py`: `/console/engines/<uuid>/`, `/console/engines/<uuid>/sync/`, `/console/engines/<uuid>/validate/` | ✅ |
| 1.2.5 | Template `engine_detail.html` — connection info, status badge, action buttons | ✅ |

**Extra changes made alongside 1.2:**
- `geodata_console.html` engine cards now have a "Details" link → `engine_detail`
- `@require_POST` applied to `engine_sync` and `engine_validate` (task S.2 partial)
- `_get_client()` helper in `views.py` handles token creation for all views

### 1.3 — Sync Safety Rules ✅

These are non-negotiable. Any sync logic that does not follow these patterns must be rewritten.

| Rule | Direction | Implementation |
|------|-----------|----------------|
| GeoServer is authoritative for published state | Pull | ✅ already in sync_service |
| Django is authoritative for metadata & intent | Push | ✅ push_all_workspaces() added |
| NEVER delete Django objects first | Delete | ✅ fixed in WorkspaceViewSet + LayerViewSet |
| ALWAYS verify engine state before and after | All | ✅ verify step added to delete_workspace_safe + WorkspaceViewSet.destroy |
| Create: check exists → create if missing → verify → persist | Create | ✅ push_workspace() follows this pattern |
| Delete: delete in engine FIRST → verify → delete Django object | Delete | ✅ delete_workspace_safe() + WorkspaceViewSet.destroy fixed |
| Update: compare → apply minimal change → verify | Update | ✅ pull sync already handles via update_or_create |

| # | Task | Status |
|---|------|--------|
| 1.3.1 | Audit `sync_service.py` — does each operation follow all 3 rules above? | ✅ |
| 1.3.2 | Add `push_workspace(workspace)` + `push_all_workspaces()` to sync service | ✅ |
| 1.3.3 | Add `delete_workspace_safe(workspace)` — engine first, verify, then Django | ✅ |
| 1.3.4 | Expose push sync via `POST /api/geoengine/engines/{id}/push/` | ✅ |

**Audit findings fixed alongside 1.3:**
- `WorkspaceViewSet.destroy`: was deleting Django regardless of engine result — **fixed** (returns 400 if engine fails, 409 if verify fails)
- `LayerViewSet.destroy`: was deleting Django regardless of unpublish result — **fixed** (returns 400 on unpublish failure)
- `api_client.py`: added `push_engine(engine_id)` method to match new endpoint

### 1.4 — UI Feedback ✅

| # | Task | Status |
|---|------|--------|
| 1.4.1 | Pull sync button → shows spinner → success/error inline | ✅ |
| 1.4.2 | Validate button → shows latency + connected/error badge | ✅ |
| 1.4.3 | Error messages show engine error text, not generic "something went wrong" | ✅ |
| 1.4.4 | All sync errors logged to `logger.error()` with engine ID | ✅ |

**Milestone check:**
- Engine list visible ✅
- Engine detail page loads ✅
- Sync button → AJAX call → inline spinner → stat panel (workspaces/stores/layers counts) ✅
- Validate button → AJAX call → latency + version badge ✅
- Error messages surface typed detail from API, not generic text ✅
- All sync errors logged with engine ID ✅
- Engine ↔ Django state never diverges ✅ (delete safety enforced in ViewSets + sync_service)

### 1.5 — Engine Create / Edit / Delete ✅

**Gap identified 13 March 2026:** The console had no way to add, edit, or delete engines — all CRUD existed only in the DRF API, not the UI.

| # | Task | Status |
|---|------|--------|
| 1.5.1 | Add `create_engine()`, `update_engine()`, `delete_engine()` to `api_client.py` | ✅ |
| 1.5.2 | Create `geo_console/forms.py` — `EngineForm` (name, description, engine_type, base_url, admin_username, admin_password, is_active, is_default) | ✅ |
| 1.5.3 | Add `engine_create(request)` view — GET renders blank form, POST creates via API | ✅ |
| 1.5.4 | Add `engine_edit(request, engine_id)` view — GET prefills form, POST updates via API | ✅ |
| 1.5.5 | Add `engine_delete(request, engine_id)` POST view — calls `DELETE /api/geoengine/engines/{id}/`, redirects to list | ✅ |
| 1.5.6 | Create `templates/geo_console/engine_form.html` — shared create/edit form, dark theme, inline validation errors | ✅ |
| 1.5.7 | Add "+ Add Engine" button to `geodata_console.html` page header | ✅ |
| 1.5.8 | Add Edit + Delete buttons to `engine_detail.html` page header | ✅ |
| 1.5.9 | Wire URLs: `/console/engines/create/`, `/console/engines/<uuid>/edit/`, `/console/engines/<uuid>/delete/` | ✅ |

**Rules:**
- `engine_delete` must be `@require_POST` — no DELETE via GET
- Password field renders as `type="password"`, never echoed back in edit mode
- `is_default` checkbox: if checked, existing default engine must be unset (handled by the API/model layer)
- Form validation errors render inline — no redirect on invalid form

**Milestone check:**
- Can create a new GeoServer engine from the console ⬜
- Can edit name/URL/credentials of an existing engine ⬜
- Can delete an engine (no workspaces check required at this phase) ⬜
- Password never appears in the HTML of the edit form ⬜

### 1.6 — UX Improvement: Remove Validate button, Add stateless Test Connection ✅

**Change identified 14 March 2026:**
- "Validate Connection" button on engine detail was redundant — sync already validates the connection before running and reports failure explicitly
- Engine create form had no way to check credentials before saving

| # | Task | Status |
|---|------|--------|
| 1.6.1 | Remove "Validate Connection" button + result panel from `engine_detail.html` | ✅ |
| 1.6.2 | Add `test_connection` non-detail DRF action — stateless POST (base_url, admin_username, admin_password, engine_type) | ✅ |
| 1.6.3 | Add `GeoServerClient` + `GeoServerConnectionError` imports to `api/views.py` | ✅ |
| 1.6.4 | `engine_form.html` create mode: "Create Engine" button starts disabled | ✅ |
| 1.6.5 | Add "Test Connection" button to create form — calls `POST /api/geoengine/engines/test_connection/` | ✅ |
| 1.6.6 | On test success: enable Create button, show latency + version badge | ✅ |
| 1.6.7 | On connection field change: re-disable Create button (forces re-test) | ✅ |
| 1.6.8 | Fix sync: layer names from GeoServer carry `workspace:layername` prefix — must be stripped before Django lookup | ✅ |

**New endpoint:** `POST /api/geoengine/engines/test_connection/`
**Edit mode:** unchanged — Test Connection not shown, Save Changes always enabled.

#### 1.6.8 — Sync Layer Name Prefix Bug (ARCHITECTURE ISSUE — do not implement without reading this)

**Status:** ✅ Complete.

**What was done:**
- `client.py` `get_layers()`: already strips `workspace:` prefix (patched 14 March 2026).
- `sync_service.py` `sync_all_resources()`: integrity cleanup block added at start of every sync — finds any `Layer.name` containing `:`, strips the prefix, deduplicates, and logs warnings.
- `api/views.py` `GeodataEngineViewSet`: `create()` and `update()` now auto-trigger `sync_all_resources()` immediately after the engine is persisted, so workspaces/stores/layers are populated in Django without requiring a manual "Sync Now" click.

**Problem:**
GeoServer REST API `GET /rest/workspaces/{ws}/layers` returns layer names in the format `workspace:layername` (e.g. `vector:buildings`). The current `client.get_layers()` was returning these raw names. Django's `Layer` model stores only `layername` (without prefix), enforced by `unique_together = ['workspace', 'name']`.

**Consequence before partial patch:**
Every sync cycle would:
1. See `vector:buildings` from GeoServer → not found in Django → `created += 1`
2. See `buildings` in Django → not found in GeoServer → `deleted += 1`
3. Repeat next sync → UI showed "✓ Imported 1 layer · 1 removed" even with no real changes

**Partial patch applied (14 March 2026):**
In `geoserver/client.py` `get_layers()`: strip `workspace:` prefix via `layer['name'].split(':', 1)[1]`.
This fixes new syncs going forward but **does not clean up already-corrupted Django records** (rows with names like `vector:buildings` that were created before the patch).

**What needs to happen before this task is complete:**

1. **Data migration / cleanup script** — find all `Layer` records whose `name` contains `:` and strip the prefix:
   ```python
   for layer in Layer.objects.filter(name__contains=':'):
       layer.name = layer.name.split(':', 1)[1]
       layer.save()
   ```
   Must check for duplicate `(workspace, name)` before saving — if both `vector:buildings` and `buildings` exist in the same workspace, deduplicate first.

2. **Verify `get_datastores()` does NOT have the same issue** — GeoServer store names sometimes also include workspace prefix in some API responses. Audit `client.get_datastores()` return values in logs.

3. **Add a data integrity check** to `sync_all_resources()` — before syncing, assert that no Django `Layer.name` contains `:`. Log a warning if found so it is visible in future.

**Files involved:**
- `tosca_api/apps/geodata_engine/geoserver/client.py` — `get_layers()` (already patched)
- `tosca_api/apps/geodata_engine/sync_service.py` — consider adding integrity assertion
- A one-time migration or management command to clean existing data

---

## PHASE 2 — Workspaces ✅

**Branch:** `feature/console-workspaces`
**Goal:** Workspace CRUD with sync-safe behavior.

| # | Task | Status |
|---|------|--------|
| 2.1 | Add `workspace_list(request)` view | ✅ |
| 2.2 | Add `workspace_create(request)` POST view | ✅ |
| 2.3 | Add `workspace_delete(request, workspace_id)` POST view — engine first! | ✅ |
| 2.4 | Add `workspace_sync(request, workspace_id)` POST view | ✅ |
| 2.5 | Add `WorkspaceForm` to `forms.py` (name, description, engine selection) | ✅ |
| 2.6 | Templates: `workspace_list.html`, `workspace_create.html` | ✅ |
| 2.7 | Add methods to `api_client.py`: `list_workspaces()`, `create_workspace()`, `delete_workspace()`, `sync_workspace()` | ✅ |
| 2.8 | Protect default workspace `vector` — cannot delete | ✅ |
| 2.9 | Wire URLs: `/console/workspaces/`, `/console/workspaces/create/`, `/console/workspaces/<uuid>/delete/` | ✅ |

**Milestone check:**
- Workspace CRUD works ✅
- Delete fails safely if workspace has layers ✅ (WorkspaceViewSet.destroy handles this — 400 if engine delete fails)
- Default `vector` workspace cannot be deleted ✅ (checked in workspace_delete view before API call)
- Sync leaves no ghost workspaces ✅ (sync_workspace() delegates to engine pull-sync)

---

## PHASE 3 — Stores ⬜

**Branch:** `feature/console-stores`
**Goal:** Store management bound to PostGIS schemas.

| # | Task | Status |
|---|------|--------|
| 3.1 | Add `store_list(request)` view | ⬜ |
| 3.2 | Add `store_create(request)` POST view | ⬜ |
| 3.3 | Add `store_delete(request, store_id)` POST view | ⬜ |
| 3.4 | Add `store_test_connection(request, store_id)` POST view | ⬜ |
| 3.5 | `StoreForm` — workspace selection, schema from ENV, db params | ⬜ |
| 3.6 | Validate schema existence in DB before creating store in GeoServer | ⬜ |
| 3.7 | Templates: `store_list.html`, `store_create.html` | ⬜ |
| 3.8 | Add `list_stores()`, `create_store()`, `delete_store()`, `test_store()` to `api_client.py` | ⬜ |
| 3.9 | Wire URLs: `/console/stores/`, `/console/stores/create/`, `/console/stores/<uuid>/delete/` | ⬜ |

**Milestone check:**
- Store connects to PostGIS ⬜
- Schema validated before insert ⬜
- Workspace-store relationship is stable ⬜

---

## PHASE 4 — Layers ⬜

**Branch:** `feature/console-layers`
**Goal:** Layer import → PostGIS → GeoServer publish.

### 4.1 — Upload & Inspect ⬜

| # | Task | Status |
|---|------|--------|
| 4.1.1 | `layer_upload(request)` view — accept GeoJSON / GeoPackage | ⬜ |
| 4.1.2 | GDAL inspection: geometry type, CRS, feature count | ⬜ |
| 4.1.3 | `LayerUploadForm` — file field, target workspace, target store, layer name | ⬜ |
| 4.1.4 | Show inspection result to user before confirming import | ⬜ |

### 4.2 — Import to PostGIS ⬜

| # | Task | Status |
|---|------|--------|
| 4.2.1 | `layer_import(request)` POST view — GDAL → PostGIS ingest | ⬜ |
| 4.2.2 | Register `Layer` object in Django after successful import | ⬜ |
| 4.2.3 | Rollback Django object if PostGIS import fails | ⬜ |

### 4.3 — Publish to Engine ⬜

| # | Task | Status |
|---|------|--------|
| 4.3.1 | `layer_publish(request, layer_id)` POST view — separate step from import | ⬜ |
| 4.3.2 | Publish via engine client, verify layer is live | ⬜ |
| 4.3.3 | Update `Layer.is_published = True` only after verified success | ⬜ |

### 4.4 — Layer List & Detail ⬜

| # | Task | Status |
|---|------|--------|
| 4.4.1 | `layer_list(request)` view — filter by workspace/store | ⬜ |
| 4.4.2 | `layer_detail(request, layer_id)` view — geometry info, CRS, publish status | ⬜ |
| 4.4.3 | `layer_delete(request, layer_id)` — engine first, verify, then Django | ⬜ |
| 4.4.4 | Templates: `layer_list.html`, `layer_detail.html`, `layer_upload.html`, `publish_confirm.html` | ⬜ |

**Milestone check:**
- Layer uploaded and inspected ⬜
- Layer imported into PostGIS ⬜
- Layer published to GeoServer ⬜
- Sync state consistent across Django + PostGIS + GeoServer ⬜

---

## PHASE 5 — Styles ⬜

**Branch:** `feature/console-styles`
**Goal:** Style upload, validation, and publish to GeoServer.

| # | Task | Status |
|---|------|--------|
| 5.1 | `style_list(request)` view | ⬜ |
| 5.2 | `style_upload(request)` POST view — accept `.sld` or `.json` (MBStyle) | ⬜ |
| 5.3 | SLD validation: XML parse + XSD schema check | ⬜ |
| 5.4 | MBStyle validation: JSON schema check | ⬜ |
| 5.5 | `style_publish(request, style_id)` POST view | ⬜ |
| 5.6 | `style_delete(request, style_id)` — engine first | ⬜ |
| 5.7 | `StyleUploadForm` — file field, style name, format selector | ⬜ |
| 5.8 | Templates: `style_list.html`, `style_upload.html` | ⬜ |
| 5.9 | Add `Style` model to `geodata_engine` or reference via engine API | ⬜ |

**Milestone check:**
- SLD and MBStyle validated before publish ⬜
- Style published to GeoServer ⬜
- Errors are explicit (which line, which rule failed) ⬜

---

## PHASE 6 — Jobs (Django-Q) ⬜

**Branch:** `feature/console-jobs`
**Goal:** Async job tracking for expensive operations.

| # | Task | Status |
|---|------|--------|
| 6.1 | Add `django-q` to `pyproject.toml` and `INSTALLED_APPS` | ⬜ |
| 6.2 | Configure `Q_CLUSTER` in `base.py` | ⬜ |
| 6.3 | Create `Job` model (or use Django-Q's `Task` model) with states: `pending`, `running`, `failed`, `completed` | ⬜ |
| 6.4 | Move sync operations to `async_task()` calls | ⬜ |
| 6.5 | Move layer import/publish to async tasks | ⬜ |
| 6.6 | `job_list(request)` view — show status, error, duration | ⬜ |
| 6.7 | Template: `job_list.html` — auto-refresh every 5s | ⬜ |
| 6.8 | Wire URL: `/console/jobs/` | ⬜ |

**Milestone check:**
- Long operations don't block the HTTP response ⬜
- Job status visible in `/console/jobs/` ⬜
- Failed jobs show error message ⬜

---

## CROSS-CUTTING CONCERNS

These apply to every phase. Do not skip.

### Security
| # | Task | Status |
|---|------|--------|
| S.1 | All console views require `@login_required` | ✅ |
| S.2 | Destructive POST views require `@require_POST` | ⬜ |
| S.3 | All forms use CSRF tokens | ⬜ |
| S.4 | Add `ConsoleUserPermission` class — only `console_user` or admin role can access | ⬜ |
| S.5 | Rate limit sync/publish endpoints — max 5 calls/min per user | ⬜ |

### Audit Logging
| # | Task | Status |
|---|------|--------|
| A.1 | Create `AuditLog` model: `user`, `action`, `target_type`, `target_id`, `timestamp`, `result` | ⬜ |
| A.2 | Log every create/delete/sync/publish action | ⬜ |
| A.3 | Show last 20 audit entries in console sidebar or `/console/audit/` | ⬜ |

### Error Handling
| # | Task | Status |
|---|------|--------|
| E.1 | All engine errors caught and shown with specific message (not generic 500) | ⬜ |
| E.2 | Network timeouts show "Engine unreachable" — not crash | ⬜ |
| E.3 | Validation errors shown inline on forms, not redirected | ⬜ |

### URL Structure (final target)
```
/console/                          — engine overview (done)
/console/engines/<uuid>/           — engine detail
/console/engines/<uuid>/sync/      — POST
/console/engines/<uuid>/validate/  — POST
/console/workspaces/               — list + create
/console/workspaces/<uuid>/delete/ — POST
/console/workspaces/<uuid>/sync/   — POST
/console/stores/                   — list + create
/console/stores/<uuid>/delete/     — POST
/console/stores/<uuid>/test/       — POST
/console/layers/                   — list
/console/layers/upload/            — GET + POST
/console/layers/<uuid>/            — detail
/console/layers/<uuid>/publish/    — POST
/console/layers/<uuid>/delete/     — POST
/console/styles/                   — list
/console/styles/upload/            — GET + POST
/console/styles/<uuid>/delete/     — POST
/console/jobs/                     — list
```

---

## PROGRESS SUMMARY

| Phase | Description | Status |
|-------|-------------|--------|
| 0 | Bootstrap console app | ✅ Done |
| 1 | Engines + Sync UI | ✅ Done (1.1–1.6 all complete) |
| 2 | Workspaces | ✅ Done |
| 3 | Stores | ⬜ Not started |
| 4 | Layers (import + publish) | ⬜ Not started |
| 5 | Styles | ⬜ Not started |
| 6 | Jobs (Django-Q) | ⬜ Not started |

**Next action:** Phase 3 — Stores.
**Last updated:** 14 March 2026 — Auto-sync on engine create/update implemented; 1.6.8 layer name integrity cleanup complete. Sync architecture fixed: `get_datastores()` now fetches real host/port/database/schema per store via individual detail calls; `get_layers()` now traverses store→featuretypes so every layer carries its `store_name`, eliminating placeholder stores during sync.
