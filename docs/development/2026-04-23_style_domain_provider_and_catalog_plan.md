# Style Domain Provider And Catalog Plan

Tarih: 2026-04-23

## Amaç

Bu dokümanın amacı `Style` domain'ini önce `geodata_providers` içinde doğru
şekilde modellemek, admin panelden yönetilebilir yapmak, GeoServer'a SLD ve
MBStyle olarak upload edebilmek, validasyon/test yüzeyini kurmak ve daha sonra
`catalog_api` v1 ile dışarı okunabilir hale getirmektir.

Bu iş iki ana fazda yapılmalıdır:

1. Provider fazı: `geodata_providers` içinde style model, admin, servis,
   validasyon ve GeoServer upload/sync davranışı.
2. Catalog fazı: `catalog_api` içinde frontend'in beklediği style detail
   response'unu provider style domain'i üstünden üretmek.

Öncelik provider fazıdır. Catalog tarafına geçmeden önce style domain'i admin ve
servis katmanında güvenilir hale gelmelidir.

## Mevcut Durum

Şu anda style için gerçek domain yoktur.

Mevcut boşluklar:

- `geodata_providers` içinde `Style` modeli yok.
- `Layer` ile style ilişkisi yok.
- Admin panelde style upload/edit/delete yeri yok.
- SLD / MBStyle dosyası validasyonu yok.
- GeoServer'a style upload/delete/assign yapan command service yok.
- `StyleQueryService` placeholder durumunda.
- `catalog_api` v1 style endpoint'i gerçek domain'e değil, GeoServer remote
  best-effort read davranışına dayanıyor.

Bu yüzden style işi önce provider domain'de çözülmelidir.

## Temel Kararlar

- Style ownership `geodata_providers` içinde olacaktır.
- Style, bir `GeodataEngine` ve opsiyonel bir `Workspace` bağlamında tutulur.
- Workspace `null` ise style global GeoServer style olarak kabul edilir.
- Workspace dolu ise style workspace-scoped GeoServer style olarak kabul edilir.
- Style adı GeoServer style identifier'dır ve GeoServer'a aynı isimle upload
  edilir.
- Desteklenen formatlar ilk fazda sadece:
  - `sld`
  - `mbstyle`
- Style dosyası önce local olarak validate edilir.
- Local validasyon geçmeden GeoServer'a upload yapılmaz.
- GeoServer upload başarılı olmadan Django kaydı "published/synced" sayılmaz.
- GeoServer REST Styles API içinde ayrı bir native `validate` endpoint yoktur.
  Bu yüzden "valid mi?" kontrolü bizim local validation servisimiz ve upload
  sonrası verify/smoke read davranışı ile sağlanacaktır.
- Delete davranışı GeoServer-first olmalıdır.
- Catalog API style okuması doğrudan GeoServer'a dağınık çağrı yapmamalı;
  önce provider query/service katmanı kullanılmalıdır.

## Faz 1: Provider Style Domain

### 1. Model Tasarımı

Yeni model:

- `tosca_api/apps/geodata_providers/models.py`
- model adı: `Style`

Önerilen alanlar:

| Field | Type | Not |
|---|---|---|
| `id` | UUIDField primary key | Diğer provider modelleriyle tutarlı |
| `geodata_engine` | FK `GeodataEngine` | Zorunlu |
| `workspace` | FK `Workspace`, null/blank | Null ise global style |
| `name` | CharField max 100 | GeoServer style adı |
| `title` | CharField max 200 blank | Admin/UI için insan okunur ad |
| `description` | TextField blank | Açıklama |
| `format` | CharField choices | `sld`, `mbstyle` |
| `file_name` | CharField max 255 | Upload edilen dosya adı |
| `file_content` | TextField | Raw SLD XML veya MBStyle JSON |
| `content_hash` | CharField max 64 | SHA-256, değişiklik takibi için |
| `validation_state` | CharField choices | `UNKNOWN`, `VALID`, `INVALID` |
| `validation_errors` | JSONField default list | Validasyon hata detayları |
| `remote_state` | CharField choices | `LOCAL_ONLY`, `UPLOADED`, `FAILED`, `DELETED` |
| `remote_error` | TextField blank | Son GeoServer hata mesajı |
| `remote_uploaded_at` | DateTimeField null | Son başarılı upload zamanı |
| `remote_verified_at` | DateTimeField null | Son başarılı verify zamanı |
| `created_at` | DateTimeField auto_now_add | |
| `updated_at` | DateTimeField auto_now | |
| `created_by` | FK User | |

