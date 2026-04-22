# Geodata Platform Architecture

Tarih: 2026-04-21

## Amaç

Bu doküman, geospatial yönetim alanı ile web GIS tüketim alanını aynı sistem içinde
nasıl ayıracağımızı tanımlar.

Bu sistemin iki farklı ihtiyacı var:

- operasyonel yönetim:
  - engine ekleme
  - workspace/store/layer CRUD
  - publish / unpublish
  - sync
  - connection validation
- tüketim / katalog erişimi:
  - provider list
  - workspace detail
  - layer detail
  - style list/detail
  - web GIS için temiz ve normalize edilmiş payload

Bu iki ihtiyaç aynı domain modellerini kullanır, ama aynı delivery surface'i
paylaşmak zorunda değildir.

## Temel Karar

Platform iki ana katmana ayrılacak:

1. `geodata_providers`
2. `catalog_api`

### 1. `geodata_providers`

Bu app domain owner olacaktır.

Sorumlulukları:

- modeller
  - `GeodataEngine`
  - `Workspace`
  - `Store`
  - `Layer`
- GeoServer client entegrasyonu
- sync/reconciliation mantığı
- admin panel
- command/orchestration service'leri
- gerekirse internal management API

Bu app sistemin source-of-truth domain katmanıdır.

### 2. `catalog_api`

Bu app web GIS uygulamasına veri sunan consumer-facing API olacaktır.

Sorumlulukları:

- provider/workspace/layer/style read endpoint'leri
- frontend için sadeleştirilmiş payload
- iç GeoServer URL'lerini ve gereksiz field'ları filtreleme
- query/read service'lerini kullanarak katalog response üretme

Bu app domain sahibi değildir. Domain logic yazmaz.

## App'ler Nasıl Haberleşir

App-to-app iletişim HTTP/DRF ile yapılmayacak.

Yanlış yaklaşım:

- `catalog_api` gidip internal DRF endpoint'lerini çağırır
- aynı Django projesi içinde HTTP üstünden iç istek atılır

Doğru yaklaşım:

- ortak iş mantığı Python service katmanında tutulur
- admin, internal API ve `catalog_api` aynı service katmanını import eder

Yani iletişim şekli:

- `import`
- function / class call

HTTP sadece dış boundary'dir.

## Katmanlama

Önerilen katmanlar:

1. Domain layer
- Django models
- domain state

2. Integration layer
- GeoServer client
- PostGIS inspection helpers

3. Service layer
- orchestration / command services
- query / catalog services

4. Delivery layer
- Django Admin
- internal DRF API
- `catalog_api`

## Service Layer Ayrımı

Service layer ikiye ayrılmalıdır:

### A. Command / Orchestration Services

Mutasyon işlemleri burada yaşar.

Örnekler:

- `LayerService.publish_postgis(...)`
- `LayerService.update_published_metadata(...)`
- `LayerService.delete_layer_safe(...)`
- `StoreService.create_postgis_store(...)`
- `StoreService.delete_store_safe(...)`
- `WorkspaceService.create_workspace(...)`
- `WorkspaceService.delete_workspace_safe(...)`

Bu service'ler şu standardı uygular:

- pre-check
- remote mutate
- verify-after-mutate
- `transaction.atomic()` ile local persist
- normalized result contract

### B. Query / Catalog Services

Read odaklı veri toplama ve normalize etme burada yaşar.

Örnekler:

- `CatalogProviderQueryService.list_providers()`
- `CatalogWorkspaceQueryService.get_workspace_detail(...)`
- `CatalogLayerQueryService.get_layer_detail(...)`
- `CatalogStyleQueryService.list_styles(...)`

Bu service'ler:

- modellerden okur
- gerekirse GeoServer detail endpoint'leriyle zenginleştirir
- frontend'e uygun response shape döner

## Internal DRF API Gerekli mi

Zorunlu değil.

Eğer yönetim yalnızca admin panel üzerinden yapılacaksa, admin-oriented DRF API
zorunlu değildir.

Yine de internal API şu sebeplerle faydalı olabilir:

- gelecekte custom management UI yazılması
- otomasyon / script / workflow ihtiyacı
- admin dışı operational client'lar

Ama mimari olarak ana şart değildir.

Bu yüzden öneri:

- internal DRF API varsa thin adapter olsun
- gerçek iş mantığı service katmanında yaşasın
- internal API isterse daraltılabilsin

## Catalog API Neden Ayrı

`catalog_api` ayrı tutulursa şu faydalar gelir:

- consumer/read contract yönetim API'den ayrılır
- permission modeli ayrı tanımlanabilir
- payload daha sade tutulur
- web GIS ihtiyacı ile admin ihtiyacı karışmaz

## Naming

Bu dokümanda önerilen isim:

- app adı: `catalog_api`

Sebep:

- `provider_api` fazla teknik ve domain merkezli
- `catalog_api` web GIS tarafındaki kullanım amacını daha iyi anlatır

## Domain Naming Semantics

Layer alanları için standart:

- `Layer.name`
  - GeoServer resource / featuretype name
- `Layer.table_name`
  - native PostGIS table or view
- `Layer.title`
  - GeoServer title / display name

Bu semantik hem command service hem sync hem catalog read tarafında tutarlı
olmalıdır.

## Result Contract

Command service'ler benzer yapı dönmelidir:

- `success`
- `message`
- `error`
- `verified`
- `already_exists`
- `already_deleted`
- `resource`

Bu contract admin, internal API ve ileride başka surface'lerde tekrar
kullanılabilir.

## Uygulama Sırası

1. `geodata_providers` içinde command service refactor
2. internal admin/api surface'leri bu service'lere bağla
3. query/catalog service'leri tanımla
4. `catalog_api` app'ini aç
5. `catalog_api`yı sadece read/query surface olarak bağla

## Sonuç

Bu platformda `geodata_providers` domain sahibi, `catalog_api` ise tüketiciye
bakan bir read surface olmalıdır.

Tekrar eden iş mantığını önlemenin ana yolu:

- app'leri HTTP ile birbirine konuşturmak değil
- service layer'ı tek yerde toplamak

Yarın devam edilecek ana referans doküman budur.
