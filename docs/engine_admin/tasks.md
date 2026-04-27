# TOSCA — Geodata Engine Admin Panel Task Tracker

**Target app:** `geodata_engine`
**Admin prefix:** `/admin/geodata_engine/`
**DRF API prefix (unchanged, never touched):** `/api/`
**Geo Console (unchanged, never touched):** `/console/`
**Last updated:** 16 March 2026

---

## Legend
- ✅ Done
- 🔄 In Progress
- ⬜ Not Started

---

## GOAL & SCOPE

Replace the need for a separate console UI by building a fully functional Django admin panel for the `geodata_engine` app. The admin panel must support:

- **Engine** — CRUD, sync (pull from GeoServer), test connection
- **Workspace** — CRUD, per-workspace sync, list stores/layers inline
- **Store** — CRUD, credential management, PostGIS table preview
- **Layer** — CRUD, publish from PostGIS table, unpublish/delete with sync-safe flow

### What this is NOT
- This does not replace the geo_console app (leave it completely untouched)
- This does not touch any DRF ViewSet or serializer (those remain for future API consumers)
- This does not add new models — all 4 models already exist in `geodata_engine/models.py`
- This does not add Celery or any async queue at this phase

### Sync Philosophy (NON-NEGOTIABLE — same as geo_console)

| Operation | Sequence |
|-----------|----------|
| CREATE | Check exists in engine → create in engine if missing → verify in engine → persist in Django |
| DELETE | Delete in engine FIRST → verify deletion → delete Django object |
| UPDATE | Compare current state → apply minimal change in engine → verify → update Django |
| PULL sync | GeoServer state → Django DB (via `sync_service.py`) |

**Never:** delete Django objects before engine deletion succeeds.
**Never:** assume an engine operation succeeded without verifying.
**Never:** call `GeoServerClient` directly in admin views — always go through `EngineClientFactory.create_client(engine)`.

---

## ARCHITECTURE RULES FOR ADMIN

```
geodata_engine/
  admin.py          ← ALL admin registration here. One file.
  admin_actions.py  ← Custom admin actions (sync, publish, etc.)
  admin_forms.py    ← Admin-specific forms (PostGIS table picker, credential form)
  admin_views.py    ← Custom admin views wired via AdminSite.get_urls()
                       (e.g. postgis table list endpoint, test connection endpoint)
```

- All GeoServer operations go through `EngineClientFactory.create_client(engine)` from `engine_factory.py`
- Sync operations go through `sync_service.py` functions only
- Existing exceptions from `exceptions.py` must be caught everywhere — `GeoServerConnectionError`, `GeoServerPublishError`, `GeodataEngineError`
- Never use `except Exception: pass`
- All destructive actions require Django admin confirmation step (`intermediate_view` pattern)
- Use `self.message_user(request, ..., messages.SUCCESS/ERROR)` for feedback — never `print()`

---

## EXISTING CODEBASE STATE (read-only reference)

### Models (do not modify)
| Model | Key fields |
|-------|-----------|
| `GeodataEngine` | `id (UUID)`, `name`, `engine_type`, `base_url`, `admin_username`, `admin_password (encrypted)`, `is_active`, `is_default` |
| `Workspace` | `id (UUID)`, `geodata_engine (FK)`, `name`, `description` |
| `Store` | `id (UUID)`, `workspace (FK)`, `name`, `store_type`, `db_host`, `db_port`, `db_name`, `db_username`, `db_password (encrypted)`, `db_schema` |
| `Layer` | `id (UUID)`, `store (FK)`, `workspace (FK)`, `name`, `title`, `table_name`, `geometry_column`, `geometry_type`, `srid`, `publishing_state`, `is_published` |

### DRF Endpoints (never touch)
- `GET/POST /api/engines/` + sync/validate/push/test_connection actions
- `GET/POST /api/workspaces/` + sync action
- `GET/POST /api/stores/` + postgis_tables action
- `GET/POST /api/layers/` + publish_postgis action

### Existing helpers (use these)
- `tosca_api/apps/geodata_engine/engine_factory.py` — `EngineClientFactory.create_client(engine)`
- `tosca_api/apps/geodata_engine/sync_service.py` — `sync_all_resources()`, `sync_stores_for_workspace()`, `sync_layers_for_workspace()`
- `tosca_api/apps/geodata_engine/geoserver/client.py` — `GeoServerClient` (never instantiate directly)
- `tosca_api/apps/geodata_engine/exceptions.py` — `GeoServerConnectionError`, `GeoServerPublishError`, `GeodataEngineError`
- `tosca_api/apps/geodata_engine/postgis_inspector.py` — `list_tables()`, `get_table_metadata()` (PostGIS direct connection)
- `tosca_api/apps/geodata_engine/encryption.py` — `decrypt_value()` for reading stored credentials