Choices:

```python
STYLE_FORMATS = [
    ("sld", "SLD"),
    ("mbstyle", "MBStyle"),
]

VALIDATION_STATES = [
    ("UNKNOWN", "Unknown"),
    ("VALID", "Valid"),
    ("INVALID", "Invalid"),
]

REMOTE_STATES = [
    ("LOCAL_ONLY", "Local only"),
    ("UPLOADED", "Uploaded"),
    ("FAILED", "Failed"),
    ("DELETED", "Deleted"),
]
```

Model constraint:

- `UniqueConstraint(fields=["geodata_engine", "workspace", "name"], ...)`
- `workspace.geodata_engine_id == geodata_engine_id` olmalı.

Model helper property'leri:

- `is_global`
- `is_valid`
- `is_uploaded`
- `qualified_name`
  - workspace varsa `{workspace.name}:{name}`
  - global ise `{name}`

Not:

- İlk fazda dosyayı fiziksel storage'a koymak şart değil. Raw content DB'de
  tutulabilir. Eğer ileride dosya boyutları büyürse `FileField` + object storage
  planlanabilir.

### 2. Layer-Style İlişkisi

Sadece `Style` modeli yetmez; layer hangi style'ı kullanıyor bilinmelidir.

Yeni model önerisi:

- model adı: `LayerStyle`

Alanlar:

| Field | Type | Not |
|---|---|---|
| `id` | UUIDField primary key | |
| `layer` | FK `Layer` related_name `style_links` | |
| `style` | FK `Style` related_name `layer_links` | |
| `role` | CharField choices | `default`, `alternate` |
| `is_active` | BooleanField | |
| `created_at` | DateTimeField auto_now_add | |
| `updated_at` | DateTimeField auto_now | |
| `created_by` | FK User | |

Constraint:

- Bir layer için en fazla bir aktif default style olmalı.
- Style ile layer aynı `geodata_engine` üzerinde olmalı.
- Workspace-scoped style ise style workspace'i layer workspace'i ile aynı olmalı.
- Global style her workspace layer'ına atanabilir.

Neden `Layer.default_style` FK değil?

- GeoServer hem default style hem alternate styles destekler.
- İleride style listesi frontend'e açılabilir.
- Admin action ile layer'a birden fazla style atanabilir.

### 3. Migration

Beklenen migration:

- `Style` tablosu
- `LayerStyle` tablosu
- constraint/index tanımları

İsim önerisi:

- `0002_style_layerstyle.py`

Migration sonrası kontrol:

```bash
docker exec -it tosca-django bash -lc "uv run python manage.py makemigrations geodata_providers"
docker exec -it tosca-django bash -lc "uv run python manage.py migrate"
```

## Faz 2: Style Validasyon Katmanı

### 1. Dosya Yapısı

Yeni dosyalar:

- `tosca_api/apps/geodata_providers/services/commands/style_validation_service.py`
- `tosca_api/apps/geodata_providers/services/commands/style_service.py`
- `tosca_api/apps/geodata_providers/tests/test_style_validation_service.py`
- `tosca_api/apps/geodata_providers/tests/test_style_service.py`

### 2. StyleValidationService

Sorumluluk:

- SLD ve MBStyle içeriğini local olarak validate etmek.
- Validasyon sonucu normalize dict döndürmek.
- GeoServer upload yapmamak.
- GeoServer'da native validate endpoint varmış gibi davranmamak.

Public API:

```python
class StyleValidationService:
    @classmethod
    def validate(cls, *, content: str, style_format: str) -> dict:
        ...

    @classmethod
    def validate_sld(cls, *, content: str) -> dict:
        ...

    @classmethod
    def validate_mbstyle(cls, *, content: str) -> dict:
        ...
```

