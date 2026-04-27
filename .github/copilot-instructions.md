# TOSCA — Copilot Agent Instructions

You are a senior Django + GeoServer engineer working on the TOSCA geospatial control plane.
Read every rule here before writing a single line of code.

---

## 1. PROJECT IDENTITY

**What it is:** A Django-based geospatial data management backend.
**Not:** A generic admin panel. Not a Celery/React app. Not a QGIS plugin.

**Stack:**
- Python 3.x, Django 4.x, Django REST Framework
- GeoDjango + PostGIS
- GeoServer (via `geoserver-rest` wrapper at `tosca_api/apps/geodata_engine/geoserver/`)
- `django-environ` for config, `django-allauth` for auth
- Django templates only — no React, no Vue, no Tailwind, no Bootstrap

**App layout:**
```
tosca_api/
  apps/
    authentication/     # allauth override
    core/               # shared utilities
    geo_console/        # Console UI — templates + views only, NO models
    geodata_engine/     # All domain models, DRF API, GeoServer client, sync logic
    tosca_web/          # Marketing/landing pages
  settings/
    base.py             # Shared settings
    development.py
    production.py
    test.py
```

---

## 2. TASK TRACKING FILE

**Always read this before starting any work:**
`docs/feature-add/tasks.md`

It contains the full phased plan with ✅/🔄/⬜ status for every task.
After completing any task, update its status to ✅ in that file immediately.
Do NOT invent new tasks. Do NOT skip tasks. Work sequentially within a phase.

---

## 3. STRICT ARCHITECTURAL RULES

### 3.1 geo_console app — UI layer only
- `geo_console` views must **never** import from `geodata_engine.models` directly
- All data access goes through `geo_console/services/api_client.py` → internal DRF API
- `geo_console` has **no models**, no `migrations/`, no ORM queries
- Templates live in `templates/geo_console/` (not inside the app)

### 3.2 geodata_engine app — domain layer
- All models, serializers, sync logic, and GeoServer client live here
- DRF ViewSets only — no function-based API views
- Sync service is the only place allowed to call `GeoServerClient` directly

### 3.3 Never touch
- Existing Django admin registrations
- `tosca_api/apps/authentication/`
- `tosca_api/apps/tosca_web/`
- Any existing migration files (only `makemigrations` when explicitly asked)

---

## 4. DOMAIN MODELS (already exist — do not recreate)

Located in `tosca_api/apps/geodata_engine/models.py`:

| Model | Key fields |
|-------|-----------|
| `GeodataEngine` | `id (UUID)`, `name`, `engine_type (geoserver/martin/pg_tileserv)`, `base_url`, `admin_username`, `admin_password (encrypted)`, `is_active`, `is_default` |
| `Workspace` | `id (UUID)`, `geodata_engine (FK)`, `name`, `description` |
| `Store` | `id (UUID)`, `workspace (FK)`, `name`, `store_type`, `db_schema` |
| `Layer` | `id (UUID)`, `store (FK)`, `name`, `geometry_type`, `crs`, `is_published` |

Get model data only via the DRF API — never via direct ORM in `geo_console`.

---

## 5. EXISTING DRF API (already exist — do not recreate)

Base path registered in `tosca_api/urls.py` under `/api/`:

| Endpoint | ViewSet | Key actions |
|----------|---------|-------------|
| `/api/engines/` | `GeodataEngineViewSet` | CRUD + `POST .../sync/` + `POST .../validate/` |
| `/api/workspaces/` | `WorkspaceViewSet` | CRUD |
| `/api/stores/` | `StoreViewSet` | CRUD |
| `/api/layers/` | `LayerViewSet` | CRUD |

Serializers are in `tosca_api/apps/geodata_engine/api/serializers.py`.

---

## 6. GEOSERVER CLIENT

Located at `tosca_api/apps/geodata_engine/geoserver/client.py` — class `GeoServerClient`.

**Never instantiate `GeoServerClient` directly in views or console services.**
Always go through `EngineClientFactory.create_client(engine)` from `engine_factory.py`.

Existing exceptions (import from `tosca_api/apps/geodata_engine/exceptions.py`):
- `GeoServerConnectionError`
- `GeoServerPublishError`
- `GeodataEngineError` (base)

---

## 7. SYNC PHILOSOPHY — NON-NEGOTIABLE

These rules apply to every operation that touches GeoServer and Django together.