---

## PHASE 1 — Engine Admin ✅

**Goal:** Full engine management in admin — create, edit, delete, sync, test connection.

### 1.1 — Base Engine ModelAdmin

| # | Task | Status |
|---|------|--------|
| 1.1.1 | Register `GeodataEngine` in `admin.py` with `GeodataEngineAdmin(ModelAdmin)` | ✅ |
| 1.1.2 | `list_display`: `name`, `engine_type`, `base_url`, `is_active`, `is_default`, `connection_status_badge`, `workspace_count`, `layer_count` | ✅ |
| 1.1.3 | `list_filter`: `engine_type`, `is_active`, `is_default` | ✅ |
| 1.1.4 | `search_fields`: `name`, `base_url` | ✅ |
| 1.1.5 | `readonly_fields` on change form: `id`, `created_at`, `updated_at` | ✅ |
| 1.1.6 | `fieldsets`: "Identity" (name, description, engine_type), "Connection" (base_url, admin_username, admin_password), "State" (is_active, is_default) | ✅ |
| 1.1.7 | Password field: render as `PasswordInput` — never echo decrypted value back to UI | ✅ |

### 1.2 — Computed list_display columns

| # | Task | Status |
|---|------|--------|
| 1.2.1 | `workspace_count` — annotate queryset with `Count('workspace')` | ✅ |
| 1.2.2 | `layer_count` — annotate with `Count('workspace__layer')` | ✅ |
| 1.2.3 | `connection_status_badge` — readonly column; calls `client.validate_connection()` on engine, renders `✓ Connected` or `✗ Unreachable` with HTML color (use `format_html`). Mark `short_description = "Status"`, not cached at list load — only shown when fetched on demand (add a "Test Connection" button instead, see 1.3) | ✅ |

### 1.3 — Admin Actions

| # | Task | Status |
|---|------|--------|
| 1.3.1 | Create `admin_actions.py` with `sync_engines(modeladmin, request, queryset)` action | ✅ |
| 1.3.2 | `sync_engines`: iterate selected engines → call `sync_all_resources(engine)` → `self.message_user()` per result | ✅ |
| 1.3.3 | Add `test_connection` action — calls `client.validate_connection()`, reports latency + version | ✅ |
| 1.3.4 | Add `set_as_default` action — sets `is_default=True` on selected engine, unsets others | ✅ |
| 1.3.5 | Register all 3 actions in `GeodataEngineAdmin.actions` | ✅ |

### 1.4 — Custom Change Form Button (Test Connection)

| # | Task | Status |
|---|------|--------|
| 1.4.1 | Create `admin_views.py` with `engine_test_connection_view(request, engine_id)` — calls `EngineClientFactory.create_client(engine).validate_connection()`, returns JSON | ✅ |
| 1.4.2 | Wire view via `GeodataEngineAdmin.get_urls()` at `engines/<id>/test-connection/` | ✅ |
| 1.4.3 | Add "Test Connection" button to engine change form via `change_form_template` override — calls the wired URL via fetch, shows latency + version inline | ✅ |
| 1.4.4 | Add "Sync Now" button to engine change form — calls `admin_views.engine_sync_view`, shows workspaces/stores/layers count result inline | ✅ |

**Milestone check:**
- Engine list shows name, type, URL, active/default state, ws+layer counts ✅
- Can create/edit/delete engine from admin ✅
- Password never visible in HTML ✅
- "Test Connection" button works from change form ✅
- Bulk sync action works from list ✅

---

## PHASE 2 — Workspace Admin ✅

**Goal:** Workspace management with inline store preview, per-workspace sync.

### 2.1 — Base Workspace ModelAdmin

| # | Task | Status |
|---|------|--------|
| 2.1.1 | Register `Workspace` with `WorkspaceAdmin(ModelAdmin)` | ✅ |
| 2.1.2 | `list_display`: `name`, `engine_link`, `description`, `store_count`, `layer_count`, `created_at` | ✅ |
| 2.1.3 | `list_filter`: `geodata_engine`, `geodata_engine__engine_type` | ✅ |
| 2.1.4 | `search_fields`: `name`, `geodata_engine__name` | ✅ |
| 2.1.5 | `engine_link`: `format_html('<a href="...">{name}</a>')` linking to engine change page | ✅ |
| 2.1.6 | `store_count`, `layer_count`: annotated in `get_queryset()` | ✅ |

### 2.2 — Inline: Stores under Workspace

