# Catalog API V1 And Generalization Plan

Tarih: 2026-04-22

## Amaç

Bu dokümanın amacı `catalog_api` için iki aşamalı bir yol tanımlamaktır:

1. İlk aşamada frontend koduna dokunmadan, mevcut `geoserver.ts` ve `map.ts`
   beklentilerini Django üzerinden karşılamak.
2. İkinci aşamada bu geçiş katmanını daha genel, provider-aware bir
   `catalog_api` kontratına evirmek.

Bu doküman özellikle şu iki dosyayı baz alır:

- `docs/development/geoserver.ts`
- `docs/development/map.ts`

Bu doküman eski `catatlog_api.md` notlarını referans almak zorunda değildir.
Ana kaynak mevcut frontend davranışıdır.


## Karar Özeti

- İlk aşamada frontend kodu değişmeyecek.
- İlk aşamada response shape mümkün olduğunca birebir korunacak.
- İlk aşamada GeoServer REST yerine Django v1 endpointleri verilecek.
- İlk aşamada `map.ts` içindeki WMS/WMTS URL üretim davranışı korunacak.
- İlk aşamada frontend sadece env bazlı endpoint değişikliği ile çalışmalı.
- İlk aşamada görünür katalog sadece `is_active=True` provider ve
  `is_public=True && publishing_state='PUBLISHED'` layerları içermeli.
- Ilk asamada yzacagimiz api adi v1 olarak yazilacak.
- İkinci aşamada daha genel `catalog_api` kontratına geçilecek. sonra bu v2 olacak
- `catalog_api` mümkün olan maksimum seviyede
  `geodata_providers/services/queries/` katmanını kullanmalı.
- `catalog_api` içinde doğrudan model query yazımı istisna olmalı;
  önce mevcut query service kontrol edilmeli, gerekirse yeni read/query service
  `geodata_providers` altında açılmalı.

## Frontend Bugün Ne Bekliyor

### 1. `geoserver.ts` Beklentisi

Frontend bugün GeoServer REST'ten şunları alıyor:

- workspace list
- layer list
- layer info
- layer detail
- style detail
- gerekirse geojson source

Beklenen TypeScript shape'leri:

- `WorkspaceListResponse`
- `GeoserverLayerListResponse`
- `GeoserverLayerInfoResponse`
- `GeoServerVectorTypeLayerDetail`
- `GeoserverRasterTypeLayerDetail`
- style için MBStyle JSON

### 2. `map.ts` Beklentisi

Frontend bugün source URL'yi kendisi kuruyor.

Elindeki minimum bilgiler:

- `workspaceName`
- vector ise `featureType.name`
- raster ise `coverage.name`
- `sourceProtocol` = `wms | wmts`
- `sourceDataType` = `vector | raster`
- `VITE_GEOSERVER_BASE_URL`

Sonuç:

- İlk aşamada Django'nun full tile URL döndürmesi şart değildir.
- İlk aşamada Django'nun REST metadata shape'ini doğru vermesi şarttır.
- İlk aşamada `VITE_GEOSERVER_BASE_URL` frontend'de kalabilir.

## Aşama 1: V1 Mode

### Hedef

Frontend'in bugün GeoServer REST'ten aldığı veriyi, Django üzerinden aynı shape
ile almasını sağlamak.

### Temel Kural

Frontend store kodu değişmeyecek.

Sadece environment veya config tarafında `VITE_GEOSERVER_REST_URL` artık
GeoServer'a değil Django `catalog_api` v1 endpointine bakacak.

Örnek:

- eski: `https://maps.example.com/geoserver/rest`
- yeni: `https://api.example.com/api/catalog/v1`

`VITE_GEOSERVER_BASE_URL` ise ilk aşamada aynen kalabilir:

- `https://maps.example.com/geoserver`

Bu sayede:

- metadata read -> Django
- tile/WMS/WMTS request -> GeoServer public map endpoint

### API Dokümantasyonu