Response shape:

```python
{
    "valid": True,
    "errors": [],
    "warnings": [],
    "metadata": {
        "format": "sld",
    },
}
```

SLD minimum validasyon:

- XML parse edilebilmeli.
- Root element local-name olarak `StyledLayerDescriptor` olmalı.
- En az bir `NamedLayer` veya `UserLayer` bulunmalı.
- XML namespace farklı olsa bile local-name kontrolü çalışmalı.

SLD ikinci seviye validasyon:

- `NamedLayer/Name` varsa style metadata'ya alınabilir.
- `UserStyle/Name` varsa style metadata'ya alınabilir.
- GeoServer'ın kabul etmeyeceği bariz XML syntax hataları yakalanmalı.

MBStyle minimum validasyon:

- JSON parse edilebilmeli.
- Root object dict olmalı.
- `version` alanı olmalı.
- `version == 8` olmalı.
- `layers` alanı list olmalı.
- `sources` alanı dict olmalı.

MBStyle ikinci seviye validasyon:

- Her layer içinde `id` ve `type` kontrol edilmeli.
- `type` değerleri Mapbox style spec tiplerinden biri olmalı:
  - `fill`
  - `line`
  - `symbol`
  - `circle`
  - `heatmap`
  - `fill-extrusion`
  - `raster`
  - `hillshade`
  - `background`
- `paint` ve `layout` object değilse warning/error üretilmeli.

Not:

- İlk fazda tam JSON schema validasyonu şart değildir.
- Ancak servis sınırı buna uygun tasarlanmalı; ileride `jsonschema` dependency
  eklenebilir.
- GeoServer'ın kendi REST API'sinde style validate endpoint'i olmadığı için bu
  servis provider tarafındaki ana validasyon kapısıdır.

### 3. Testler

Zorunlu testler:

- valid SLD geçer
- malformed XML fail olur
- root `StyledLayerDescriptor` değilse fail olur
- valid MBStyle geçer
- malformed JSON fail olur
- `version` eksikse fail olur
- `version != 8` fail olur
- `layers` list değilse fail olur
- desteklenmeyen MBStyle layer type fail/warning üretir

## Faz 3: GeoServer Client Style Methods

### 1. Dosya

Mevcut client:

- `tosca_api/apps/geodata_providers/geoserver/client.py`

Eklenecek methodlar:

```python
class GeoServerClient:
    def upload_style(self, *, name: str, content: str, style_format: str, workspace: str | None = None) -> dict:
        ...

    def delete_style(self, *, name: str, workspace: str | None = None) -> dict:
        ...

    def get_style(self, *, name: str, workspace: str | None = None, style_format: str | None = None) -> dict | None:
        ...

    def list_styles(self, *, workspace: str | None = None) -> list[dict]:
        ...

    def assign_style_to_layer(self, *, workspace: str, layer_name: str, style_name: str) -> dict:
        ...
```

### 2. Upload Davranışı

GeoServer style upload iki adımlı yapılmalıdır:

1. Style metadata oluştur.
2. Style content upload et.

Global style metadata endpoint:

- `POST /rest/styles`

Workspace style metadata endpoint:

- `POST /rest/workspaces/{workspace}/styles`

Metadata body:

```xml
<style>
  <name>{style_name}</name>
  <filename>{style_name}.sld</filename>
</style>
```

MBStyle için filename:

```xml
<style>
  <name>{style_name}</name>
  <filename>{style_name}.json</filename>
</style>
```

Content upload endpoint:

- global: `PUT /rest/styles/{style_name}`
- workspace: `PUT /rest/workspaces/{workspace}/styles/{style_name}`

Content-Type:

- SLD: `application/vnd.ogc.sld+xml`
- MBStyle: `application/vnd.geoserver.mbstyle+json`

Not:

- Eğer style zaten varsa metadata create `409` dönebilir. Bu durumda content
  update denenebilir.
- İlk implementasyonda açık `overwrite=True` parametresi olmadan mevcut style
  üstüne yazılmamalı.
- `StyleService` tarafında overwrite kararı açık verilmeli.

### 3. Verify Davranışı

