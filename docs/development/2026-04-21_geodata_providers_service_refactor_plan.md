# Geodata Providers Service Refactor Plan

Tarih: 2026-04-21

## Amaç

`geodata_providers` app içindeki iş kurallarını admin, admin view, admin action ve
internal API dosyalarına dağılmış halden çıkarıp servis katmanında toplamak.

Bu refactor'un amacı mevcut çalışan CRUD akışlarını bozmak değil, onları daha
güvenli, tekrar kullanılabilir ve test edilebilir hale getirmektir.

## Kritik İlke

Şu anki admin yapısında temel CRUD akışları çalışıyor gibi görünüyor.

Bu yüzden refactor sırasında ana ilke:

- mevcut çalışan admin CRUD davranışını bozmamak
- mevcut akışları "working baseline" olarak kabul etmek
- aynı davranışı servis katmanına taşırken edge-case ve verify standardını
  güçlendirmek

Yani bu çalışma bir yeniden yazım değil, kontrollü extraction/refactor çalışmasıdır.

## Kapsam

Bu belge yalnızca `geodata_providers` domain içindeki command/orchestration
değişikliklerini kapsar.

Kapsam dışı:

- `catalog_api`
- web GIS read/query endpoint tasarımı
- consumer-facing response contract

## Product Context

Bu refactor sadece admin temizliği için yapılmıyor.

Asıl ürün hedefi:

- farklı geodata engine instance'larını tek platformda toplamak
- bunları web arayüzünde sol panelde, `Data Bazaar` benzeri bir yapı altında göstermek
- kullanıcıya farklı GeoServer / Martin / pg_tileserv benzeri kaynakları tek yerden browse etme imkanı vermek

Bu yüzden `GeodataEngine` sadece bir admin kaydı değildir.
Platform içindeki gerçek üst seviye provider instance'ıdır.

Örnek:

- bir GeoServer instance
- başka bir GeoServer instance
- bir Martin instance
- ileride başka tile/data engine'leri

Her biri kendi altında şu yapıyı taşıyabilir:

- workspace / namespace / collection benzeri container'lar
- layer / source / dataset kayıtları

Bu nedenle refactor yalnızca `Workspace`, `Store`, `Layer` değil, en üstte
`GeodataEngine` instance lifecycle'ını da açık biçimde ele almalıdır.

## GeodataEngine Neden Kritik

Şu sebeplerle `GeodataEngine` service refactor kapsamına dahil edilmelidir:

- platformdaki dış veri sağlayıcının giriş noktası odur
- sync ve discovery onun üstünden başlar
- farklı engine tiplerini aynı soyutlama altında yönetebilmek için ilk boundary odur
- yarın `catalog_api` ve Data Bazaar görünümü provider ağacını engine seviyesinden kuracaktır

Kısacası:

- `Layer/Store/Workspace` alt seviye lifecycle
- `GeodataEngine` üst seviye provider instance lifecycle

## GeodataEngine İçin Refactor Hedefi

`GeodataEngine` tarafında da command/orchestration service yaklaşımı uygulanmalıdır.

### Dosya

- `geodata_providers/services/commands/geodata_engine_service.py`

### Yazılacak servisler

1. `GeodataEngineService.create_engine(...)` ✅
- girdi:
  - `name`
  - `engine_type`
  - `base_url`
  - `credentials`
  - `description`
  - `user`
- akış:
  - connection validation
  - supported engine type policy kontrolü
  - `transaction.atomic()` ile local persist
  - initial sync/discovery tetikle

2. `GeodataEngineService.update_engine(...)` ✅
- girdi:
  - `engine`
  - değişen connection/config alanları
- akış:
  - connection değiştiyse re-validate
  - başarılıysa local update
  - sync/discovery tekrar çalıştır

3. `GeodataEngineService.delete_engine_safe(...)` ✅
- girdi:
  - `engine`
- akış:
  - bağlı `workspace/store/layer` sayımlarını kontrol et
  - silme policy'sine göre blokla veya izin ver
  - local delete

4. `GeodataEngineService.sync_engine(...)` ✅
- girdi:
  - `engine`
  - `user`
- akış:
  - engine-specific sync service çağrısı
  - result normalize et

5. `GeodataEngineService.validate_engine_connection(...)` ✅
- girdi:
  - credentials/config
- akış:
  - engine factory üzerinden doğru client oluştur
  - validation sonucu normalize et

## GeodataEngine Refactor Sonrası Kullanım

Bu servisleri kullanacak yerler:

- admin engine create/update/delete
- internal DRF engine create/update/sync/test connection
- ileride Data Bazaar discovery bootstrap

Bu sayede engine seviyesi davranış da tek yerde toplanmış olur.

## Neden Gerekli

Şu an aynı orchestration adımları birden fazla yerde tekrar ediyor:

- `admin.py`
- `admin_views/*.py`
- `admin_actions/*.py`
- `api/views.py`