Catalog v1 ve sonraki generic catalog endpointlerini tarayıcıdan adım adım
incelemek için Swagger UI sağlanmalıdır.

Endpointler:

- `GET /api/schema/`
- `GET /api/docs/`

Local geliştirme için:

- `http://localhost:8000/api/schema/`
- `http://localhost:8000/api/docs/`

Not:

- `Swagger UI` sadece dışarı açılacak catalog endpointlerini göstermek için
  kullanılmalıdır.
- `geodata_providers` / `geoengine` endpointleri internal kalmalı ve Swagger
  UI içinde gösterilmemelidir.

### Aşama 1 Kapsamı

İlk aşamada şu endpointler sağlanmalı:

1. workspace list 🟢➡️
2. layer list 🟢➡️
3. layer info 🟢➡️
4. layer detail 🟢➡️
5. style detail

`getGeoJSONLayerSource(...)` ilk aşamada opsiyoneldir. Esas hedef mevcut katalog
gezinmesini ve layer seçimini ayakta tutmaktır.

## Aşama 1 Endpoint Tasarımı

### 1. Workspace List

Frontend çağrısı:

- `GET {VITE_GEOSERVER_REST_URL}/workspaces`

Django endpoint:

- `GET /api/catalog/v1/workspaces`

Output:

```json
{
  "workspaces": {
    "workspace": [
      {
        "name": "mobility",
        "href": "https://api.example.com/api/catalog/v1/workspaces/mobility"
      }
    ]
  }
}
```

Notlar:

- `href` alanı GeoServer URL olmak zorunda değil.
- `href` frontend tarafından bugün aktif kullanılmıyorsa placeholder değil,
  gerçek Django URL dönmek daha doğru olur.
- Liste yalnızca görünür katalogda yer alan workspace'leri içermeli.

### 2. Layer List 🟢➡️

Frontend çağrıları:

- `GET {VITE_GEOSERVER_REST_URL}/layers`
- `GET {VITE_GEOSERVER_REST_URL}/workspaces/{workspaceName}/layers`

Django endpointleri:

- `GET /api/catalog/v1/layers`
- `GET /api/catalog/v1/workspaces/{workspace_name}/layers`

Output:

```json
{
  "layers": {
    "layer": [
      {
        "name": "tram_lines",
        "href": "https://api.example.com/api/catalog/v1/workspaces/mobility/layers/tram_lines"
      }
    ]
  }
}
```

Notlar:

- Bu liste sadece aktif provider + public + published layerları içermeli.
- Global `layers` list endpoint'i çok providerlı sistemde belirsizlik yaratır.
  Bu yüzden her item'in `href` alanı tam Django URL olmalı.
- Workspace scoped endpoint'te görünür olmayan veya bulunmayan workspace için
  boş liste yerine `404` dönmek daha doğru v1 davranışıdır.

### 3. Layer Info 🟢➡️

Frontend çağrısı:

- `GET {VITE_GEOSERVER_REST_URL}/workspaces/{workspace}/layers/{layer}`

Django endpoint:

- `GET /api/catalog/v1/workspaces/{workspace_name}/layers/{layer_name}`

Output:

```json
{
  "layer": {
    "name": "tram_lines",
    "type": "VECTOR",
    "defaultStyle": {
      "name": "default",
      "href": "https://api.example.com/api/catalog/v1/styles/default"
    },
    "resource": {
      "@class": "featureType",
      "name": "tram_lines",
      "href": "https://api.example.com/api/catalog/v1/workspaces/mobility/resources/tram_lines"
    },
    "attribution": {
      "logoWidth": 0,
      "logoHeight": 0
    },
    "dateCreated": "2026-04-22T12:00:00Z",
    "dateModified": "2026-04-22T12:00:00Z"
  }
}
```

Notlar:

- `type` frontendin bugün kullandığı şekle uygun olmalı.
- `resource.href` mutlaka Django endpointi olmalı.
- `defaultStyle.href` de mutlaka Django endpointi olmalı.
- Eğer GeoServer'da style yoksa fallback davranışı backend'de yönetilmeli.