| # | Task | Status |
|---|------|--------|
| 2.2.1 | `StoreInline(TabularInline)` — `model=Store`, `fields=('name', 'store_type', 'db_host', 'db_schema', 'has_credential')`, all `readonly_fields` | ✅ |
| 2.2.2 | `has_credential` — inline method: `bool(store.db_password)` rendered as ✓/✗ | ✅ |
| 2.2.3 | `extra = 0`, `can_delete = False`, `show_change_link = True` | ✅ |
| 2.2.4 | Add `StoreInline` to `WorkspaceAdmin.inlines` | ✅ |

### 2.3 — Sync Action

| # | Task | Status |
|---|------|--------|
| 2.3.1 | `sync_workspaces` admin action — calls `sync_all_resources(engine)` for each unique engine in queryset | ✅ |
| 2.3.2 | Add "Sync Workspace" button to workspace change form via custom URL (same pattern as engine) | ✅ |
| 2.3.3 | Sync result: displays workspace/store/layer delta (created/updated/deleted counts) | ✅ |

### 2.4 — Delete Safety

| # | Task | Status |
|---|------|--------|
| 2.4.1 | Override `WorkspaceAdmin.delete_model(request, obj)` — call `client.delete_workspace(workspace.name)` first, verify deletion, then `obj.delete()` | ✅ |
| 2.4.2 | If engine deletion fails: raise `PermissionDenied` with error message — Django admin surfaces this cleanly | ✅ |
| 2.4.3 | Override `delete_queryset` for bulk delete — same per-workspace safety loop | ✅ |
| 2.4.4 | Protect `vector` workspace — if `obj.name == 'vector'`: `self.message_user(request, "Default workspace 'vector' cannot be deleted.", messages.ERROR)` and abort | ✅ |

**Milestone check:**
- Workspace list shows engine, store/layer counts ✅
- StoreInline visible on workspace change form ✅
- Delete goes engine-first, blocks if engine deletion fails ✅
- Sync action updates Django from GeoServer ✅

---

## PHASE 3 — Store Admin ✅

**Goal:** Store management with credential editing and PostGIS table preview.

### 3.1 — Base Store ModelAdmin

| # | Task | Status |
|---|------|--------|
| 3.1.1 | Register `Store` with `StoreAdmin(ModelAdmin)` | ✅ |
| 3.1.2 | `list_display`: `name`, `workspace_link`, `store_type`, `db_host`, `db_schema`, `has_password_badge`, `layer_count` | ✅ |
| 3.1.3 | `list_filter`: `store_type`, `workspace__geodata_engine`, `workspace` | ✅ |
| 3.1.4 | `search_fields`: `name`, `workspace__name`, `db_host`, `db_schema` | ✅ |
| 3.1.5 | `has_password_badge` — `format_html('<span style="color:green">✓ Set</span>')` / `format_html('<span style="color:#e54d4d">✗ Missing</span>')` | ✅ |

### 3.2 — Fieldsets on Change Form

| # | Task | Status |
|---|------|--------|
| 3.2.1 | "Identity" fieldset: `name`, `workspace`, `store_type` — readonly after creation | ✅ |
| 3.2.2 | "PostGIS Connection" fieldset: `db_host`, `db_port`, `db_name`, `db_username`, `db_password`, `db_schema` | ✅ |
| 3.2.3 | `db_password` field: `PasswordInput` widget, never pre-filled, help_text: "Leave blank to keep existing password." | ✅ |
| 3.2.4 | `save_model()`: if `db_password` is blank in submission, preserve existing encrypted value — never overwrite with empty string | ✅ |

### 3.3 — PostGIS Table Preview (Custom Admin View)

| # | Task | Status |
|---|------|--------|
| 3.3.1 | `admin_views.store_postgis_tables_view(request, store_id)` — decrypts credentials, calls `postgis_inspector.list_tables(store)`, returns JSON `{tables: [...]}` | ✅ |
| 3.3.2 | Wire at `stores/<id>/postgis-tables/` via `StoreAdmin.get_urls()` | ✅ |
| 3.3.3 | Add "Preview PostGIS Tables" button to store change form — fetch the URL, render table list inline (table name, geometry column, type, srid) | ✅ |
| 3.3.4 | If no password set: button shows warning inline "Set connection credentials first." | ✅ |

### 3.4 — Layer Inline under Store

| # | Task | Status |
|---|------|--------|
| 3.4.1 | `LayerInline(TabularInline)` — `model=Layer`, `fields=('name', 'title', 'geometry_type', 'srid', 'publishing_state')`, all `readonly_fields` | ✅ |
| 3.4.2 | `extra = 0`, `can_delete = False`, `show_change_link = True` | ✅ |
| 3.4.3 | Add `LayerInline` to `StoreAdmin.inlines` | ✅ |

### 3.5 — Delete Safety

| # | Task | Status |
|---|------|--------|
| 3.5.1 | Override `StoreAdmin.delete_model(request, obj)` — call `EngineClientFactory.create_client(engine).delete_featurestore(workspace, store_name)` first, verify, then `obj.delete()` | ✅ |
| 3.5.2 | Override `delete_queryset` for bulk — same safety loop | ✅ |

