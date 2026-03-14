# TOSCA — Milestone Notes

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