### 4. Layer Detail 🟢➡️

Frontend çağrısı:

- `GET {resource.href}`

Django endpoint:

- `GET /api/catalog/v1/workspaces/{workspace_name}/resources/{layer_name}`

Output vector için:

```json
{
  "featureType": {
    "name": "tram_lines",
    "nativeName": "tram_lines",
    "namespace": {
      "name": "mobility",
      "href": ""
    },
    "title": "Tram Lines",
    "abstract": "Transit layer",
    "keywords": {
      "string": []
    },
    "nativeCRS": "EPSG:4326",
    "srs": "EPSG:4326",
    "nativeBoundingBox": {
      "minx": 0,
      "maxx": 0,
      "miny": 0,
      "maxy": 0,
      "crs": "EPSG:4326"
    },
    "latLonBoundingBox": {
      "minx": 0,
      "maxx": 0,
      "miny": 0,
      "maxy": 0,
      "crs": "EPSG:4326"
    },
    "projectionPolicy": "FORCE_DECLARED",
    "enabled": true,
    "store": {
      "@class": "dataStore",
      "name": "mobility_store",
      "href": ""
    },
    "serviceConfiguration": false,
    "simpleConversionEnabled": false,
    "internationalTitle": "",
    "internationalAbstract": "",
    "maxFeatures": 0,
    "numDecimals": 0,
    "padWithZeros": false,
    "forcedDecimal": false,
    "overridingServiceSRS": false,
    "skipNumberMatched": false,
    "circularArcPresent": false,
    "attributes": {
      "attribute": []
    }
  }
}
```

Output raster için:

- shape `GeoserverRasterTypeLayerDetail` ile uyumlu olmalı

Notlar:

- İlk hedef frontend v1 contract olduğu için key isimleri korunmalı.
- İç `href` alanları boş string olabilir ya da Django URL olabilir.
- Frontend `href` alanlarını aktif kullanmıyorsa boş string dönmek kabul
  edilebilir.
- Eğer bbox ve attributes şu an güvenilir biçimde üretilemiyorsa backend
  fallback stratejisi tanımlamalı.

### 5. Style Detail

Frontend çağrısı:

- `GET {defaultStyle.href}`

Django endpoint:

- `GET /api/catalog/v1/styles/{style_name}`

Output:

- GeoServer'dan alınan MBStyle JSON

Notlar:

- Bu endpoint mümkünse pass-through değil normalize read olmalı.
- İlk aşamada sadece mevcut frontendin kullandığı style formatı desteklenebilir.

## Aşama 1 İç Servis Yapısı

`catalog_api` app'i içinde iki ayrı katman olmalı:

1. query katmanı
2. v1 composer katmanı

### Query Katmanı

Var olan `geodata_providers/services/queries/` kullanılmalı:

- `ProviderQueryService`
- `WorkspaceQueryService`
- `LayerQueryService`
- `StyleQueryService`

Bu katman:

- aktif provider filtresi
- public/published görünürlük filtresi
- DB ilişkileri

işlerini çözer.

Ek kural:

- `catalog_api` visibility, provider, workspace ve layer read mantığını mümkün
  olduğunca burada çözmeli.
- `catalog_api` aynı filtreleri veya aynı relation traversal mantığını ikinci kez
  kendi içinde yazmamalı.
- Eğer v1 endpoint'i yeni bir read ihtiyacı doğurursa çözüm yeri
  önce `geodata_providers/services/queries/` olmalı.
- `catalog_api` içinde sadece:
  - v1 response composition
  - URL/href üretimi
  - GeoServer remote enrichment
  - provider-specific response mapping
  kalmalı.

### V1 Composer Katmanı

Yeni katman:

- frontendin beklediği GeoServer REST shape'ini üretir
- gerekirse GeoServer client ile remote detail çeker
- local DB + remote GeoServer verisini birleştirir

Önerilen servisler:

- `catalog_api/services/v1/workspace_v1_service.py`
- `catalog_api/services/v1/layer_v1_service.py`
- `catalog_api/services/v1/style_v1_service.py`
- `catalog_api/services/v1/geoserver_v1_builder.py`

## Aşama 1 Veri Kaynakları

### DB'den Gelecek Bilgiler

- provider / engine
- workspace
- store
- layer
- visibility
- publishing state
- layer title / description
- layer name
- store name
- workspace name

### GeoServer'dan Gelecek Bilgiler

- layer info
- featureType veya coverage detail
- style detail
- bbox / attributes / keywords gibi remote metadata

## Aşama 1 İçin Django Model Stratejisi

### Zorunlu Yeni Tablo Var Mı

İlk aşamada zorunlu yeni tablo yok.

Mevcut tablolar yeterli başlangıç sağlar:

- `GeodataEngine`
- `Workspace`
- `Store`
- `Layer`

### Önerilen Model Genişletmeleri

İlk aşamada zorunlu olmasa da aşağıdaki alanlar yüksek değer taşır:

#### `GeodataEngine`

- `public_base_url`
  Amaç: frontendin kullandığı public GeoServer map base URL

- `public_rest_url`
  Amaç: istenirse public REST URL ayrı tutulabilir, ama ilk aşamada şart değil

Not:

- mevcut `base_url` backend iç erişim URL'si olarak kalabilir
- örnek: `http://geoserver:8080/geoserver`
- `public_base_url` örnek: `https://maps.example.com/geoserver`

#### `Layer`

İlk aşamada yeni alan şart değildir.

Ama ileride şu alanlar faydalı olabilir:

- `catalog_data_type`
- `catalog_protocol_hint`
- `catalog_default_style_name`
- `catalog_last_synced_at`

### Neden İlk Aşamada Yeni Tablo Açmıyoruz

İlk hedef v1 layer olduğu için önce runtime composition ile başlamak
daha güvenlidir.

Önce:

- sistem çalışsın
- shape otursun
- frontend bozulmasın

Sonra:

- gerekirse denormalized catalog snapshot alanları eklenir

## Aşama 1 Input / Output Sözleşmesi

### Input

İlk aşamada backend input olarak şunları alır:

- path parametreleri:
  - `workspace_name`
  - `layer_name`
  - `style_name`
- auth context:
  - ilk aşamada public catalog, ama görünürlük kuralı backend içinden uygulanır

### Output

İlk aşamada output tamamen frontend v1 contract'ye göre verilir.

Bu yüzden output contract'ın birincil kaynağı:

- `docs/development/geoserver.ts`
- `docs/development/map.ts`

olmalıdır.

## Aşama 1 Uygulama Adımları

### Step 1 🟢➡️

`catalog_api` app'i açılır.

### Step 2 🟢➡️

V1 endpointleri yazılır:

- workspaces
- layers
- layer info
- layer detail
- style detail

### Step 3 🟢➡️

`geodata_providers` query service'leri ile görünür katalog filtrelenir.

Not:

- Bu adım "query reuse first" prensibiyle yapılmalı.
- Workspace/layer görünürlük mantığı doğrudan `catalog_api` içinde tekrar
  yazılmamalı.
- Gerekirse `WorkspaceQueryService` ve `LayerQueryService` genişletilmeli ya da
  `geodata_providers` altında yeni bir visibility query service açılmalı.

### Step 4 🟢➡️

GeoServer client üzerinden eksik remote detail çekilir.

### Step 5 🟢➡️

V1 builder ile frontendin beklediği birebir response shape üretilir.

### Step 6

Frontend env sadece REST endpointi Django'ya bakacak şekilde değiştirilir.

### Step 7

Sistem mevcut frontend koduyla ayağa kaldırılır.

## Aşama 1 Riskler

### 1. Raster Domain Eksikliği

Mevcut domain modeli daha çok vector/PostGIS layerlara yakın duruyor.
Gerçek raster/coverage desteği için ek modelleme gerekebilir.