**Milestone check:**
- Store list shows workspace, type, host, schema, credential status, layer count ✅
- Password field never echoed back ✅
- Blank password on save preserves existing encrypted value ✅
- "Preview PostGIS Tables" button returns geometry metadata ✅
- Delete goes engine-first ✅

---

## PHASE 4 — Layer Admin + PostGIS Publish ✅

**Goal:** Layer management — list, edit title/description/srid, publish from PostGIS, delete sync-safe. No file upload (that is a future phase).

### 4.1 — Base Layer ModelAdmin

| # | Task | Status |
|---|------|--------|
| 4.1.1 | Register `Layer` with `LayerAdmin(ModelAdmin)` | ✅ |
| 4.1.2 | `list_display`: `name`, `title`, `workspace_link`, `store_name`, `geometry_type`, `srid`, `publishing_state_badge`, `is_public` | ✅ |
| 4.1.3 | `list_filter`: `publishing_state`, `geometry_type`, `workspace__geodata_engine`, `workspace`, `is_public` | ✅ |
| 4.1.4 | `search_fields`: `name`, `title`, `table_name`, `workspace__name` | ✅ |
| 4.1.5 | `publishing_state_badge`: `format_html(...)` — PUBLISHED=green, UNPUBLISHED=orange, DRAFT=grey, FAILED=red | ✅ |
| 4.1.6 | `readonly_fields` on change form: `id`, `name`, `table_name`, `geometry_column`, `geometry_type`, `workspace`, `store`, `created_at`, `publishing_state` | ✅ |

### 4.2 — Editable Fields on Change Form

| # | Task | Status |
|---|------|--------|
| 4.2.1 | "Identity" fieldset: `name` (readonly), `title` (editable), `description` (editable) | ✅ |
| 4.2.2 | "Geometry & CRS" fieldset: `geometry_column` (readonly), `geometry_type` (readonly), `srid` (editable), `table_name` (readonly) | ✅ |
| 4.2.3 | "Visibility" fieldset: `publishing_state` (readonly), `is_public` (editable), `is_published` (readonly) | ✅ |
| 4.2.4 | `save_model()` for PUBLISHED layers: call `client.update_featuretype(workspace, store, table_name, title, abstract)` BEFORE `super().save_model()` — if GeoServer update fails, abort save and `self.message_user(..., messages.ERROR)` | ✅ |

### 4.3 — Publish from PostGIS (Custom Admin View)

This is the most complex part. Must replicate the logic of `LayerViewSet.publish_postgis` without calling it (that DRF endpoint is for API consumers). Reuse the same underlying service functions.

| # | Task | Status |
|---|------|--------|
| 4.3.1 | Create `admin_forms.py` — `PublishPostGISForm`: `workspace` (ModelChoiceField), `store` (ModelChoiceField filtered by workspace), `table_name` (CharField — user types or selects), `layer_name` (CharField — GeoServer featuretype identifier), `title` (CharField, optional), `description` (Textarea, optional), `geometry_column` (CharField, default `geom`), `geometry_type` (ChoiceField from `GEOMETRY_TYPE_CHOICES`), `srid` (IntegerField, default 4326) | ✅ |
| 4.3.2 | Create `admin_views.publish_postgis_view(request)` — GET renders form, POST runs publish flow | ✅ |
| 4.3.3 | Publish flow (same sequence as `LayerViewSet.publish_postgis` — reuse service layer, NOT the DRF action): | ✅ |
| | → Validate form | |
| | → `EngineClientFactory.create_client(engine)` | |
| | → `client.verify_featuretype(workspace, store, table_name)` — if exists: error "already published" | |
| | → `client.publish_featuretype(store_name, workspace, pg_table, srid, geometry_type, layer_name)` | |
| | → `client.verify_featuretype(...)` — post-publish confirmation | |
| | → `Layer.objects.update_or_create(workspace=ws, name=table_name, defaults={...})` | |
| | → `self.message_user(request, "Layer published.", messages.SUCCESS)` → redirect to layer changelist | |
| 4.3.4 | Wire view at `layers/publish-postgis/` via `LayerAdmin.get_urls()` | ✅ |
| 4.3.5 | Add "Publish from PostGIS" button at the top of the Layer changelist (override `changelist_view` or use `change_list_template` with custom context) | ✅ |
| 4.3.6 | Add AJAX endpoint `admin_views.store_tables_for_workspace(request)` — `GET ?workspace_id=<uuid>` returns stores for that workspace; used by PublishPostGISForm JS to filter store dropdown when workspace changes | ✅ |

### 4.4 — Publish / Unpublish Actions

