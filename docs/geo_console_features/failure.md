## F-011 — Inline event handlers in HTML templates: behavior scattered across 5 template files
**Date:** 15 March 2026
**Discovered:** Template audit — 12 inline handlers found: 3× `onchange`, 4× `onsubmit`, 1× `onclick` with inline confirm/submit logic.

**What happened:**
As console pages were built incrementally, quick behavior was written directly into HTML attributes:
- `onchange="this.form.submit()"` on `<select>` (engine/workspace filter)
- `onsubmit="return confirm('Delete ...\nThis also removes it from GeoServer.')"` on delete forms
- `onclick="return confirm('Delete engine ...')"` on a button

This scattered JS behavior across 5 different template files with no single place to update or test.
Multi-line confirm messages required escaped `\'` and `\n` in HTML attribute values — noisy and error-prone.

**Why it was wrong:**
Inline event handlers cannot be extracted, reused, or linted. They couple behavior to markup.
Any wording change to a confirm message required hunting across template files.
`onsubmit="return confirm(...)"` with escaped quotes inside Django template strings is a maintenance trap.

**Fix:**
Created `console.js` with a single `DOMContentLoaded` listener. All behavior driven by `data-*` attributes:
- `data-autosubmit` on `<select>` → auto-submits closest form on `change`
- `data-confirm="msg&#10;second line"` on `<form>` → `window.confirm()` before submit (`&#10;` = newline)
- `data-confirm-click="msg"` on any element → `window.confirm()` before click

Intentionally kept as `onclick`: only `syncEngine(id)`, `quickSync(id)`, `testConnection()` — these are calls to functions defined in external JS files, not inline logic.

**Rule going forward:**
No `onchange=`, `onsubmit=`, `onclick=` with inline JS expressions in any template.
Use `data-*` attributes + handler in `console.js`.
`onclick="fn(runtimeArg)"` is acceptable only when `fn` is defined in an external `.js` file.

---

## F-010 — `{% block page_js %}` override in child templates silently dropped `console.js`
**Date:** 15 March 2026
**Discovered:** After adding `console.js` to `geo_console/base.html`, `data-autosubmit` and `data-confirm` behaviors were not working on pages that had `{% block page_js %}` overrides.

**What happened:**
`geo_console/base.html` loads `console.js` inside `{% block page_js %}`. Any child template that also defines `{% block page_js %}` **completely replaces** that block's content — including the `console.js` `<script>` tag. Django template block override is total, not additive.

Five child templates (`layer_publish.html`, `engine_detail.html`, `engine_form.html`, `store_create.html`, `geodata_console.html`) all had `{% block page_js %}` definitions, so `console.js` never loaded on any of those pages.

**Why it was silent:**
The rest of the page rendered normally. `console.js` simply never loaded — no JS error, no Django error, no server-side warning. The only symptom was that `data-autosubmit` and `data-confirm` had no effect.

**Fix:**
Added `{% block page_extra_js %}{% endblock %}` inside the base's `{% block page_js %}` after the `console.js` `<script>` tag:
```django
{% block page_js %}
<script src="{% static 'geo_console/js/console.js' %}"></script>
{% block page_extra_js %}{% endblock %}
{% endblock %}
```
All five child templates migrated from `{% block page_js %}` → `{% block page_extra_js %}`.
`console.js` now always loads first on every console page; child scripts append after it.

**Rule going forward:**
Never load a shared/required script inside a `{% block %}` that child templates might override.
Always split: the outer block loads the shared script unconditionally, a nested `page_extra_js` block is the extension point for children.
This mirrors how `page_css` + `page_extra_css` already works.

---

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

## F-007 — `delete_datastore` method does not exist on `Geoserver` object
**Date:** 15 March 2026
**Discovered:** Store silme "Delete failed: 'Geoserver' object has no attribute 'delete_datastore'" hatası verdi.

**What happened:**
`GeoServerClient.delete_store()` içinde `self._client.delete_datastore(datastore=store, workspace=workspace)` çağrılıyordu.
Oysa lokal `geoserver-rest` kütüphanesinde bu method hiç yok — doğru method adı `delete_featurestore(featurestore_name, workspace)`.
Bu method `recurse=true` parametresini kendisi handle ediyor ve string döndürüyor (dict değil).
Dolayısıyla ardından gelen `validate_response()` çağrısı da gereksizdi.

**Why it was silent:**
Unit test yoktu, sadece gerçek GeoServer üzerinde store silmeye çalışınca ortaya çıktı.
Pyproject'te `geoserver-rest` pip package olarak değil lokal subdirectory olarak mount edildiği için IDE method completion da çalışmıyordu.