Bu yüzden v1 için şu karar açık yazılmalı:

- vector-first mi gidilecek
- yoksa raster da aynı sprintte desteklenecek mi

### 2. GeoServer Remote Drift

GeoServer response'ları değişirse v1 builder etkilenir.

### 3. Href Alanları

Frontend aktif kullanmıyorsa boş bırakılabilir.
Ama kullanıyorsa mutlaka Django URL dönülmelidir.

## Aşama 2: Generalized Catalog API

### Hedef

V1 katmanını koruyarak, daha temiz ve provider-aware bir katalog
kontratı tanımlamak.

### Temel Prensip

- üst seviye contract standart olur
- provider-specific payload ayrı blokta taşınır
- query/read mantığının ana sahibi yine `geodata_providers/services/queries/`
  olur
- `catalog_api` v2 de doğrudan ORM-first değil, query-service-first yaklaşımıyla
  kurulmalıdır

Örnek:

```json
{
  "id": "layer-id",
  "name": "tram_lines",
  "title": "Tram Lines",
  "provider_type": "geoserver",
  "workspace_name": "mobility",
  "data_type": "vector",
  "supported_protocols": ["wmts", "wms"],
  "provider_payload": {
    "geoserver": {
      "workspace_name": "mobility",
      "layer_name": "tram_lines",
      "style_name": "default"
    }
  }
}
```

### Aşama 2 Endpointleri

- `GET /api/catalog/providers/`
- `GET /api/catalog/providers/{provider_id}/workspaces/{workspace_id}/`
- `GET /api/catalog/layers/{layer_id}/`
- `GET /api/catalog/styles/`
- `GET /api/catalog/styles/{style_id}/`

### Aşama 2 Davranışı

- frontend artık GeoServer-shaped v1 shape'e mecbur olmaz
- backend daha sade ve sürdürülebilir bir kontrat sunar
- GeoServer-specific detaylar root response'u kirletmez

## Aşama 1 ve Aşama 2 Birlikte Nasıl Yaşar

İki yüzey aynı anda yaşayabilir:

- `/api/catalog/v1/...`
- `/api/catalog/...`

Bu sayede:

- eski frontend bozulmaz
- yeni frontend parça parça generic contract'a taşınır

Her iki yüzey için ortak kural:

- visibility/filter mantığı tek yerde yaşamalı
- provider/workspace/layer read davranışı iki app içinde kopyalanmamalı
- ortak read ownership `geodata_providers/services/queries/` içinde kalmalı

## Önerilen Dosya Yapısı

- `tosca_api/apps/catalog_api/apps.py`
- `tosca_api/apps/catalog_api/urls.py`
- `tosca_api/apps/catalog_api/views.py`
- `tosca_api/apps/catalog_api/serializers.py`
- `tosca_api/apps/catalog_api/services/v1/workspace_v1_service.py`
- `tosca_api/apps/catalog_api/services/v1/layer_v1_service.py`
- `tosca_api/apps/catalog_api/services/v1/style_v1_service.py`
- `tosca_api/apps/catalog_api/services/v1/geoserver_v1_builder.py`
- `tosca_api/apps/catalog_api/services/general/provider_catalog_service.py`
- `tosca_api/apps/catalog_api/services/general/workspace_catalog_service.py`
- `tosca_api/apps/catalog_api/services/general/layer_catalog_service.py`

## Net Tavsiye

İlk implementasyon sırası şu olmalı:

1. v1 endpointleri
2. frontend env switch
3. system smoke test
4. generic catalog endpointleri
5. v1 katmanını yavaş yavaş küçültme

## Final Karar

Bu projede en doğru yaklaşım:

- ilk aşamada frontend'e hiç dokunmamak
- Django'yu GeoServer REST-shaped v1 katmanı yapmak
- mevcut frontend contract'ını birebir korumak
- sistem çalıştıktan sonra daha genel katalog kontratına evrilmek

Bu doküman bu kararın resmi tasarım notu olarak kullanılmalıdır.