| # | Task | Status |
|---|------|--------|
| 4.4.1 | `publish_layer` admin action — for DRAFT layers: calls `client.publish_featuretype(...)`, verifies, sets `publishing_state=PUBLISHED` | ✅ |
| 4.4.2 | `unpublish_layer` admin action — for PUBLISHED layers: calls `client.delete_layer(workspace, layer_name)`, verifies deletion from GeoServer (`verify_featuretype` returns False), sets `publishing_state=UNPUBLISHED` | ✅ |
| 4.4.3 | Both actions: skip layers already in the target state with a warning message | ✅ |

### 4.5 — Delete Safety

**Layer vs Store asymmetry (by design):**
- **Store** — ALWAYS engine-first regardless of state (store has no publishing_state; if it was synced from GeoServer it exists there).
- **Layer** — engine-first ONLY if `publishing_state == PUBLISHED`. DRAFT/FAILED/UNPUBLISHED layers were never live in GeoServer, so Django-only delete is safe and correct.

| # | Task | Status |
|---|------|--------|
| 4.5.1 | Override `LayerAdmin.delete_model(request, obj)` — if `publishing_state == 'PUBLISHED'`: call `client.delete_layer(workspace, layer_name)` first, verify, then `obj.delete()` | ✅ |
| 4.5.2 | If engine deletion fails: abort, `self.message_user(request, "GeoServer delete failed: ...", messages.ERROR)` | ✅ |
| 4.5.3 | Override `delete_queryset` for bulk — same per-layer safety loop | ✅ |

**Milestone check:**
- Layer list shows workspace, store, geometry type, srid, publish state (colored) ✅
- title + description + srid editable; name/table/geometry readonly ✅
- `save_model` syncs title/description to GeoServer for PUBLISHED layers ✅
- "Publish from PostGIS" custom view creates layer in GeoServer + Django ✅
- Duplicate publish blocked by `verify_featuretype()` pre-check ✅
- Delete goes GeoServer-first for PUBLISHED layers ✅

---

---

## PHASE 5 — Store Credential Management ✅

**Source:** `geoengine_features/tasks.md` §3.10
**Problem:** Stores synced FROM GeoServer have no password in Django (GeoServer REST API never exposes credentials). This causes PostGIS table preview, layer publish, and any direct DB operation to silently fail.

### 5.1 — Detect Stores Without Credentials

| # | Task | Status |
|---|------|--------|
| 5.1.1 | `has_password_badge` and `store_postgis_tables_view` credential check: **DO NOT use `bool(obj.password)`** (raw encrypted field) — use `obj.decrypted_password` with `try/except ValueError` so a corrupt token shows `✗ Missing` instead of `✓ Set`. **✅ Fixed in `admin.py` + `admin_views/store.py`.** | ✅ |
| 5.1.2 | Add `NoCredentialFilter(SimpleListFilter)` to `StoreAdmin.list_filter` — "Missing Password" shortcut shows only stores where `password` is blank | ✅ |
| 5.1.3 | On Store change form: if `store.store_type == 'postgis'` and `not store.decrypted_password` (use `try/except`) — render a yellow warning banner above the form: "⚠ No password stored. PostGIS table preview and layer publish will fail." | ✅ |

### 5.2 — Sync Service: Never Overwrite Passwords

**Source:** `geoengine_features/tasks.md` §4.4.6 — **this bug was already fixed in geo_console but must be verified in the admin sync path too.**

GeoServer REST API `GET /datastores` never returns credentials. When `sync_stores_for_workspace()` calls `Store.objects.update_or_create(defaults={...})`, if `password` is in `defaults` with value `''` it silently wipes the stored encrypted password.

| # | Task | Status |
|---|------|--------|
| 5.2.1 | Audit `sync_service.py` `sync_stores_for_workspace()` — confirm `password` is NOT in `defaults` dict passed to `update_or_create` | ✅ |
| 5.2.2 | Add regression test comment in `sync_service.py` above the `update_or_create` call: `# NEVER include password in defaults — GeoServer REST never returns credentials` | ✅ |
| 5.2.3 | Same audit for `sync_all_resources()` if it calls store creation internally | ✅ |

---

## PHASE 6 — Data Integrity: Layer Name Prefix ✅

**Source:** `geoengine_features/tasks.md` §1.6.8
**Problem:** GeoServer REST `GET /workspaces/{ws}/layers` returns layer names as `workspace:layername` (e.g. `vector:buildings`). Django's `Layer.name` stores only `layername`. Before the patch in `client.get_layers()`, every sync cycle would create a new `Layer` with name `vector:buildings` AND fail to find/delete the existing `buildings` record → "✓ Imported 1 · 1 removed" on every sync even with no real changes.