Tekrarlanan yapı:

- pre-check
- remote mutate
- verify-after-mutate
- local persist/delete
- user message / response shaping

Bu tekrar ileride drift ve davranış farkı üretir.

## Hedef Yapı

Servis katmanı iki ana klasörde organize edilecek:

- `geodata_providers/services/commands/`
- `geodata_providers/services/queries/`

Bu doküman ilk etapta ağırlıklı olarak `commands/` tarafını kapsar.
Ama `GeodataEngine` üst seviye provider instance olduğu için burada ayrıca
özellikle ele alınır.

## Refactor Scope Sırası

Güncel sıralama şu olmalıdır:

1. `GeodataEngineService`
2. `LayerService`
3. `StoreService`
4. `WorkspaceService`

Sebep:

- sistemin gerçek root nesnesi `GeodataEngine`
- farklı provider instance'larını platforma dahil etme mantığı burada başlar
- alt resource'ların tamamı engine bağlamına bağlıdır

Yine de kod karmaşıklığı açısından ilk extraction teknik olarak `LayerService`
ile başlayabilir. Ama doküman seviyesinde root abstraction olarak `GeodataEngine`
öne yazılmalıdır.

## Phase 0 — GeodataEngine Command Service

### Dosya

- `geodata_providers/services/commands/geodata_engine_service.py` ✅

### İlk kullanım yüzeyleri

- `GeodataEngineAdmin.save_model` ✅
- `GeodataEngineAdmin.delete_model` ✅
- internal DRF:
  - engine create ✅
  - engine update ✅
  - engine sync ✅
  - engine validate/test connection ✅

## Phase 1 — Layer Command Service

İlk adım `Layer` tarafıdır. Çünkü publish/update/delete/unpublish orchestration
en yoğun burada bulunuyor.

### Dosya

- `geodata_providers/services/commands/layer_service.py` ✅

### Yazılacak servisler

1. `LayerService.publish_postgis(...)` ✅
- girdi:
  - `workspace`
  - `store`
  - `table_name`
  - `layer_name`
  - `title`
  - `description`
  - `geometry_column`
  - `geometry_type`
  - `srid`
  - `user`
- akış:
  - target layer pre-check
  - remote publish
  - remote verify
  - `transaction.atomic()` ile local persist
  - normalized result dön

2. `LayerService.update_published_metadata(...)` ✅
- girdi:
  - `layer`
  - `title`
  - `description`
- akış:
  - remote update
  - remote metadata verify
  - başarılıysa local update

3. `LayerService.delete_layer_safe(...)` ✅
- girdi:
  - `layer`
- akış:
  - published ise remote delete
  - remote verify
  - already deleted ise idempotent success
  - local delete

4. `LayerService.unpublish_layer(...)` ✅
- girdi:
  - `layer`
- akış:
  - remote delete/unpublish
  - verify
  - local state update

## Phase 2 — Layer Surface Refactor

Bu servis yazıldıktan sonra mevcut surface'ler bu servisi kullanacak şekilde
ince refactor edilecek.

### Admin

- `LayerAdmin.save_model` ✅
- `LayerAdmin.delete_model` ✅
- `LayerAdmin.delete_queryset` ✅

### Admin view / action

- `admin_views/layer.py` ✅
- `admin_actions/layer.py` ✅

### Internal API

- `LayerViewSet.update` ✅
- `LayerViewSet.destroy` ✅
- `LayerViewSet.unpublish` ✅
- `LayerViewSet.publish_postgis` ✅

## Phase 3 — Store Command Service

### Dosya

- `geodata_providers/services/commands/store_service.py` ✅

### Yazılacak servisler

1. `StoreService.create_postgis_store(...)` ✅
- girdi:
  - `workspace`
  - `name`
  - `store_type`
  - PostGIS/file/geotiff config alanları
  - `description`
  - `user`
- akış:
  - target store pre-check
  - engine-specific remote create
  - remote verify
  - `transaction.atomic()` ile local persist
  - normalized result dön

2. `StoreService.clone_store(...)` ✅
- girdi:
  - `source_store`
  - `target_workspace`
  - `name`
  - override config alanları
  - `user`
- akış:
  - Django uniqueness pre-check
  - gerekiyorsa remote create
  - verify
  - local persist
  - opsiyonel workspace sync sonucu normalize et

3. `StoreService.delete_store_safe(...)` ✅
- girdi:
  - `store`
- akış:
  - bağlı `layer` sayımını kontrol et
  - published dependency varsa blokla
  - remote delete
  - already deleted ise idempotent success
  - verify-after-delete
  - local delete

### Mevcut durum

- `StoreService` eklendi ✅
- store create/delete orchestration service'e taşındı ✅
- clone akışı service'e taşındı ✅
- admin form create preflight (`StoreAdminForm._post_clean`) henüz service'e taşınmadı

### Refactor edilecek mevcut surface'ler

#### Admin

