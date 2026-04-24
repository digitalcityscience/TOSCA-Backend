# Style Domain Provider And Catalog Plan

Tarih: 2026-04-23

## Amaç

Bu dokümanın amacı `Style` domain'ini `geodata_providers` içinde tek ve merkezi
bir domain olarak modellemek, admin panelde yönetilebilir yapmak, destekleyen
provider'larla sync edebilmek ve catalog API'de layer detaylarıyla birlikte
okunabilir hale getirmektir.

Temel hikaye:

- `Style`, `Workspace`, `Store`, `Layer` gibi provider domain'in bir parçasıdır.
- Django style için tek yetkili kaynaktır.
- Provider style destekliyorsa style o provider'a sync edilebilir.
- Provider style desteklemiyorsa style Django'da kalır ve catalog response'unda
  kullanılmaya devam eder.
- Layer altında ayrı bir "Layer Style" domain'i yoktur. Layer detayında style
  seçimi/atanması vardır.
- Bir Martin layer'ı, Django'da kayıtlı ve örneğin GeoServer provider'ına sync
  edilmiş bir style'ı kullanabilir. Catalog API Martin layer detayını dönerken
  seçili style bilgisini de kullanıcıya dönebilmelidir.

## Revize Temel Kararlar

- `Style` tek ana domain modelidir.
- Style admin panelde `geodata_providers` altında ayrı bir kaynak olarak görünür.
- Style'ın bir owner/sync provider'ı olabilir:
  - GeoServer provider: remote style sync desteklenir.
  - Martin provider: native style sync yoksa style `LOCAL_ONLY` veya
    `UNSUPPORTED` kalır.
- Style workspace-scoped veya global olabilir.
- Workspace scope, style'ın provider içindeki organizasyon/sync context'idir.
  Layer'a atanabilmesi için layer ile aynı provider/workspace olmak zorunda
  değildir.
- Layer ile style ilişkisi ayrı bir business domain değildir; bu ilişki layer
  detayında kullanılan assignment bilgisidir.
- Assignment modelinin adı teknik olarak `LayerStyle` veya
  `LayerStyleAssignment` olabilir, fakat admin menüsünde ayrı ana kaynak gibi
  gösterilmemelidir.
- Catalog API style bilgisini GeoServer'a dağınık çağrı yaparak değil, Django
  style domain'i ve layer assignment üstünden üretmelidir.
- Cross-provider assignment desteklenmelidir:
  - Martin layer + GeoServer style geçerli bir senaryodur.
  - Martin layer + local-only Django style da geçerli olabilir.
- Remote sync ve catalog kullanım birbirinden ayrıdır:
  - Remote sync, provider capability meselesidir.
  - Catalog kullanım, Django'daki style content/metadata meselesidir.

## Mevcut Implementasyon Değerlendirmesi

Son yapılan Faz 1 implementasyonu bazı parçaları doğru kurdu, ancak yeni hikaye
ile tamamen uyumlu değildir. Bu yüzden önce düzeltme fazı gereklidir.

Uyumlu parçalar:

- `Style` modelinin ana domain olarak eklenmesi doğru yöndedir.
- Style content'in Django DB'de tutulması ilk faz için uygundur.
- `content_hash`, validation state ve remote state alanları faydalıdır.
- Style'ın admin panelde görünmesi gereklidir.
- Layer ile style arasında assignment kaydı tutulması doğrudur.

Uyumsuz / revize edilecek parçalar:

- `LayerStyle` ayrı admin ana kaynağı gibi gösterilmemelidir.
- `LayerStyle` ismi domain gibi algılanıyor. Gerekirse teknik model adı
  `LayerStyleAssignment` olarak değiştirilmelidir.
- Assignment için `style.geodata_engine == layer.workspace.geodata_engine`
  zorunluluğu yanlıştır. Martin layer'ın GeoServer style kullanabilmesi gerekir.
- Workspace-scoped style'ın sadece aynı workspace layer'a atanabilmesi kuralı
  yeni hikaye ile uyumlu değildir.
- Remote state değerleri provider capability'yi ifade etmiyor. Martin gibi style
  sync desteklemeyen provider için `UNSUPPORTED` veya `NOT_SUPPORTED` state'i
  gerekir.