**Status of patch:** `client.get_layers()` already strips the prefix (patched 14 March 2026). However, Django DB may have **corrupted rows** created before the patch.

### 6.1 — One-Time Data Cleanup

| # | Task | Status |
|---|------|--------|
| 6.1.1 | Write management command `fix_layer_name_prefixes` — find all `Layer.objects.filter(name__contains=':')`, strip prefix, check for `(workspace, name)` duplicate before saving, log all changes | ✅ |
| 6.1.2 | Run command on dev DB, verify zero rows with `:` in name after | ✅ |
| 6.1.3 | Add integrity assertion to `sync_all_resources()` start: if any `Layer.name` contains `:` → `logger.warning(...)` with count (non-fatal) | ✅ |

### 6.2 — Audit Datastores for Same Issue

| # | Task | Status |
|---|------|--------|
| 6.2.1 | Check `client.get_datastores()` return values — do any store names include `workspace:` prefix? Log and confirm | ✅ |
| 6.2.2 | If yes: apply same strip logic in `client.get_datastores()` and add data cleanup for `Store.name` | ✅ |

---

## PHASE 7 — Layer Publish Robustness ✅

**Source:** `geoengine_features/tasks.md` §4.4.7, §4.4.8

### 7.1 — Duplicate Publish Error Message

**Problem:** When user tries to publish a table that is already published in GeoServer, the error message was "choose a different layer name" which was wrong — the conflict is on `table_name` (GeoServer featuretype identifier), not `layer_name` (display title).

| # | Task | Status |
|---|------|--------|
| 7.1.1 | `publish_postgis_view`: on `verify_featuretype()` returning `True` (pre-check), error message must be: `"Table '{table_name}' is already published in workspace '{workspace}'. Delete the existing layer first."` — conflict is on `table_name` (GeoServer featuretype ID), NOT `layer_name` (display title). **✅ Already correct in `admin_views/layer.py`.** | ✅ |
| 7.1.2 | `publish_layer` action pre-check behavior differs intentionally from the form path: if `verify_featuretype()` returns True the action **reconciles** (sets `publishing_state=PUBLISHED` with WARNING) rather than erroring — because the action is a bulk reconciliation tool, not a new-intent form. This is correct. No code change needed. | ✅ |
| 7.1.3 | `publish_postgis.html`: on form error for `table_name` field — render link to Layer list so user can find and delete the conflicting layer | ✅ |

### 7.2 — Form State Preservation on Error

**Problem:** After a failed publish (server-side error), the form returns blank — user must re-select everything from scratch.

| # | Task | Status |
|---|------|--------|
| 7.2.1 | `publish_postgis_view` POST: on any error, re-render form with `request.POST` values — all fields must retain their values | ✅ |
| 7.2.2 | `publish_postgis.html`: workspace dropdown must re-select correct workspace on re-render | ✅ |
| 7.2.3 | `publish_postgis.html`: store dropdown must trigger AJAX refill AND pre-select the previously selected store on page load (JS: if `{{ form.store.value }}` is set and workspace matches, pre-select it) | ✅ |

---

## PHASE 8 — Styles Admin ⬜

**Source:** `geoengine_features/tasks.md` §5
**Goal:** Manage GeoServer styles (SLD / MBStyle) from the admin panel — upload, validate, attach to layers, delete.

### Models needed

A `Style` model does not exist yet. Must be added to `geodata_engine/models.py`.

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID PK | |
| `name` | CharField | GeoServer style identifier |
| `format` | CharField choices: `sld`, `mbstyle` | |
| `workspace` | FK Workspace, nullable | null = global style |
| `geodata_engine` | FK GeodataEngine | |
| `file_content` | TextField | Raw SLD/MBStyle content |
| `description` | TextField | |
| `created_at`, `updated_at` | DateTimeField | |
| `created_by` | FK User | |

### 8.1 — Model & Migration

| # | Task | Status |
|---|------|--------|
| 8.1.1 | Add `Style` model to `geodata_engine/models.py` | ⬜ |
| 8.1.2 | Run `makemigrations geodata_engine` | ⬜ |

### 8.2 — GeoServer Client Methods

| # | Task | Status |
|---|------|--------|
| 8.2.1 | Add `upload_style(name, sld_content, workspace=None)` to `GeoServerClient` | ⬜ |
| 8.2.2 | Add `delete_style(name, workspace=None)` to `GeoServerClient` | ⬜ |
| 8.2.3 | Add `list_styles(workspace=None)` to `GeoServerClient` | ⬜ |
| 8.2.4 | Add `assign_style_to_layer(workspace, layer_name, style_name)` to `GeoServerClient` | ⬜ |

### 8.3 — StyleAdmin