- `StoreAdminForm._post_clean`
- `StoreAdmin.save_model` ✅
- `StoreAdmin.delete_model` ✅
- `StoreAdmin.delete_queryset` ✅

#### Admin view / action

- `admin_views/store.py::store_clone_view` ✅
- `admin_actions/store.py::clone_store` ✅

#### Internal API

- `StoreViewSet.create` ✅
- `StoreViewSet.destroy` ✅

#### Test

- `tests/test_store_service.py` ✅
- mevcut admin testleri service call seviyesine taşınacak:
  - `test_admin_forms.py` ✅

## Phase 4 — Workspace Command Service

### Dosya

- `geodata_providers/services/commands/workspace_service.py` ✅

### Yazılacak servisler

1. `WorkspaceService.create_workspace(...)` ✅
- girdi:
  - `engine`
  - `name`
  - `description`
  - `user`
- akış:
  - reserved-name policy kontrolü
  - Django uniqueness pre-check
  - remote create
  - remote verify
  - `transaction.atomic()` ile local persist
  - normalized result dön

2. `WorkspaceService.delete_workspace_safe(...)` ✅
- girdi:
  - `workspace`
- akış:
  - reserved workspace policy kontrolü (`vector`)
  - bağlı `store/layer` sayımlarını kontrol et
  - remote delete
  - already deleted ise idempotent success
  - verify-after-delete
  - local delete

### Mevcut durum

- `WorkspaceService` eklendi ✅
- workspace create akışı service'e taşındı ✅
- workspace delete akışı service'e taşındı ✅
- command boundary netleştirildi ✅

### Refactor edilecek mevcut surface'ler

#### Admin

- `WorkspaceAdminForm._post_clean` ✅
- `WorkspaceAdmin.save_model` ✅
- `WorkspaceAdmin.delete_model` ✅
- `WorkspaceAdmin.delete_queryset` ✅

#### Admin view / action

- `admin_views/workspace.py::workspace_sync_view` ✅
- `admin_actions/workspace.py::sync_workspaces` ✅

#### Internal API

- `WorkspaceViewSet.create` ✅
- `WorkspaceViewSet.destroy` ✅

#### Test

- `tests/test_workspace_service.py` ✅
- mevcut admin/API testleri service call seviyesine taşındı ✅

## Phase 5 — Store Surface Refactor

`StoreService` yazıldıktan sonra mevcut store surface'leri bu servisi kullanacak
şekilde inceltilecek.

### Admin

- `StoreAdminForm._post_clean`
- `StoreAdmin.delete_model` ✅
- `StoreAdmin.delete_queryset` ✅

### Admin view / action

- `admin_views/store.py::store_clone_view` ✅
- `admin_actions/store.py::clone_store` ✅

### Internal API

- `StoreViewSet.create` ✅
- `StoreViewSet.destroy` ✅

## Phase 6 — Workspace Surface Refactor

`WorkspaceService` yazıldıktan sonra workspace create/delete surface'leri
command service'e taşınacak; sync action/view ise query/sync boundary'sinde
kalacak.

### Admin

- `WorkspaceAdminForm._post_clean` ✅
- `WorkspaceAdmin.save_model` ✅
- `WorkspaceAdmin.delete_model` ✅
- `WorkspaceAdmin.delete_queryset` ✅

### Internal API

- `WorkspaceViewSet.create` ✅
- `WorkspaceViewSet.destroy` ✅

## Sonra Gözden Geçirilecekler

- internal DRF management API'nin daraltılması gerekip gerekmediği
- sync service ile command service boundary'nin netleştirilmesi
- engine type bazlı adapter/policy ayrımının service katmanına nasıl oturtulacağı

## Result Contract

Command servisler mümkün olduğunca ortak yapı dönmeli:

- `success`
- `message`
- `error`
- `verified`
- `already_exists`
- `already_deleted`
- `resource`



## Refactor Kuralları

- mevcut çalışan admin CRUD flow'u kırma
- önce servis ekle, sonra caller'ları yavaşça taşı
- behavior parity koru
- remote-first ve verify-after-mutate standardını geriye götürme
- naming semantiğini bozma:
  - `Layer.name` = GeoServer resource / featuretype name
  - `Layer.table_name` = native PostGIS table
  - `Layer.title` = GeoServer title

## Uygulama Sırası

1. `GeodataEngineService`
2. engine admin/api refactor
3. `LayerService`
4. layer admin/api refactor
5. `StoreService`
6. store admin/api refactor
7. `WorkspaceService`
8. workspace admin/api refactor

## Güncel Durum Özeti

- `GeodataEngineService` ✅
- engine admin/api refactor ✅
- `LayerService` ✅
- layer admin/api refactor ✅
- `StoreService` ✅
- store admin/api refactor ✅
- `WorkspaceService` ✅
- workspace admin/api refactor ✅

Bu sırayla gitmek en düşük riskli yol olarak kabul edilir.