- Faz 1 tamamlandı kabul edilemez; model ve admin düzeltmesi yapılmalıdır.

## Faz 1: Style Domain Model Revizyonu

### 1. Style Modeli

Model:

- `tosca_api/apps/geodata_providers/models.py`
- model adı: `Style`

Alanlar:

| Field | Type | Not |
|---|---|---|
| `id` | UUIDField primary key | Diğer provider modelleriyle tutarlı |
| `geodata_engine` | FK `GeodataEngine` | Style'ın owner/sync provider'ı |
| `workspace` | FK `Workspace`, null/blank | Provider içindeki style scope; null ise global |
| `name` | CharField max 100 | Provider/Django style identifier |
| `title` | CharField max 200 blank | Admin/UI için insan okunur ad |
| `description` | TextField blank | Açıklama |
| `format` | CharField choices | `sld`, `mbstyle` |
| `file_name` | CharField max 255 | Upload edilen dosya adı |
| `file_content` | TextField | Raw SLD XML veya MBStyle JSON |
| `content_hash` | CharField max 64 | SHA-256 |
| `validation_state` | CharField choices | `UNKNOWN`, `VALID`, `INVALID` |
| `validation_errors` | JSONField default list | Validasyon hata detayları |
| `remote_state` | CharField choices | `LOCAL_ONLY`, `SYNCED`, `FAILED`, `UNSUPPORTED`, `DELETED` |
| `remote_error` | TextField blank | Son provider sync hata mesajı |
| `remote_uploaded_at` | DateTimeField null | Son başarılı remote upload zamanı |
| `remote_verified_at` | DateTimeField null | Son başarılı remote verify zamanı |
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
    ("SYNCED", "Synced"),
    ("FAILED", "Failed"),
    ("UNSUPPORTED", "Unsupported by provider"),
    ("DELETED", "Deleted"),
]
```

Constraint:

- Style name provider/scope içinde unique olmalıdır.
- Global style için `geodata_engine + name` unique olmalıdır.
- Workspace style için `geodata_engine + workspace + name` unique olmalıdır.
- `workspace.geodata_engine_id == geodata_engine_id` sadece style'ın kendi
  owner/sync scope'u için geçerlidir.

Helper property'leri:

- `is_global`
- `is_valid`
- `is_synced`
- `is_remote_supported`
- `qualified_name`
  - workspace varsa `{workspace.name}:{name}`
  - global ise `{name}`

### 2. Layer Style Assignment

Layer altında style seçebilmek için teknik bir assignment modeli gerekir. Bu
ayrı bir Style domain'i değildir.

Önerilen model adı:

- `LayerStyleAssignment`

Eğer mevcut implementasyonda `LayerStyle` kaldıysa:

- Admin menüsünde ayrı ana model olarak gösterilmemeli.
- Kod içinde anlamı "assignment/link" olacak şekilde dokümante edilmeli.
- Mümkünse migration revizyonunda `LayerStyleAssignment` adına taşınmalı.

Alanlar:

| Field | Type | Not |
|---|---|---|
| `id` | UUIDField primary key | |
| `layer` | FK `Layer` related_name `style_assignments` | |
| `style` | FK `Style` related_name `layer_assignments` | |
| `role` | CharField choices | `default`, `alternate` |
| `is_active` | BooleanField | |
| `created_at` | DateTimeField auto_now_add | |
| `updated_at` | DateTimeField auto_now | |
| `created_by` | FK User | |

Kurallar:

- Bir layer için en fazla bir aktif default style olmalıdır.
- Aynı layer/style/role tekrarlanmamalıdır.
- Style valid olmalıdır.
- Style content catalog tarafından okunabilir olmalıdır.
- Style ile layer aynı provider üzerinde olmak zorunda değildir.
- Style workspace'i ile layer workspace'i aynı olmak zorunda değildir.
- Eğer ileride tenant/project güvenlik sınırı eklenirse assignment o sınırla
  kısıtlanmalıdır; provider/workspace eşitliğiyle değil.

Neden `Layer.default_style` FK değil?

- Layer bir default ve birden fazla alternate style taşıyabilir.
- UI ileride style listesi gösterebilir.
- Assignment metadata'sı gerekir.

### 3. Migration Revizyonu

Beklenen migration işi:

- Mevcut `0002_style_layerstyle.py` yeni karara göre revize edilmeli.
- Cross-provider assignment'ı engelleyen model validation kaldırılmalı.
- `LayerStyle` ayrı admin kaynak gibi kalacaksa admin registration kaldırılmalı.
- Remote state choices `UNSUPPORTED`/`SYNCED` kararına göre güncellenmeli.

Kontrol:

```bash
docker exec tosca-django uv run python manage.py makemigrations geodata_providers --check --dry-run
docker exec tosca-django uv run python manage.py migrate geodata_providers
```

## Faz 2: Style Validasyon Katmanı

Yeni dosyalar:

- `tosca_api/apps/geodata_providers/services/commands/style_validation_service.py`
- `tosca_api/apps/geodata_providers/tests/test_style_validation_service.py`

Sorumluluk:

- SLD ve MBStyle içeriğini local olarak validate etmek.
- Validasyon sonucu normalize dict döndürmek.
- Provider upload yapmamak.
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

MBStyle minimum validasyon:

- JSON parse edilebilmeli.
- Root object dict olmalı.
- `version` alanı olmalı.
- `version == 8` olmalı.
- `layers` alanı list olmalı.
- `sources` alanı dict olmalı.
- Her layer içinde `id` ve `type` kontrol edilmeli.
- Desteklenen type değerleri:
  - `fill`
  - `line`
  - `symbol`
  - `circle`
  - `heatmap`
  - `fill-extrusion`
  - `raster`
  - `hillshade`
  - `background`

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

## Faz 3: Provider Style Sync Capability

Amaç:

- Style sync davranışı provider capability'ye bağlı olmalıdır.
- GeoServer style sync destekler.
- Martin style sync desteklemiyorsa remote operation denenmemelidir.

Önerilen servis sınırı:

```python
class StyleProviderCapability:
    @classmethod
    def supports_remote_styles(cls, *, engine: GeodataEngine) -> bool:
        ...