| # | Task | Status |
|---|------|--------|
| 8.3.1 | Register `Style` with `StyleAdmin(ModelAdmin)` | ⬜ |
| 8.3.2 | `list_display`: `name`, `format`, `workspace_link`, `geodata_engine`, `created_at` | ⬜ |
| 8.3.3 | `StyleUploadForm` — `file` (FileField accepting `.sld`, `.json`), `name`, `format` (auto-detected from extension), `workspace` (optional) | ⬜ |
| 8.3.4 | SLD validation: parse XML, check root element is `<StyledLayerDescriptor>` — reject with error if not | ⬜ |
| 8.3.5 | MBStyle validation: parse JSON, check `version` key exists — reject with error if not | ⬜ |
| 8.3.6 | `save_model()`: validate file content → `client.upload_style()` → verify via `client.list_styles()` → persist Django | ⬜ |
| 8.3.7 | `delete_model()` / `delete_queryset()`: `client.delete_style()` first → verify → Django delete | ⬜ |
| 8.3.8 | Custom action `assign_style_to_layer` — shows intermediate form to pick target layer, calls `client.assign_style_to_layer()` | ⬜ |

**Milestone check:**
- SLD and MBStyle validated before upload ⬜
- Style uploaded to GeoServer, verified, saved in Django ⬜
- Delete goes GeoServer-first ⬜
- Style can be assigned to a layer via admin action ⬜

---

## PHASE 9 — File Upload → PostGIS → Publish Pipeline ⬜

**Source:** `geoengine_features/tasks.md` §4.5
**Goal:** Accept file uploads (GeoJSON, GeoPackage, Shapefile), inspect metadata, ingest to PostGIS, publish as GeoServer layer.
**Note:** Do NOT implement until Phase 4–8 are fully verified in production. This phase requires Django-Q (Phase 10).

### Supported formats
- GeoJSON (`.geojson`, `.json`)
- GeoPackage (`.gpkg`)
- Shapefile (`.zip` containing `.shp`, `.dbf`, `.shx`, `.prj`)

### Inspection outputs (metadata shown to user before ingest)

| Property | Source |
|----------|--------|
| Geometry type | GDAL / OGR |
| CRS (EPSG) | GDAL |
| Feature count | GDAL |
| Bounding box | GDAL |
| Attribute list | GDAL |

### 9.1 — Inspection

| # | Task | Status |
|---|------|--------|
| 9.1.1 | Add `FileInspectionForm` to `admin_forms.py` — file upload + workspace + store + layer name | ⬜ |
| 9.1.2 | `inspect_upload_view(request)` — accepts file, runs GDAL/OGR inspection, returns JSON metadata | ⬜ |
| 9.1.3 | If GDAL not available: return informative error, do not crash silently | ⬜ |

### 9.2 — Ingest to PostGIS

| # | Task | Status |
|---|------|--------|
| 9.2.1 | `ingest_to_postgis(file_path, store, table_name, target_srid)` service function | ⬜ |
| 9.2.2 | Use `ogr2ogr` or GeoPandas `to_postgis()` — confirm GDAL available in container | ⬜ |
| 9.2.3 | Use parameterized queries only — never string interpolation for table/schema names (use `psycopg2.sql.Identifier`) | ⬜ |
| 9.2.4 | After ingest: verify table exists in PostGIS before calling GeoServer publish | ⬜ |

### 9.3 — Publish

| # | Task | Status |
|---|------|--------|
| 9.3.1 | Reuse `publish_featuretype()` from Phase 4 — no duplication | ⬜ |
| 9.3.2 | On publish failure: drop the ingested PostGIS table (cleanup) — do not leave orphan tables | ⬜ |

---

## PHASE 10 — Async Jobs (Django-Q) ⬜

**Source:** `geoengine_features/tasks.md` §6
**Goal:** Long-running operations (sync, file ingest, publish) should not block HTTP responses. Track status in admin.
**Prerequisite:** Phases 1–8 must be stable before introducing async.

### 10.1 — Setup

| # | Task | Status |
|---|------|--------|
| 10.1.1 | Add `django-q2` to `pyproject.toml` (`django-q` is abandoned, use `django-q2`) | ⬜ |
| 10.1.2 | Add `django_q` to `INSTALLED_APPS` in `base.py` | ⬜ |
| 10.1.3 | Configure `Q_CLUSTER` in `settings/base.py` — ORM broker (no Redis/Rabbit dependency for dev) | ⬜ |
| 10.1.4 | Run `makemigrations` + `migrate` for django_q tables | ⬜ |
| 10.1.5 | Add `qcluster` management command to Docker entrypoint (separate process or `manage.py qcluster`) | ⬜ |

### 10.2 — Wrap Admin Operations

