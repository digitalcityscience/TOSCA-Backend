# TOSCA — Milestone Notes

---

## 15 March 2026 — Template Static File Cleanup (Phase 4.6)

### Tamamlanan İşler

**1. CSS mimarisi — 1036 satırlık `console.css` parçalandı**
- `console.css` (~400 satır) — ortak temel; header, button, badge, card, form, engine selector
- `layers.css` (~369 satır) — layer list tablosu (`table-card`, `layer-table`, `lp-*` publish form namespace)
- `stores.css` — store-specific stiller (`.form-section-note` vb.)
- `geo_console/base.html`'e `{% block page_extra_css %}` hook eklendi
- Tüm domain template'lere domain CSS'i `{% block page_extra_css %}` üzerinden yükleniyor
- Tüm 12 template'den `style=""` attribute'ları kaldırıldı, yerlerine utility class'lar eklendi
- `docs/development/UI-UX_Rules.md`'e §11 CSS Architecture bölümü eklendi (400 satır hard cap, dosya yapısı tablosu, loading pattern örneği)

**2. JS extraction — tüm inline `<script>` bloklar dışa taşındı**
- `static/geo_console/js/layer_publish.js` — publish form logic: `filterStores`, `loadTables`, `selectTable`, `selectCRS`, `applyCustomCRS`, `updateGeomDisplay`
- `static/geo_console/js/store_form.js` — store type toggle; `data-store-type-id` dataset pattern ile Django template var'ı external JS'e iletiliyor
- `static/geo_console/js/console.js` — tüm console sayfaları için paylaşılan `DOMContentLoaded` handler'ları
- `geo_console/base.html`'e `{% block page_extra_js %}` hook eklendi (`{% block page_js %}` içinde, `console.js`'den sonra)
- Tüm 5 alt template `{% block page_js %}` → `{% block page_extra_js %}`'e taşındı