```

Davranış:

- `engine_type == "geoserver"` ise remote style sync desteklenir.
- `engine_type == "martin"` ise şimdilik remote style sync desteklenmez.
- Desteklenmeyen provider için upload/sync çağrısı style'ı `UNSUPPORTED`
  yapmalı veya açık hata döndürmelidir; `FAILED` sayılmamalıdır.

## Faz 4: GeoServer Client Style Methods

Mevcut client:

- `tosca_api/apps/geodata_providers/geoserver/client.py`

Eklenecek methodlar:

```python
class GeoServerClient:
    def upload_style(self, *, name: str, content: str, style_format: str, workspace: str | None = None, overwrite: bool = False) -> dict:
        ...

    def delete_style(self, *, name: str, workspace: str | None = None) -> dict:
        ...

    def get_style(self, *, name: str, workspace: str | None = None, style_format: str | None = None) -> dict | None:
        ...

    def list_styles(self, *, workspace: str | None = None) -> list[dict]:
        ...
```

Not:

- GeoServer layer'a style assign endpoint'i client'ta bulunabilir, ancak bu
  Martin layer + GeoServer style gibi cross-provider catalog senaryolarını
  çözmez.
- Bu yüzden provider remote assign ve Django assignment birbirinden ayrılmalıdır.

GeoServer upload iki adımlı yapılmalıdır:

1. Style metadata oluştur.
2. Style content upload et.

Content-Type:

- SLD: `application/vnd.ogc.sld+xml`
- MBStyle: `application/vnd.geoserver.mbstyle+json`

Verify:

- `GET /rest/styles/{style_name}.json`
- workspace için `GET /rest/workspaces/{workspace}/styles/{style_name}.json`
- Minimum koşul: GeoServer `200` döner ve style name eşleşir.

## Faz 5: StyleService Command Katmanı

Amaç:

- Admin ve ileride console/API aynı iş mantığını kullanmalıdır.
- Style write flow'u admin içine gömülmemelidir.
- Remote sync ve layer assignment birbirinden ayrılmalıdır.

Public API:

```python
class StyleService:
    @classmethod
    def create_style(cls, *, data: dict, file_content: str, user) -> Style:
        ...

    @classmethod
    def validate_style(cls, *, style: Style) -> dict:
        ...

    @classmethod
    def sync_style(cls, *, style: Style, overwrite: bool = False) -> dict:
        ...

    @classmethod
    def delete_style(cls, *, style: Style, delete_remote: bool = True) -> dict:
        ...

    @classmethod
    def assign_style_to_layer(cls, *, style: Style, layer: Layer, user, role: str = "default") -> LayerStyleAssignment:
        ...