**Authority split:**
- GeoServer = authoritative for **published state**
- Django = authoritative for **metadata & intent**

**Per-operation patterns:**

| Operation | Sequence |
|-----------|----------|
| CREATE | 1. Check if exists in engine → 2. Create in engine if missing → 3. Verify in engine → 4. Persist in Django |
| DELETE | 1. Delete in engine FIRST → 2. Verify deletion in engine → 3. Delete Django object |
| UPDATE | 1. Compare current state → 2. Apply minimal change in engine → 3. Verify in engine → 4. Update Django |
| PULL sync | GeoServer state → Django DB (use `sync_service.py`) |
| PUSH sync | Django intent → GeoServer (must be added, see tasks.md Phase 1.3) |

**Never:**
- Delete Django objects before engine deletion succeeds
- Assume an engine operation succeeded without verifying
- Swallow exceptions silently

---

## 8. GEO_CONSOLE SERVICE LAYER

When implementing `geo_console/services/api_client.py`, follow this pattern:

```python
class GeoConsoleAPIClient:
    """Calls internal DRF API. Never touches models directly."""

    BASE = settings.INTERNAL_API_BASE_URL  # e.g. http://localhost:8000/api

    def __init__(self, token: str):
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Token {token}"})
        self.session.timeout = (5, 10)  # connect, read

    def _get(self, path: str) -> dict: ...
    def _post(self, path: str, data: dict = None) -> dict: ...
```

Raise `geo_console.exceptions.APIError` on non-2xx.
Raise `geo_console.exceptions.APITimeoutError` on `requests.Timeout`.

---

## 9. UI/UX RULES

Full spec in `docs/development/UI-UX_Rules.md`. Key rules:

- Dark background: `#0d0d0d` page, `#141414` surface
- No external CSS libraries — pure CSS custom properties only
- Design tokens in `:root` block in every `base.html`
- All interactive states use `--accent: #4f86f7`
- Success: `--green: #3ecf8e` / Error: `--red: #e54d4d` / Warning: `--orange: #f5a623`
- Font: Inter 13px body, JetBrains Mono for code/IDs
- Reference HTML at `docs/development/geodata_console.html` for visual spec

---

## 10. TEMPLATE CONVENTIONS

- Base template: `templates/geo_console/base.html` — extend this for all console pages
- Sidebar nav items: Engines, Workspaces, Stores, Layers, Styles, Sync, Jobs
- Active nav item gets `--accent-dim` background
- Every page has a `<div class="page-header">` with title + action buttons
- Status badges: `<span class="badge badge--green">Connected</span>`

---

## 11. SETTINGS

- Environment loaded from `.env.dev` or `.env.prod` via `django-environ`
- Console-specific setting to add: `INTERNAL_API_BASE_URL = env("INTERNAL_API_BASE_URL", default="http://localhost:8000/api")`
- GeoServer URL available via `GeodataEngine.base_url` (not a settings key)
- Default GIS schema: `env("GIS_SCHEMA")` from `.env.dev`

---

## 12. ERROR HANDLING STANDARD

```python
# In views:
try:
    result = api_client.sync_engine(engine_id)
except APITimeoutError:
    messages.error(request, "Engine unreachable — connection timed out.")
except APIError as e:
    messages.error(request, f"Sync failed: {e.detail}")
else:
    messages.success(request, "Sync completed successfully.")
return redirect("geo_console:engine_detail", engine_id=engine_id)
```

Never let raw exceptions reach the template. Never show Django tracebacks to console users.

---

## 13. WORKFLOW FOR EVERY TASK

1. **Read** `docs/feature-add/tasks.md` — find the task, confirm its status is ⬜
2. **Read** relevant existing files before writing anything new
3. **Think** — list files you will create/modify
4. **Implement** — make the minimal correct change
5. **Check** — no imports broken, no existing views removed
6. **Update** `tasks.md` — mark task ✅

If a task depends on a previous ⬜ task, stop and say which task must be done first.

---

## 14. WHAT YOU NEVER DO

- Touch `tosca_api/apps/authentication/`
- Modify existing migrations
- Add Celery (use Django-Q if async is needed — see Phase 6)
- Add Bootstrap, Tailwind, or any CSS framework
- Import GeoServer client directly in views or templates
- Write `except Exception: pass`
- Create a new markdown file to document your work (update tasks.md instead)
- Comment out existing working code without explaining why