**Fix:**
`delete_datastore(...)` → `delete_featurestore(featurestore_name=store, workspace=workspace)`
`validate_response()` çağrısı kaldırıldı — kütüphane başarısızlıkta `GeoserverException` raise ediyor, success'te string döndürüyor.
Yanlış method ve gereksiz validation kodu temizlendi.

**Rule going forward:**
Yeni bir `self._client.*` çağrısı yazmadan önce `geoserver-rest/geo/Geoserver.py` içinde `grep 'def method_name'` ile method adını doğrula.
Methodun return type'ına dikkat et — bazıları string, bazıları dict döndürüyor. `validate_response()` yalnızca dict dönen methodlarda kullan.

---

## F-008 — `{% block page_scripts %}` yanlış block adı — tüm JS sessizce silindi
**Date:** 15 March 2026
**Discovered:** Layer publish formunda workspace seçilince store dropdown'ı hiç dolmuyordu. Browser'da JS yoktu.

**What happened:**
`templates/geo_console/layer_publish.html` içinde JS bloğu `{% block page_scripts %}` olarak tanımlanmıştı.
Oysa inheritance zinciri: `base.html` → `geo_console/base.html` → `layer_publish.html`.
`base.html` sadece `{% block page_js %}` tanımlıyor — `page_scripts` adında bir block yok.
Django bilinmeyen block'ları sessizce yok sayar, extend ederken exception fırlatmaz.
Sonuç: tüm template render edildi, JS kodu hiç HTML'e dahil edilmedi, browser'a tek satır JS gitmedi.

**Why it was silent:**
Django template engine unknown block'ları tamamen drop ediyor — render sırasında hiçbir uyarı yok.
Sayfanın geri kalanı normal göründüğü için JS yokluğu hemen anlaşılmadı. `stores-data` JSON script tag'i de `{% block content %}` içinde olduğu için o render ediliyordu, yanıltıcıydı.

**Fix:**
`{% block page_scripts %}` → `{% block page_js %}` (opening tag'i değiştirmek yeterli, closing `{% endblock %}` aynı kalır).

**Rule going forward:**
Yeni template yazarken kullandığın `{% block %}` adının inheritance zincirinde gerçekten `base.html`'de tanımlı olduğunu kontrol et.
Mevcut bloklar: `title`, `page_css`, `content`, `sidebar_subnav`, `topbar_left`, `topbar_right`, `page_js`.
Bunlar dışında bir block adı yazmak sessiz hata demektir.

---

## F-009 — GeoServer sync'den gelen store'larda Django'da password kaydedilmiyor
**Date:** 15 March 2026
**Discovered:** PostGIS tablo listesi alınmaya çalışılınca `fe_sendauth: no password supplied` hatası.

**What happened:**
GeoServer'dan pull sync yapılınca `Store` kaydı oluşturuluyor: `host`, `port`, `database`, `username`, `schema` GeoServer REST API'den alınıyor.
Ama GeoServer REST API hiçbir zaman credentials expose etmiyor — `password` alanı boş kalıyor.
`postgis_inspector.get_geometry_tables()` sonra `store.decrypted_password` ile bağlanmaya çalışıyor, boş password yüzünden PostGIS bağlantısı reddediyor.
Hata mesajı raw SQLAlchemy stack trace olarak kullanıcıya dönüyordu.

**Why it was wrong:**
Sistem tasarımı açısından: GeoServer `password` field'ını kasten saklamaz, bu güvenlik gereği. Django'nun bu credentials'ı GeoServer'dan beklememesi gerekiyordu.
UX açısından: stack trace doğrudan API response'a giriyordu, console kullanıcısı için anlamsız.

**Fix:**
1. `StoreViewSet.postgis_tables()`: bağlantı denemesinden önce `store.decrypted_password` kontrol ediyor. Boşsa açık Türkçe/İngilizce mesajla `400` dönüyor.
2. Store Detail sayfası eklendi (`/console/stores/<uuid>/`): sync'den gelen store'larda sarı banner gösteriyor — kullanıcı password'u buradan girebiliyor.
3. `StoreSerializer.has_password` computed field: şifreli password var mı yok mu boolean olarak serialize ediliyor.
4. `GeoConsoleAPIClient.update_store()`: PATCH metodu eklendi — store detail formundan credential güncellemesi yapıyor.

**Rule going forward:**
GeoServer'dan sync edilen herhangi bir credential alanının (password, secret key, vb.) Django'da boş olabileceğini her zaman varsay.
Bu alanları kullanan herhangi bir işlem öncesinde boşluk kontrolü yap ve kullanıcıya açıklayıcı hata mesajı ver.
Asla raw exception stack trace'i API response'a veya template'e sızdırma.

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