GeoServer REST Styles API'de ayrı bir `validate` endpoint yoktur.

Bu bölümdeki verify davranışı validasyon değildir. Amaç, local validasyondan
geçen ve GeoServer'a upload edilen style'ın gerçekten GeoServer tarafından
kaydedildiğini ve okunabildiğini doğrulamaktır.

Upload sonrası verify:

- `GET /rest/styles/{style_name}.json`
- workspace için `GET /rest/workspaces/{workspace}/styles/{style_name}.json`

MBStyle content gerekiyorsa ayrıca:

- `GET /rest/styles/{style_name}.mbstyle`
- workspace için `GET /rest/workspaces/{workspace}/styles/{style_name}.mbstyle`

Verify başarılı sayılacak minimum koşul:

- GeoServer `200` döner.
- Response içinde style name eşleşir.

Verify başarısızlığı şu anlama gelir:

- Local content syntactically valid olabilir.
- Ancak GeoServer style'ı kabul etmemiş, kaydetmemiş veya beklenen endpoint'ten
  okuyamamış olabilir.
- Bu durumda `remote_state=FAILED` olmalı, `validation_state=VALID` kalabilir.

### 4. Assign Davranışı

Layer default style set endpoint:

- `PUT /rest/layers/{workspace}:{layer_name}`

Payload:

```json
{
  "layer": {
    "defaultStyle": {
      "name": "{style_name}"
    }
  }
}
```

Workspace-scoped style için gerekirse style name:

- `{workspace}:{style_name}`

Bu nokta implementasyon sırasında gerçek GeoServer davranışıyla smoke test
edilmelidir.

## Faz 4: StyleService Command Katmanı

### 1. Amaç

Admin ve ileride console/API aynı iş mantığını kullanmalıdır.

Style write flow'u admin içine gömülmemeli; servis içinde yaşamalıdır.

### 2. Public API

```python
class StyleService:
    @classmethod
    def create_style(cls, *, data: dict, file_content: str, user) -> Style:
        ...

    @classmethod
    def validate_style(cls, *, style: Style) -> dict:
        ...

    @classmethod
    def upload_style(cls, *, style: Style, overwrite: bool = False) -> dict:
        ...

    @classmethod
    def delete_style(cls, *, style: Style) -> dict:
        ...

    @classmethod
    def assign_to_layer(cls, *, style: Style, layer: Layer, user) -> LayerStyle:
        ...
```

### 3. Create Flow

Sıra:

1. Dosya içeriğini oku.
2. Formatı extension veya form field üzerinden belirle.
3. `StyleValidationService.validate(...)` çalıştır.
4. Invalid ise Django kaydı oluşturma.
5. Valid ise `Style` kaydını `LOCAL_ONLY + VALID` olarak oluştur.
6. `upload_style(...)` çağrıldıysa GeoServer'a upload et.
7. Verify başarılı ise `remote_state=UPLOADED` yap.
8. Verify başarısız ise `remote_state=FAILED`, `remote_error` set et.

### 4. Delete Flow

Sıra:

1. Style layer'a atanmış mı kontrol et.
2. Eğer atanmışsa default davranış delete engellemek olmalı.
3. Force delete ayrı admin action olabilir.
4. GeoServer delete çağrısı yap.
5. GeoServer delete başarılıysa Django kaydını sil.
6. GeoServer delete başarısızsa Django kaydı silinmemeli.

### 5. Assign Flow

Sıra:

1. Style valid mi kontrol et.
2. Style uploaded mı kontrol et.
3. Style ile layer aynı provider mı kontrol et.
4. Workspace uyumu kontrol et.
5. GeoServer default style set çağrısı yap.
6. Başarılıysa `LayerStyle` kaydı oluştur/güncelle.
7. Aynı layer için eski default style varsa `role=alternate` veya
   `is_active=False` yapılmalı. İlk implementasyonda eski default `is_active=False`
   yapılması daha basit.

## Faz 5: Admin Panel

### 1. Admin Konumlandırma

`Style` modeli admin panelde `geodata_providers` app'i altında, `Layer` modelinin
hemen altında görünmelidir.

Mevcut admin ordering:

- `GeodataEngine`
- `Workspace`
- `Store`
- `Layer`

Güncellenmiş ordering:

- `GeodataEngine`
- `Workspace`
- `Store`
- `Layer`
- `Style`
- `LayerStyle` gerekiyorsa gizli ya da alt model olarak

`_GEODATA_PROVIDER_ADMIN_ORDER` içine:

```python
"Style": 4,
"LayerStyle": 5,
```

`_GEODATA_PROVIDER_ADMIN_LABELS` içine:

```python
"Style": "Style",
"LayerStyle": "Layer Style",
```

Kullanıcı beklentisi:

- Admin panelde Layers bölümünün hemen altında `Style` görülecek.
- Style yönetimi provider domain içinde kalacak.

### 2. StyleAdmin

Yeni admin:

- `StyleAdmin(RemoteDeleteAdminMixin, admin.ModelAdmin)`

List display:

- `name`
- `format`
- `workspace`
- `geodata_engine`
- `validation_badge`
- `remote_state_badge`
- `remote_uploaded_at`
- `updated_at`

List filters:

- `format`
- `validation_state`
- `remote_state`
- `geodata_engine`
- `workspace`

Search:

- `name`
- `title`
- `description`
- `workspace__name`
- `geodata_engine__name`

Readonly fields:

- `content_hash`
- `validation_state`
- `validation_errors`
- `remote_state`
- `remote_error`
- `remote_uploaded_at`
- `remote_verified_at`
- `created_at`
- `updated_at`

### 3. StyleUploadForm

Form alanları:

- `geodata_engine`
- `workspace`
- `name`
- `title`
- `description`
- `format`
- `file`
- `upload_to_geoserver` checkbox default true
- `overwrite_remote` checkbox default false

Davranış:

- Dosya extension `.sld` ise format default `sld`.
- Dosya extension `.json` veya `.mbstyle` ise format default `mbstyle`.
- `name` boşsa dosya adından slug/GeoServer-safe name üretilir.
- Workspace seçildiyse geodata_engine otomatik workspace engine'i ile uyumlu
  olmalıdır.

Name sanitization:

- lowercase önerilir ama zorunlu değildir.
- boşluk yerine `_` veya `-`.
- GeoServer için güvenli regex:
  - `^[A-Za-z0-9_\\-\\.]+$`

### 4. Admin Actions

StyleAdmin actions:

- `validate_selected_styles`
- `upload_selected_styles`
- `verify_selected_styles`
- `delete_remote_styles`

LayerAdmin action:

- `assign_style_to_selected_layers`

Layer detail admin içinde:

- default style bilgisi readonly gösterilebilir.
- style assignment inline olarak ikinci fazda eklenebilir.

### 5. Intermediate Assign Form

LayerAdmin action tek tıkla çalışmamalı; intermediate form göstermeli.

Form:

- target style dropdown
- overwrite default style checkbox

Dropdown sadece şu style'ları göstermeli:

- aynı provider
- global veya layer workspace'i ile aynı workspace
- `validation_state=VALID`
- `remote_state=UPLOADED`

## Faz 6: Query Services

Mevcut placeholder:

- `tosca_api/apps/geodata_providers/services/queries/style_query_service.py`

Gerçek hale getirilecek methodlar:

```python
class StyleQueryService:
    @classmethod
    def list_styles(cls, *, provider_id=None, workspace_id=None, uploaded_only=False) -> list[dict]:
        ...

    @classmethod
    def get_style_detail(cls, *, style_id) -> dict:
        ...

    @classmethod
    def get_style_by_name(cls, *, geodata_engine_id, workspace_name=None, style_name: str) -> dict:
        ...

    @classmethod
    def get_layer_default_style(cls, *, layer_id) -> dict | None:
        ...
```

Normalized style dict:

```json
{
  "id": "uuid",
  "name": "tram_lines_default",
  "title": "Tram Lines Default",
  "format": "mbstyle",
  "workspace": {
    "id": "uuid",
    "name": "mobility"
  },
  "provider": {
    "id": "uuid",
    "name": "Catalog Engine",
    "engine_type": "geoserver"
  },
  "validation_state": "VALID",
  "remote_state": "UPLOADED",
  "content_hash": "sha256",
  "updated_at": "2026-04-23T10:00:00Z"
}
```