```

Create flow:

1. Dosya içeriğini oku.
2. Formatı extension veya form field üzerinden belirle.
3. `StyleValidationService.validate(...)` çalıştır.
4. Invalid ise Django kaydı oluşturma.
5. Valid ise `Style` kaydını oluştur.
6. Provider remote style destekliyorsa ve sync seçildiyse `sync_style(...)`
   çağır.
7. Provider remote style desteklemiyorsa `remote_state=UNSUPPORTED` veya
   `LOCAL_ONLY` bırak.

Assign flow:

1. Style valid mi kontrol et.
2. Style content catalog için okunabilir mi kontrol et.
3. Layer/provider eşitliği şartı arama.
4. Workspace eşitliği şartı arama.
5. Aynı layer için aktif default varsa pasifleştir veya explicit overwrite iste.
6. Assignment kaydını oluştur/güncelle.
7. Eğer layer provider'ı remote assignment destekliyorsa opsiyonel olarak remote
   assign denenebilir; bu catalog assignment'ın ön koşulu değildir.

Delete flow:

1. Style aktif layer assignment'larında kullanılıyor mu kontrol et.
2. Kullanılıyorsa default davranış delete engellemek olmalı.
3. Force delete ayrı admin action olabilir.
4. Remote provider destekliyorsa delete remote denenir.
5. Remote delete başarısızsa Django kaydı silinmemeli.
6. Provider remote style desteklemiyorsa sadece Django dependency kuralları
   uygulanır.

## Faz 6: Admin Panel

Admin kaynakları:

- `GeodataEngine`
- `Workspace`
- `Store`
- `Layer`
- `Style`

`LayerStyleAssignment` ayrı ana menü kaynağı olmamalıdır.

StyleAdmin:

- Style listesi gösterir.
- Style create/upload/edit/delete akışını yönetir.
- Validation state ve remote state badge gösterir.
- Sync action sadece provider destekliyorsa çalışır.
- Provider desteklemiyorsa kullanıcıya açık "remote style sync unsupported"
  mesajı gösterir.

LayerAdmin:

- Layer detail içinde `Styles` bölümü olmalıdır.
- Sistem kaydındaki style'lar buradan layer'a eklenebilmelidir.
- Dropdown tüm uygun style'ları gösterebilir:
  - valid style
  - active/deleted olmayan style
  - format catalog için anlamlı style
- Aynı provider/workspace zorunlu filtre olmamalıdır.
- UI style'ın owner provider'ını göstermelidir:
  - örnek: `GeoServer Main / mobility / roads-default`
  - örnek: `Local Martin Provider / global / simple-line`

Admin ordering:

```python
_GEODATA_PROVIDER_ADMIN_ORDER = {
    "GeodataEngine": 0,
    "Workspace": 1,
    "Store": 2,
    "Layer": 3,
    "Style": 4,
}
```

## Faz 7: Query Services

Mevcut placeholder:

- `tosca_api/apps/geodata_providers/services/queries/style_query_service.py`

Gerçek hale getirilecek methodlar:

```python
class StyleQueryService:
    @classmethod
    def list_styles(cls, *, provider_id=None, workspace_id=None, valid_only=False) -> list[dict]:
        ...

    @classmethod
    def get_style_detail(cls, *, style_id) -> dict:
        ...

    @classmethod
    def get_style_content(cls, *, style_id) -> str | dict:
        ...

    @classmethod
    def get_layer_default_style(cls, *, layer_id) -> dict | None:
        ...

    @classmethod
    def list_styles_for_layer_assignment(cls, *, layer_id) -> list[dict]:
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
    "name": "Catalog GeoServer",
    "engine_type": "geoserver"
  },
  "validation_state": "VALID",
  "remote_state": "SYNCED",
  "content_hash": "sha256",
  "updated_at": "2026-04-23T10:00:00Z"
}
```

## Faz 8: Catalog API Entegrasyonu

Provider fazı bitmeden catalog style endpoint'i tamamlanmış sayılmamalıdır.

Layer info response içindeki:

```json
"defaultStyle": {
  "name": "default",
  "href": "https://api.example.com/api/catalog/v1/styles/default"
}
```

şu kaynaktan gelmelidir:

1. Layer'a atanmış aktif default style assignment
2. Yoksa layer/provider default fallback
3. Yoksa `"default"` fallback

Önemli:

- Layer provider'ı Martin olsa bile default style GeoServer owner'lı olabilir.
- Catalog response style'ın owner provider bilgisini taşımalıdır.
- Catalog response remote provider'a gitmeden Django'daki style content'i
  kullanabilmelidir.

Önerilen catalog style endpoint:

- `GET /api/catalog/v1/styles/{style_id}`

Neden `style_id`?

- Style name tek başına global sistemde belirsizdir.
- Cross-provider assignment olduğunda `workspace_name/style_name` da yeterli
  olmayabilir.
- Layer info response zaten `href` üreteceği için frontend id bilmek zorunda
  değildir.

Backward compatibility:

- `GET /api/catalog/v1/styles/{style_name}` geçici olarak kalabilir.
- Ancak yeni `defaultStyle.href` style id bazlı endpoint üretmelidir.

Style detail davranışı:

- `format=mbstyle` ise raw MBStyle JSON döner.
- `format=sld` ise frontend MBStyle beklediği endpointte `406 Not Acceptable`
  dönmelidir.
- İleride SLD için ayrı raw endpoint eklenebilir.

Catalog testleri:

- Martin layer + GeoServer style assignment layer info içinde döner.
- Layer info style href'i id bazlı endpoint üretir.
- MBStyle detail raw JSON döner.
- SLD style frontend endpointinde `406` döner.
- Missing style `404`.
- Deleted/invalid style leak etmez.

## Faz 9: API / Console Durumu

Bu fazda dışarı public `geodata_providers` write API açılmayacak.

Kurallar:

- Style write işlemleri admin panel ve provider command service ile yapılacak.
- Swagger UI sadece catalog read endpointlerini göstermeye devam edecek.
- Provider API prefixi `/api/v1/providers/provider/` olacak.
- Catalog endpointleri sadece read surface olacak.

## Faz 10: Test Planı

Unit tests:

- `test_style_models.py`
- `test_style_validation_service.py`
- `test_style_service.py`
- `test_style_query_service.py`

Admin tests:

- `StyleUploadForm` valid SLD
- `StyleUploadForm` invalid SLD
- `StyleUploadForm` valid MBStyle
- `StyleUploadForm` invalid MBStyle
- `StyleAdmin.save_model` service çağırır
- `StyleAdmin.delete_model` dependency ve remote kurallarına uyar
- `LayerAdmin` style assignment selector kullanır
- Cross-provider style assignment admin formda kabul edilir

GeoServer client tests:

- SLD upload doğru endpoint/content-type
- MBStyle upload doğru endpoint/content-type
- workspace style endpoint doğru kurulur
- global style endpoint doğru kurulur
- delete style doğru endpoint

Catalog tests:

- `test_v1_api.py` içinde style domain bağlı testler
- ayrı `test_v1_style_api.py` açmak daha temiz olur
- Martin layer + GeoServer style scenario test edilmeli

## Baştan Development Sırası

### Step 1: Mevcut Faz 1 Implementasyonunu Revize Et

- Yeşil tikleri tamamlandı sayma.
- `LayerStyle` ayrı admin kaynak olmaktan çıkar.
- Cross-provider assignment'ı engelleyen validation kaldırılır.
- Workspace/provider eşitliği sadece `Style` owner scope'u için kalır.
- Remote state choices provider capability'ye göre güncellenir.
- Testler cross-provider assignment senaryosunu kapsar.

### Step 2: Style Validation Service

- SLD validation
- MBStyle validation
- Valid/invalid fixture testleri

### Step 3: Provider Capability Katmanı

- Provider style sync destekliyor mu?
- GeoServer true
- Martin false
- Unsupported remote sync state testleri

### Step 4: GeoServer Style Client

- Upload
- Delete
- Get/list
- Verify

### Step 5: StyleService

- Create
- Validate
- Sync
- Delete
- Assign to layer
- Cross-provider assignment

### Step 6: Admin

- Style ana kaynak
- Layer detail style assignment bölümü
- `LayerStyleAssignment` ayrı admin menüden kaldırılır
- Provider desteklemeyen sync action mesajları

### Step 7: Query Services

- Style list/detail/content
- Layer default style
- Layer assignment candidates

### Step 8: Catalog API

- Layer info default style DB assignment üstünden gelir.
- Martin layer + GeoServer style response desteklenir.
- Style detail id bazlı endpoint kullanılır.
- MBStyle JSON döner, SLD frontend endpointinde `406` döner.

### Step 9: Swagger ve Doküman

- Sadece catalog read endpointleri public dokümante edilir.
- Internal provider write yüzeyi public Swagger'a girmez.

## Kabul Kriterleri

Provider tarafı tamam sayılmak için:

- Admin panelde `Style` ana kaynak olarak görünür.
- Admin panelde ayrı bir `Layer Style` ana kaynağı görünmez.
- Layer detail içinde style selector/assignment bölümü vardır.
- Admin'den `.sld`, `.json`, `.mbstyle` upload edilebilir.
- Invalid SLD/MBStyle kaydedilmeden reddedilir.
- Valid style Django DB'ye kaydedilir.
- GeoServer provider için style sync ve verify çalışır.
- Martin provider için remote sync unsupported olarak açık yönetilir.
- Martin layer'a GeoServer owner'lı style atanabilir.
- Unit/admin/client/service testleri geçer.

Catalog tarafı tamam sayılmak için:

- `LayerInfo` default style bilgisini DB assignment ilişkisinden üretir.
- Martin layer + GeoServer style bilgisi catalog response içinde döner.
- `defaultStyle.href` style id bazlı doğru endpoint'e gider.
- MBStyle style detail frontend'in beklediği JSON'u döner.
- SLD style frontend MBStyle endpointinde yanlışlıkla JSON gibi dönmez.
- Missing/invalid/deleted style leak etmez.

## Açık Kararlar

1. Teknik model adı `LayerStyle` olarak mı kalacak, yoksa
   `LayerStyleAssignment` migration ile rename mi edilecek?

Öneri:

- Henüz branch/commit erken aşamadaysa `LayerStyleAssignment` daha açık isimdir.

2. Remote state `UPLOADED` mı `SYNCED` mı olmalı?

Öneri:

- Provider bağımsız dil için `SYNCED` daha doğru.

3. Sync desteklemeyen provider için state ne olmalı?

Öneri:

- `UNSUPPORTED`.

4. Catalog style endpoint name mi id mi kullanmalı?

Öneri:

- Yeni href id bazlı olmalı: `/api/catalog/v1/styles/{style_id}`.

5. SLD style'lar catalog v1 frontend endpointinde `404` mı `406` mı dönmeli?

Öneri:

- `406 Not Acceptable`.

6. Style content DB'de mi file storage'da mı tutulmalı?

Öneri:

- İlk fazda DB TextField. Büyük style dosyaları problem olursa sonra storage.

## Önerilen Commit Sırası

1. `refactor(geodata): revise style assignment domain`
2. `feat(geodata): add style validation service`
3. `feat(geodata): add provider style capabilities`
4. `feat(geodata): add geoserver style client methods`
5. `feat(geodata): add style command service`
6. `feat(geodata): add style admin workflows`
7. `feat(geodata): implement style query service`
8. `feat(catalog): serve v1 style details from provider styles`

## Uygulama Notu: 2026-04-23 Faz 1 Revizyon Gerekli

Önceki implementasyon şu dosyalara dokundu:

- `tosca_api/apps/geodata_providers/models.py`
- `tosca_api/apps/geodata_providers/admin.py`
- `tosca_api/apps/geodata_providers/migrations/0002_style_layerstyle.py`
- `tosca_api/apps/geodata_providers/tests/test_style_models.py`

Bu implementasyon yeni hikayeye göre tamamlandı sayılmıyor.

Revize edilecekler:

- `LayerStyle` ayrı admin kaynak olarak görünmemeli.
- Assignment provider/workspace eşitliği zorunlu tutmamalı.
- Martin layer + GeoServer style test senaryosu eklenmeli.
- Remote state provider capability'ye göre güncellenmeli.
- Development sırası bu dokümandaki "Baştan Development Sırası"na göre devam
  etmeli.
