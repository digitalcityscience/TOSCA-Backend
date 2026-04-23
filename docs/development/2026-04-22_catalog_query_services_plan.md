# Catalog Query Services Plan

Tarih: 2026-04-22

## Amaç

`catalog_api` app'inden önce, onun dayanacağı read/query servis katmanını
`geodata_providers` domain içinde tanımlamak.

Bu katman:

- sadece okuma yapar
- mutate etmez
- admin action / command flow içermez
- frontend veya API için uygun sade veri shape'i üretir

## Neden Önce Bu Katman

`catalog_api` doğrudan model query'leri veya admin logic üstüne kurulursa:

- response shape dağılır
- aynı okuma mantığı farklı yerlerde tekrar eder
- ileride frontend payload değişince birden fazla katman etkilenir

Bu yüzden önce ortak query service boundary kurulmalıdır.

## Kapsam

Bu fazda yapılacaklar:

- `geodata_providers/services/queries/` klasörünü açmak
- provider/workspace/layer read servislerini yazmak
- gerekirse style için placeholder boundary tanımlamak
- normalized read result sözleşmesini netleştirmek
- temel testleri eklemek

Bu fazda yapılmayacaklar:

- `catalog_api` app'ini açmak
- DRF endpoint yazmak
- frontend contract'ı son haline getirmek
- remote mutate/publish işlemleri

## Hedef Dosya Yapısı

- ✅ `tosca_api/apps/geodata_providers/services/queries/__init__.py`
- ✅ `tosca_api/apps/geodata_providers/services/queries/provider_query_service.py`
- ✅ `tosca_api/apps/geodata_providers/services/queries/workspace_query_service.py`
- ✅ `tosca_api/apps/geodata_providers/services/queries/layer_query_service.py`
- ``

Test tarafı:

- ✅ `tosca_api/apps/geodata_providers/tests/test_provider_query_service.py`
- ✅ `tosca_api/apps/geodata_providers/tests/test_workspace_query_service.py`
- ✅ `tosca_api/apps/geodata_providers/tests/test_layer_query_service.py`
- ✅ `tosca_api/apps/geodata_providers/tests/test_style_query_service.py`

## Tasarım İlkeleri

1. Query service yalnızca read odaklı olmalı.
2. Service output'u model instance değil, normalize dict/list olmalı.
3. `catalog_api` bu service'leri import etmeli; `geodata_providers.api.views` çağırmamalı.
4. Query logic mümkün olduğunca DB-first olmalı.
5. Remote enrichment gerekiyorsa opsiyonel ve kontrollü olmalı.
6. İlk versiyon için sade payload tercih edilmeli.

## İlk Read Contract Taslağı

### Provider List

Her provider için minimum alanlar:

- `id`
- `name`
- `engine_type`
- `description`
- `is_active`
- `is_default`
- `workspace_count`
- `layer_count`

### Workspace Detail

Minimum alanlar:

- `id`
- `name`
- `description`
- `provider`
- `stores`
- `layers`

`provider` alt objesi:

- `id`
- `name`
- `engine_type`

### Layer Detail

Minimum alanlar:

- `id`
- `name`
- `title`
- `description`
- `table_name`
- `geometry_column`
- `geometry_type`
- `srid`
- `publishing_state`
- `is_public`
- `published_url`
- `provider`
- `workspace`
- `store`

### Style

Style modeli/domain net değilse:

- dosyayı şimdilik interface/placeholder olarak aç
- gerçek implementation'ı bir sonraki faza bırak

## Sıralı Uygulama Planı

### Phase 1 — Query Package Skeleton ✅

İlk adım:

- ✅ `services/queries/` klasörünü aç
- ✅ `__init__.py` ekle
- ✅ servis dosyalarını oluştur
- ✅ isimlendirmeyi netleştir

Bu adımın amacı yalnızca boundary kurmaktır.

### Phase 2 — Provider Query Service ✅

Yazılacak ilk servis:

- ✅ `ProviderQueryService.list_providers()`

Yapacakları:

- ✅ `GeodataEngine` queryset kur
- ✅ aktif/inaktif filtre mantığını netleştir
- ✅ workspace/layer count annotate et
- ✅ sonuçları normalize et

İlk çünkü en üst domain root burada.

### Phase 3 — Workspace Query Service ✅

İlk hedef methodlar:

- ✅ `WorkspaceQueryService.get_workspace_detail(provider_id, workspace_id)`
- ✅ `WorkspaceQueryService.list_provider_workspaces(provider_id)`

Yapacakları:

- ✅ workspace'i provider bağlamında bul
- ✅ bağlı store/layer özetini çıkar
- ✅ response shape üret

### Phase 4 — Layer Query Service ✅

İlk hedef methodlar:

- ✅ `LayerQueryService.get_layer_detail(layer_id)`
- ✅ `LayerQueryService.list_workspace_layers(workspace_id)`

Yapacakları:

- ✅ layer metadata'sını oku
- ✅ provider/workspace/store ilişkisini response içine koy
- ✅ frontend için gereksiz model alanlarını dışarıda bırak

### Phase 5 — Style Query Service ✅

Bu kısım implementation öncesi netleştirilecek:

- ✅ style gerçekten hangi modelden gelecek?
- ✅ GeoServer style read gerekecek mi?
- ✅ yoksa şimdilik placeholder mı kalacak?

Eğer domain hazır değilse bu faz minimum seviyede bırakılmalı. ✅

### Phase 6 — Testler ✅

Her service için şu tip testler yazılmalı:

- ✅ basic success case
- ✅ not found / invalid relation case
- ✅ visibility/filter behavior
- ✅ normalized response shape assertion

## Açık Kararlar

Kodlamadan önce netleştirilmesi iyi olacak başlıklar:

1. Provider list sadece `is_active=True` mi dönecek?  >> defaul olarak evet.
2. Workspace detail içinde tüm store/layer'lar mı olacak, yoksa sadece public/published olanlar mı? >default sadece public ve published ler. eger ilerideki token mantigi kuruldugunda kullanicinin grubuna ait layer larida donmemiz lazim
3. Layer detail içinde attributes/bbox gibi alanlar ilk fazda şart mı? 
> /Users/hsadmin/Desktop/coding/dcs-django-api/docs/development/catatlog_api.md bunun icinde frontend in veriyi nasil bekledigi yaziyor aslinda. islemi kontrol edelim buna gore uygun olarak mi donuyor her sey tekrar kontrol edelim. bu dokuman ana takip etmemiz gereken yapi aslinda. ama bu tabiki catalog api icinde de handle edileblir. burasi provider service burasi daha genel hizmet olmali diye dusunuyorum. d

## Önerilen İlk Scope

İlk implementasyonda yalnızca şunları bitirelim:

1. `ProviderQueryService.list_providers()`
2. `WorkspaceQueryService.get_workspace_detail(...)`
3. `LayerQueryService.get_layer_detail(...)`
4. Bunların testleri

Style tarafını ikinci commit'e bırakmak daha güvenli olur.

## Sonraki Faz

Bu dosyadaki işler bitince:

1. `catalog_api` app'i açılır
2. serializer/view katmanı bu service'lerin üstüne kurulur
3. endpoint contract orada finalize edilir
