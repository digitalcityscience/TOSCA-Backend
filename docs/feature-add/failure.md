## F-006 — Integration testi için `TestCase` kullanıldı — test DB izin hatası
**Date:** 14 March 2026
**Discovered:** `make test-console-crud` çalıştırılınca `permission denied for table django_migrations`

**What happened:**
GeoServer + DRF üzerinden CRUD yapan entegrasyon testi `django.test.TestCase` ile yazıldı.
`TestCase` her çalışmada `test_tosca` adında yeni bir DB yaratmaya çalışır.
DB user'ı (`tosca_api`) bu yetkiye sahip değil — `permission denied` hatası.

**Why it was wrong:**
Bu test tip olarak bir entegrasyon scripti: gerçek GeoServer'a bağlanıyor, servis katmanını test ediyor, kendi yarattığı her şeyi sonda temizliyor.
DB izolasyonuna ihtiyacı yok. `TestCase` sadece model davranışlarını test eden unit testler için uygundur.

**Fix:**
`TestCase`'i kaldır, `integration_test.py` ile aynı pattern'ı kullan:
```python
import django
django.setup()
# APIClient ile çağrılar, sonda cleanup
```
Artık `manage.py test` çağrılmıyor — script direkt `uv run python test_console_crud.py` ile koşuyor.

**Rule going forward:**
Gerçek servislerle (GeoServer, PostGIS) çalışan testler için Django `TestCase` kullanma.
Düz script yaz: `django.setup()` + `APIClient` yeterli.
`TestCase` yalnızca mock/unit testlerde, DB transaction isolation gerektiğinde kullan.

---

## F-001 — `get_datastores()` returned only names, not connection details
**Date:** 14 March 2026
**Discovered:** Sync worked without error but stored `host=localhost, database=gis` for every store.

**What happened:**
`client.get_datastores()` called `GET /rest/workspaces/{ws}/datastores.json` which only returns
`{name, href}` per store. The sync service then wrote hardcoded defaults (`host='localhost'`,
`database='gis'`) into every `Store` record. Real GeoServer connection params were never persisted.

**Why it was silent:**
No exception was raised. The Store model accepted the fake values. The only symptom was that
every synced store had identical, wrong connection info in the DB.

**Fix:**
`get_datastores()` now calls `GET /rest/workspaces/{ws}/datastores/{name}.json` per store and
parses the `connectionParameters.entry[]` list to extract real `host/port/database/user/schema`.
Added `get_datastore_detail()` as the underlying helper.

**Rule going forward:**
GeoServer list endpoints (`*.json` collections) return stubs only. Always call the detail
endpoint (`/rest/…/{name}`) to get actual field values before writing to Django models.

---

## F-002 — `get_layers()` had no store affiliation — created placeholder stores
**Date:** 14 March 2026
**Discovered:** Every layer sync created a phantom `Store` record with fake connection info.

**What happened:**
`client.get_layers()` called `GET /rest/workspaces/{ws}/layers` which returns `{name}` only —
no store information. The sync service tried `Store.objects.get(name=store_name)` with an
empty string, failed, then created a placeholder `Store` with `host='localhost'`.
Result: one real store + N phantom stores in the DB after sync.

**Fix:**
`get_layers()` now traverses store → featuretypes:
1. `GET /rest/workspaces/{ws}/datastores.json` → store names
2. For each store: `GET /rest/workspaces/{ws}/datastores/{store}/featuretypes.json` → layer names
Each returned layer dict now carries `store_name`.
Sync service skips layers whose store is not yet in Django (instead of creating phantoms).

**Rule going forward:**
Never create placeholder / auto-generated objects to satisfy a FK constraint during sync.
If the parent doesn't exist yet, skip and log a warning. Phantom data is harder to debug
than a missing record.

---

## F-003 — Engine create/update did NOT trigger sync — workspaces/stores/layers invisible
**Date:** 14 March 2026
**Discovered:** Adding a new GeoServer engine via the console left Django with zero related
Workspace/Store/Layer records until the user manually clicked "Sync Now".

**What happened:**
`GeodataEngineViewSet` used the default DRF `perform_create()` which only saved the engine row.
No sync was triggered. The engine existed in the DB but was completely empty from Django's
perspective.

**Fix:**
Overrode `create()` and `update()` in `GeodataEngineViewSet`. Both now call
`_trigger_initial_sync()` after persisting the engine. Sync failure does NOT roll back the
engine save — the engine is always persisted, sync result is returned alongside in the response.

**Rule going forward:**
Any resource that has a remote-state counterpart (engine → GeoServer) must trigger a pull
sync immediately on create/update. The user should never need a manual action to see the
initial state.

---

## F-004 — `ActiveEngineMiddleware` put UI session state in the domain app
**Date:** 14 March 2026
**Discovered:** During middleware review before Phase 2.

**What happened:**
`geodata_engine/middleware.py` stored `active_geodata_engine_id` in session and filtered
admin list views by it. This meant:
- Adding a second engine made all its data invisible in admin (wrong engine in session)
- Domain app (`geodata_engine`) was holding UI state — wrong layer responsibility
- Every `/admin/` request fired 2 extra DB queries even when result was unused

**Fix:**
- Removed `ActiveEngineMiddleware` from `MIDDLEWARE` in `settings/base.py`
- Removed `active_engine_context` from `CONTEXT_PROCESSORS`
- Removed all `get_active_engine()` calls from `admin.py`
- Admin list views now show all records for all engines
- `middleware.py` kept but marked INACTIVE with note for Phase 2

**Rule going forward:**
Engine selection is a UI concern. It belongs in `geo_console` as a URL query param
(`?engine=<uuid>`), not as middleware session state in the domain app.
`geodata_engine` must remain stateless at the request level.

---

## F-005 — Layer name prefix bug: `workspace:layername` written to DB
**Date:** 14 March 2026 (partial patch earlier, full fix this session)

**What happened:**
GeoServer `GET /rest/workspaces/{ws}/layers` returns names as `vector:buildings`.
Django `Layer.name` stores only `buildings` (enforced by `unique_together`).
Before the patch, sync created `Layer(name='vector:buildings')` every cycle, then
deleted `Layer(name='buildings')` as "not in GeoServer" — an infinite churn loop.

**Symptoms:**
UI showed "✓ Imported 1 layer · 1 removed" on every sync with no real changes.

**Fix (layered):**
1. `client.get_layers()`: strip `workspace:` prefix on read.
2. `sync_all_resources()`: integrity cleanup block at start — finds `Layer.name` containing `:`
   and strips/deduplicates before sync runs.
3. `GeodataEngineViewSet.create/update`: auto-sync on engine save so fresh engines start clean.

**Rule going forward:**
Never write GeoServer's composite `workspace:name` format into any Django model field.
Strip at the client layer (`client.py`) — not in the sync service, not in the view.
The client is the translation boundary between GeoServer's wire format and our domain model.