| # | Task | Status |
|---|------|--------|
| 10.2.1 | Move `sync_engines` action to `async_task('sync_service.sync_all_resources', engine.pk)` | ⬜ |
| 10.2.2 | Move `publish_layer` action to async task | ⬜ |
| 10.2.3 | Move file ingest pipeline (Phase 9) to async task | ⬜ |
| 10.2.4 | After queuing: `self.message_user(request, "Job queued. Check Jobs to monitor progress.", messages.INFO)` | ⬜ |

### 10.3 — Job Visibility in Admin

| # | Task | Status |
|---|------|--------|
| 10.3.1 | Register Django-Q's `Success`, `Failure`, `Schedule` models in admin with readable list_display | ⬜ |
| 10.3.2 | `list_display`: task name, started_at, stopped_at, success, result (truncated) | ⬜ |
| 10.3.3 | Add link from engine change form to "Recent Jobs for this engine" (filter by `group=engine.pk`) | ⬜ |

---

## CROSS-CUTTING CONCERNS

### Security

| # | Task | Status |
|---|------|--------|
| X.1 | All custom admin views require `@staff_member_required` decorator | ⬜ |
| X.2 | All custom admin views check `request.user.is_staff` explicitly before any GeoServer call | ⬜ |
| X.3 | Password fields: `PasswordInput(render_value=False)` everywhere — never pre-populate | ⬜ |
| X.4 | Custom admin AJAX endpoints return 403 for non-staff requests | ⬜ |

### Error Handling

| # | Task | Status |
|---|------|--------|
| E.1 | All `GeoServerConnectionError` caught → `self.message_user(request, "Engine unreachable: ...", messages.ERROR)` | ⬜ |
| E.2 | All `GeoServerPublishError` caught → `self.message_user(request, "GeoServer operation failed: ...", messages.ERROR)` | ⬜ |
| E.3 | Never `except Exception: pass` anywhere in admin code | ⬜ |
| E.4 | Custom admin views return `JsonResponse({'error': '...'}, status=4xx)` on failure — never stack traces | ⬜ |

### Admin UX Conventions

| # | Task | Status |
|---|------|--------|
| U.1 | All list columns with computed values: `short_description` set, `admin_order_field` set where applicable | ⬜ |
| U.2 | All custom admin views extend `admin/base_site.html` for consistent admin chrome | ⬜ |
| U.3 | Destructive actions (sync, delete, publish) show Django admin confirmation intermediate page before execution | ⬜ |
| U.4 | `list_per_page = 25` on all ModelAdmin classes | ⬜ |
| U.5 | All 4 ModelAdmin classes registered under `@admin.register(Model)` decorator — no `admin.site.register()` calls | ⬜ |

---

## FILE MAP

```
tosca_api/apps/geodata_engine/
  admin.py           ← @admin.register for all 4 models
  admin_actions.py   ← sync_engines, sync_workspaces, publish_layer, unpublish_layer, test_connection, set_as_default
  admin_forms.py     ← PublishPostGISForm
  admin_views.py     ← engine_test_connection_view, engine_sync_view,
                        store_postgis_tables_view, store_tables_for_workspace,
                        publish_postgis_view
templates/
  admin/
    geodata_engine/
      geodataengine/
        change_form.html    ← engine change form with Test Connection + Sync buttons
      store/
        change_form.html    ← store change form with Preview PostGIS Tables button
      layer/
        change_list.html    ← layer changelist with Publish from PostGIS button
        publish_postgis.html ← custom publish form page
```

---

## PROGRESS SUMMARY

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Engine Admin | ✅ Done |
| 2 | Workspace Admin | ✅ Done |
| 3 | Store Admin + Credential Management | ✅ Done |
| 4 | Layer Admin + PostGIS Publish | ✅ Done |
| 5 | Store Credential Management (sync-safe passwords) | ⬜ Not started |
| 6 | Data Integrity: Layer Name Prefix Cleanup | ⬜ Not started |
| 7 | Layer Publish Robustness (duplicate error, form state) | ⬜ Not started |
| 8 | Styles Admin (SLD/MBStyle upload + assign) | ⬜ Not started |
| 9 | File Upload → PostGIS → Publish Pipeline | ⬜ Not started |
| 10 | Async Jobs (Django-Q) | ⬜ Not started |

**Prerequisites before starting:**
- Read `tosca_api/apps/geodata_engine/admin.py` — understand what already exists there
- Read `tosca_api/apps/geodata_engine/engine_factory.py` — client creation pattern
- Read `tosca_api/apps/geodata_engine/sync_service.py` — available sync functions
- Read `tosca_api/apps/geodata_engine/api/views.py` `publish_postgis` action (lines ~690–845) — this is the reference implementation for Phase 4.3

**Next action:** Phase 1 — Engine Admin.
**Last updated:** 16 March 2026 — Initial task breakdown for geodata_engine Django admin panel.