Content ayrı methodla dönmeli:

```python
StyleQueryService.get_style_content(style_id=...)
```

Neden?

- Liste endpointleri büyük raw style content taşımamalı.

## Faz 7: Catalog API Entegrasyonu

Provider fazı bitmeden catalog style endpoint'i tamamlanmış sayılmamalıdır.

### 1. Layer Info

`catalog_api` v1 layer info response içindeki:

```json
"defaultStyle": {
  "name": "default",
  "href": "https://api.example.com/api/catalog/v1/styles/default"
}
```

şu kaynaktan gelmelidir:

1. Layer'a atanmış active default `LayerStyle`
2. Yoksa provider/workspace default style fallback
3. Yoksa `"default"` fallback

İlk implementasyon için fallback kabul edilebilir, ama atanmış style varsa mutlaka
DB'den gelmelidir.

### 2. Style Detail Endpoint

Mevcut endpoint:

- `GET /api/catalog/v1/styles/{style_name}`

Provider sonrası hedef:

- önce DB'den style bul
- format `mbstyle` ise raw MBStyle JSON dön
- format `sld` ise frontend MBStyle beklediği için iki seçenekten biri:
  - `406 Not Acceptable`
  - veya SLD'yi raw XML olarak sadece uygun `Accept` header ile dön

İlk catalog v1 kararı:

- Frontend `getLayerStyling(...)` MBStyle JSON beklediği için v1 style endpoint
  sadece `mbstyle` style'ları frontend-consumable kabul etsin.
- SLD style varsa endpoint `404` veya `406` dönmeli. Daha doğru olan `406`.

### 3. Style URL Ambiguity

Style name tek başına global sistemde belirsiz olabilir.

Mevcut frontend `defaultStyle.href` backend'den geldiği için daha doğru href
şu olabilir:

- `/api/catalog/v1/workspaces/{workspace_name}/styles/{style_name}`

Ancak mevcut v1 endpoint:

- `/api/catalog/v1/styles/{style_name}`

İlk implementasyonda `style_name` lookup şu sırayla yapılmalı:

1. workspace-scoped style, layer context biliniyorsa
2. default provider style
3. global style

Ama style detail endpoint tek başına layer context almadığı için daha sağlam
tasarım:

- Layer info response içindeki `defaultStyle.href` workspace-scoped endpoint
  üretmeli.

Önerilen yeni catalog v1 endpoint:

- `GET /api/catalog/v1/workspaces/{workspace_name}/styles/{style_name}`

Eski endpoint kalabilir:

- `GET /api/catalog/v1/styles/{style_name}`

Ama sadece global/default style için kullanılmalıdır.

### 4. Catalog Tests

Zorunlu testler:

- layer info atanmış default style adını döner
- layer info style href'i workspace-scoped endpoint üretir
- MBStyle detail raw JSON döner
- SLD style frontend endpointinde `406` döner
- missing style `404`
- private/draft layer style bilgisi leak etmez
- inactive provider style bilgisi leak etmez

## Faz 8: API / Console Durumu

Bu fazda dışarı public `geodata_providers` API açılmayacak.

Kurallar:

- Style write işlemleri admin panel ve provider command service ile yapılacak.
- Swagger UI sadece catalog endpointlerini göstermeye devam edecek.
- `/api/geoengine/` internal kalacak.
- Catalog endpointleri sadece read surface olacak.

## Faz 9: Test Planı

### Unit Tests

- `test_style_validation_service.py`
- `test_style_service.py`
- `test_style_query_service.py`
- `test_style_model.py`

### Admin Tests

- `StyleUploadForm` valid SLD
- `StyleUploadForm` invalid SLD
- `StyleUploadForm` valid MBStyle
- `StyleUploadForm` invalid MBStyle
- `StyleAdmin.save_model` service çağırır
- `StyleAdmin.delete_model` GeoServer-first davranır
- `LayerAdmin.assign_style_to_selected_layers` intermediate form kullanır

### GeoServer Client Tests

Mock `_request` ile:

- SLD upload doğru endpoint/content-type
- MBStyle upload doğru endpoint/content-type
- workspace style endpoint doğru kurulur
- global style endpoint doğru kurulur
- delete style doğru endpoint
- assign style doğru payload

### Catalog Tests

- `test_v1_api.py` içinde style domain bağlı testler
- ayrı `test_v1_style_api.py` açmak daha temiz olur

## Faz 10: Uygulama Sırası

### Step 1

Model ekle:

- `Style`
- `LayerStyle`

Test:

- model constraint
- workspace/provider uyumu

### Step 2

Validation service ekle:

- SLD validation
- MBStyle validation

Test:

- valid/invalid dosyalar

### Step 3

GeoServer client style methodlarını ekle.

Test:

- mock request endpoint ve payload assertion

### Step 4

StyleService command katmanını ekle.

Test:

- create
- validate
- upload
- delete
- assign

### Step 5

Admin paneli ekle.

Test:

- form
- admin save/delete/action

### Step 6

StyleQueryService placeholder'ını gerçek implementation yap.

Test:

- list/detail/content/default style

### Step 7

Catalog API v1 style endpointini provider style domain'e bağla.

Test:

- layer info default style
- style detail MBStyle
- SLD için `406`

### Step 8

Swagger ve development dokümanlarını güncelle.

Kontrol:

- Swagger sadece catalog read endpointlerini göstermeli.
- `geoengine` style write endpointleri public Swagger'a girmemeli.

## Kabul Kriterleri

Provider tarafı tamam sayılmak için:

- Admin panelde `Layer` altında `Style` modeli görünür.
- Admin'den `.sld`, `.json`, `.mbstyle` upload edilebilir.
- Invalid SLD/MBStyle kaydedilmeden reddedilir.
- Validasyon bizim `StyleValidationService` katmanımızda yapılır; GeoServer'da
  native validate endpoint aranmaz.
- Valid style Django DB'ye kaydedilir.
- Upload seçiliyse style GeoServer'a aynı `name` ile yüklenir.
- Upload sonrası GeoServer'dan verify edilir.
- Style layer'a default style olarak atanabilir.
- Delete GeoServer-first çalışır.
- Unit/admin/client/service testleri geçer.

Catalog tarafı tamam sayılmak için:

- `LayerInfo` default style bilgisini DB'deki `LayerStyle` ilişkisinden üretir.
- `defaultStyle.href` doğru catalog v1 style endpointine gider.
- MBStyle style detail frontend'in beklediği JSON'u döner.
- SLD style frontend MBStyle endpointinde yanlışlıkla JSON gibi dönmez.
- Missing/private/inactive provider style leak etmez.

## Açık Kararlar

1. SLD style'lar catalog v1 frontend endpointinde `404` mı `406` mı dönmeli?

Öneri:

- `406 Not Acceptable`

2. Style name global unique mi, provider/workspace scoped unique mi?

Öneri:

- provider + workspace + name unique

3. Workspace null global style gerekli mi?

Öneri:

- Evet, GeoServer global style davranışı için gerekli.

4. Style content DB'de mi file storage'da mı tutulmalı?

Öneri:

- İlk fazda DB TextField. Büyük style dosyaları problem olursa sonra storage.

5. Layer'a birden fazla style atanacak mı?

Öneri:

- Evet, `LayerStyle` ile desteklenmeli. İlk UI default style assignment ile
  başlayabilir.

6. GeoServer style validate endpoint'i kullanılacak mı?

Karar:

- Hayır. GeoServer REST Styles API'de ayrı bir validate endpoint yoktur.
- Validation local yapılacak.
- GeoServer tarafı sadece upload sonrası `GET/list` ile verify edilecek.

## Önerilen Commit Sırası

1. `feat(geodata): add style domain models`
2. `feat(geodata): add style validation service`
3. `feat(geodata): add geoserver style client methods`
4. `feat(geodata): add style admin workflows`
5. `feat(geodata): implement style query service`
6. `feat(catalog): serve v1 style details from provider styles`

Bu sırayla gidilirse her commit test edilebilir ve revert edilmesi kolay olur.