**3. Inline event handler temizliği — 9 handler kaldırıldı**
- 3× `onchange="this.form.submit()"` → `data-autosubmit` (workspace/store/layer engine selector + layer workspace filter)
- 4× `onsubmit="return confirm(...)"` → `data-confirm="msg&#10;second line"` (workspace/store/layer/layer-detail delete form'lar)
- 1× `onclick="return confirm(...)"` → engine delete butondan `<form data-confirm="...">` olarak taşındı
- Kalan `onclick`: yalnızca `syncEngine(id)`, `quickSync(id)`, `testConnection()` — external JS'de tanımlı function call'lar, inline logic değil

**4. Block inheritance fix (F-010)**
- Alt template'ler `{% block page_js %}` override edince `console.js` sessizce drop ediliyordu
- Çözüm: base'de `{% block page_js %}` köke sabit `console.js` yüklüyor, ardından `{% block page_extra_js %}` açıyor
- Alt template'ler artık yalnızca `{% block page_extra_js %}` kullanıyor → `console.js` her sayfada garanti yükleniyor

---

## 14 March 2026 — Static File Refactor + Console CRUD Test

### Tamamlanan İşler

**1. Template inline CSS/JS → static dosyalara taşındı**
- `static/geo_console/css/console.css` oluşturuldu — tüm 5 geo_console template'inin inline `<style>` bloklarından derlendi
- `static/geo_console/js/engine_detail.js` oluşturuldu — `engine_detail.html`'den inline sync fonksiyonları
- `static/geo_console/js/engine_form.js` oluşturuldu — `engine_form.html`'den inline test connection IIFE
- Tüm 5 template (`geodata_console.html`, `engine_detail.html`, `engine_form.html`, `workspace_list.html`, `workspace_create.html`) `{% block page_css %}` → `<link>` tag'e dönüştürüldü
- `{% block page_js %}` inline script → `<script src="...">` referansına dönüştürüldü

**2. Console CRUD entegrasyon testi yazıldı**
- `tosca_api/apps/geodata_engine/tests/test_console_crud.py` — düz Python script, `manage.py test` yok, test DB yok
- Engine (`pytest-geoserver`) oluştur → workspace (`pytest-workspace`) oluştur → workspace sil → engine sil
- Her adımda HTTP status ve `success` alanı kontrol ediliyor, hata varsa renkli `❌` ile çıkıyor
- `make test-console-crud` ile Docker içinde çalışıyor

---

## 14 March 2026 — Sync Architecture Fix + Admin Cleanup

### Tamamlanan İşler

**1. Auto-sync on engine create/update**
- `GeodataEngineViewSet.create()` ve `update()` override edildi — engine kaydedilir kaydedilmez `sync_all_resources()` tetikleniyor
- `_trigger_initial_sync()` helper eklendi — sync başarısız olsa bile engine kaydı geri alınmıyor, hata response'a ekleniyor

**2. `client.py` — Gerçek veri döndüren sync client**
- `get_datastores()`: artık her store için `/datastores/{name}` detail endpoint'ini çağırıyor, `connectionParameters.entry[]` parse edilerek gerçek `host/port/database/username/schema` alınıyor
- `get_datastore_detail()` yeni metod eklendi
- `get_layers()`: `GET /workspaces/{ws}/layers` yerine store→featuretypes traversal yapıyor, her layer dict'ine `store_name` ekleniyor

**3. `sync_service.py` — Placeholder veri yazmayı durdur**
- `sync_stores_for_workspace()`: eksik PostGIS parametresi olan store'lar atlanıyor (eski: `host='localhost'` yazıyordu)
- `sync_layers_for_workspace()`: store Django'da yoksa layer atlanıyor, placeholder store yaratılmıyor
- `sync_all_resources()` başına integrity cleanup eklendi — `workspace:layername` formatındaki bozuk `Layer.name` kayıtları temizleniyor

**4. Admin panel — Session filtresi kaldırıldı**
- `WorkspaceAdmin`, `StoreAdmin`, `LayerAdmin` — `get_queryset()` session'daki aktif engine'e göre filtreliyordu, ikinci engine'in verisi görünmüyordu
- Üçü de `return super().get_queryset(request)` olarak değiştirildi — artık tüm engine'lerin verisi görünüyor
- `save_model()` metodlarından session auto-assign mantığı temizlendi

**5. `ActiveEngineMiddleware` deregistered**
- `geodata_engine` (domain app) içindeydi — yanlış katman
- `settings/base.py`'dan `MIDDLEWARE` ve `CONTEXT_PROCESSORS`'dan kaldırıldı
- `middleware.py` dosyası tutuldu ama INACTIVE olarak işaretlendi
- Phase 2'de URL-based yaklaşım (`?engine=<uuid>`) planlanıyor

**6. `failure.md` dolduruldu**
- 5 mimari hata belgelendi: F-001–F-005

---

## 13 March 2026 — Dark UI + Geo Console Live Integration

### Tamamlanan İşler

**1. Dark Theme UI (UI-UX_Rules.md spec'e uygun)**
- `static/css/base.css` — tüm design token'lar (`--bg`, `--surface`, `--accent` vb.) burada, başka hiçbir yerde hardcode renk yok
- `base.css`'e eklenenler: `.nav-section-label`, `.sidebar-nav`, `.sidebar-logo`, `.logo-mark`, `.topbar-left`, `.content-header`, `.btn-add`, `.cards-grid`, `.card`, `.card-stats`, `.card-btn`, `.empty-state`

**2. `templates/base.html` — Tüm uygulama için yeni dark base**
- Sidebar: logo-mark + "TOSCA" wordmark üstte, nav itemları `<nav class="sidebar-nav">` içinde
- Topbar: sol `{% block topbar_left %}` (breadcrumb için), sağ auth area (user-pill + logout)
- Bloklar: `{% block page_css %}`, `{% block sidebar_subnav %}`, `{% block topbar_left %}`, `{% block topbar_right %}`, `{% block content %}`, `{% block page_js %}`
- Sidebar footer (avatar + username + role) kaldırıldı — auth sadece topbar'da
- `{% block page_js %}` `</div>` sonrası, `</body>` öncesinde

**3. `templates/geo_console/geodata_console.html` — extends base.html**
- 928 satırdan 70 satıra indi
- Sadece Engines sayfası implement edildi (spec gereği: sub-nav global sidebar'da değil)
- `{% block sidebar_subnav %}`: "GEO CONSOLE" section label + "Engines" nav item
- Kartlar DB'den gerçek veriyle geliyor (`engines` context)
- Engine yoksa `empty-state` gösteriyor
- `{% block page_js %}`: `static/geo_console/js/engines.js` yükleniyor

**4. `static/geo_console/js/engines.js`**
- `testConnection(engineId)` → `POST /api/geoengine/engines/{id}/validate/`
- Badge: `--` → `Connected` (badge-green) / `Failed` (badge-red) / `Error` (badge-red)
- CSRF token cookie'den alınıyor

**5. `tosca_api/apps/geo_console/views.py`**
- `GeodataEngine.objects.filter(is_active=True)` + `Coalesce(Subquery(...), Value(0))` ile workspace ve layer sayıları
- `Count` ile değil `Subquery` ile yapıldı — iki ayrı COUNT birlikte JOIN yapınca çarpım hatası oluşuyor
- Layer path: `workspace__geodata_engine` (Layer → Workspace → GeodataEngine, direkt FK yok)

**6. `docker/django/entrypoint.sh`**
- `migrate --noinput` eklendi
- `setup_default_engine` eklendi — container her başladığında idempotent çalışıyor

**7. GeoServer bağlantısı**
- Docker internal: `http://geoserver:8080/geoserver` (container adıyla, localhost değil)
- `setup_default_engine` management command settings üzerinden `.env.dev`'i okuyor
- `sync_geoserver` komutu GeoServer'daki workspace/layer'ları Django DB'ye çekiyor

### Bilinen Durum
- DB'de 1 engine (`Default GeoServer`), 4 workspace, 1 layer (`vector/apotheken`)
- Layer sayısı GeoServer sync'e bağlı — `make sync-django-geoengine` ile güncellenir
- Geo Console şu an sadece Engines view'ını implement ediyor; Workspaces/Stores/Layers ileride eklenecek

---

## 15 March 2026 — Phase 4 Layers + Store Credential Management + Bug Fixes

### Tamamlanan İşler

**1. Phase 4 — Layer Publish (4.1–4.4) tamamlandı**
- `postgis_inspector.py` oluşturuldu — SQLAlchemy/psycopg3 ile direct PostGIS bağlantısı, `geometry_columns` view'ından metadata, `ST_Extent` ile bbox
- `StoreViewSet.postgis_tables` DRF action: `GET /api/geoengine/stores/{id}/postgis_tables/`
- `LayerViewSet.publish_postgis` DRF action: `POST /api/geoengine/layers/publish_postgis/`
- Console views: `layer_list`, `layer_publish`, `layer_delete`
- `LayerPublishForm` (workspace → store → table → geometry/CRS)

**2. Layer Publish Form — Tam UX Yeniden Yazım**
- 4-bölümlü form: Source (workspace/store seçimi), Layer Definition, Geometry (read-only), CRS
- Store dropdown: workspace'e göre DOM rebuild (macOS native `<select>` CSS `display:none` option'ı yok sayıyor, JSON+JS rebuild ile çözüldü)
- Table picker: her PostGIS tablosu için card — geometry icon (●╌▭▰), type badge, srid
- Geometry display: seçilen tablonun geometry tipi/kolonu read-only kart olarak gösteriliyor
- CRS preset grid: 6 kart (WGS84/4326, Web Mercator/3857, ETRS89/4258, UTM32N/25832, UTM33N/25833, BNG/27700) + custom EPSG input
- `lp-*` CSS namespace `console.css`'e eklendi (18+ yeni selector)

**3. Layer List — Flat Table**
- Accordion yapısı kaldırıldı, flat paginated tablo (20/page)
- Workspace filter dropdown header actions'da
- Workspace tag badge, status badge (published/unpublished), CRS/geometry sütunları

**4. Store Detail + Credential Management (3.10)**
- GeoServer sync'den gelen store'ların Django'da password bilgisi olmadığı tespit edildi (GeoServer REST API credentials expose etmiyor)
- `StoreSerializer.has_password` SerializerMethodField eklendi
- `postgis_tables` endpoint: password yoksa açıklayıcı 400 mesajı, stack trace yok
- Store Detail sayfası: `/console/stores/<uuid>/` — identity card + credential edit formu + sarı uyarı banner'ı
- Store list kartları: Detail | Clone | Delete üç buton sırası
- `GeoConsoleAPIClient.update_store()` PATCH metodu eklendi

**5. Kritik Bug Fix'ler**
- `delete_datastore` → `delete_featurestore`: `geoserver-rest` kütüphanesinde bu method yoktu, store silinemiyordu (F-007)
- `{% block page_scripts %}` → `{% block page_js %}`: layer publish sayfasında tüm JS sessizce drop ediliyordu — Django bilinmeyen block'ları hata vermeden yok sayıyor (F-008)
- Delete hata mesajı: `detail` alanına bookkeeping metni yerine gerçek GeoServer hatası yazılıyor

### Bilinen Durum
- Phase 4 POC tamamlandı — PostGIS tablosunu GeoServer layer olarak publish etmek uçtan uca çalışıyor
- GeoServer'dan sync edilen store'lar için Detail sayfasından credential girilmesi gerekiyor
- Phase 5 (Styles) henüz başlamadı
