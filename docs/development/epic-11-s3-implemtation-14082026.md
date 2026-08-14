# Epic 11 S3 Implementation Log

**Tarih:** 2026-08-14  
**Branch:** `epic-11-s3-production-media`  
**Başlangıç branch'i:** `main`  
**Durum:** Storage foundation, MediaAsset entegrasyonu ve local Garage S3 stack'i tamamlandı. Public derivative üretiminin uygulama akışına bağlanması sonraki aşamadır.

> Bu dosyanın adı, takip kolaylığı için istenen biçimde bırakılmıştır: `epic-11-s3-implemtation-14082026.md`.

## 1. Çalışmanın amacı

Epic 11 kapsamında production media dosyalarını tek Docker host'una bağlı filesystem volume modelinden Garage/S3-compatible object storage modeline taşımak için ilk güvenli entegrasyon adımları gerçekleştirildi.

Temel hedefler:

- Django storage abstraction üzerinden filesystem veya S3 backend seçebilmek.
- Garage, MinIO, AWS S3 veya başka bir S3-compatible provider'a uygulama kodunu bağlayabilmek.
- View, serializer ve model katmanlarına doğrudan boto3 çağrıları eklememek.
- Local development ve test davranışını filesystem üzerinde korumak; development Compose profili ise Garage S3'ü varsayılan olarak kullanır.
- EditorJS upload dosyalarını DB metadata'sı ile takip etmek.
- S3/Garage üzerinde pahalı recursive storage scan işlemini media library'den kaldırmak.

## 2. İncelenen planlama dokümanları

İki ana doküman incelendi:

- `docs/development/epic-11_s3-production-media-roadmap.md`
- `docs/development/epic-11-organization.md`

Roadmap genel olarak doğru yönde bulundu. Ancak uygulama sırasında şu düzeltmeler gerekli görüldü:

1. `MediaAsset`, Issue 12 için yalnızca soft dependency değil, pratikte ön koşul olmalıdır. S3 üzerinde her media library isteğinde storage listesi ve PIL okumaları yapmak pahalıdır.
2. Private originals ve public derivatives aynı `default` storage içinde bırakılmamalıdır. İlerleyen aşamada ayrı storage alias'ları veya ayrı storage sınıfları gerekecektir.
3. `AWS_QUERYSTRING_AUTH=false` yalnızca public derivative storage için güvenli olabilir. Private originals için signed URL veya kontrollü erişim modeli gerekir.
4. `epic-11-organization.md` içindeki bazı referanslar eski dosya adına işaret ediyor:
   `docs/development/s3-production-media-roadmap.md`
   Güncel dosya adı:
   `docs/development/epic-11_s3-production-media-roadmap.md`

## 3. Branch hazırlığı

Yeni branch `main` üzerindeki mevcut commit'ten oluşturuldu:

```text
epic-11-s3-production-media
```

Başlangıçta çalışma ağacında bulunan kullanıcı değişiklikleri korunmuştur:

```text
M  docker/geoserver_docker
?? .orchestry/
```

Bu iki değişikliğe dokunulmadı ve commit'lere dahil edilmedi.

## 4. Commit 1: Configurable S3 storage foundation

```text
cab9fe6 feat(storage): add configurable S3 backend foundation
```

### Değiştirilen alanlar

- `pyproject.toml`
- `uv.lock`
- `tosca_api/settings/base.py`
- `docker-compose-prod.yml`
- `.env.example`
- `tosca_api/apps/core/tests/test_storage_settings.py`

### Eklenen dependency'ler

```text
django-storages[s3]
boto3
```

### Eklenen ayarlar

```dotenv
DJANGO_STORAGE_BACKEND=filesystem|s3
S3_ENDPOINT_URL=...
S3_REGION_NAME=...
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
S3_BUCKET_NAME=...
S3_PUBLIC_BUCKET_NAME=...
S3_ADDRESSING_STYLE=path|virtual|auto
S3_SIGNATURE_VERSION=s3v4
MEDIA_PUBLIC_BASE_URL=...
MEDIA_PRIVATE_PREFIX=...
MEDIA_DERIVATIVE_PREFIX=...
```

### Storage davranışı

Local development için Compose profili artık Garage S3'ü varsayılan seçer. Test settings ve doğrudan unit/regression test çalıştırmaları filesystem storage kullanmaya devam eder.

```python
STORAGES["default"] = {
    "BACKEND": "django.core.files.storage.FileSystemStorage",
}
```

S3 seçildiğinde Django storage backend kullanılır:

```python
STORAGES["default"] = {
    "BACKEND": "storages.backends.s3.S3Storage",
    "OPTIONS": {
        "bucket_name": S3_BUCKET_NAME,
        "endpoint_url": S3_ENDPOINT_URL,
        "region_name": S3_REGION_NAME,
        "addressing_style": S3_ADDRESSING_STYLE,
        "signature_version": S3_SIGNATURE_VERSION,
        "default_acl": None,
        "file_overwrite": False,
        "querystring_auth": True,
        "location": MEDIA_PRIVATE_PREFIX,
    },
}
```

Static files bilinçli olarak local `StaticFilesStorage` üzerinde bırakıldı. İlk release'te staticfiles migration'ı media migration'ına dahil edilmedi.

### Ayar validasyonları

- `DJANGO_STORAGE_BACKEND` yalnızca `filesystem` veya `s3` olabilir.
- S3 backend seçildiğinde `S3_BUCKET_NAME` zorunludur.
- Geçersiz backend değerleri `ImproperlyConfigured` ile reddedilir.

## 5. Commit 2: MediaAsset integration

```text
fa68176 feat(media): track uploaded assets in database
```

### Eklenen model

Dosya:

```text
tosca_api/apps/core/models.py
```

Model:

```text
MediaAsset
```

Alanlar:

- UUIDv7 primary key
- `storage_path`
- `original_name`
- `mime`
- `width`
- `height`
- `size`
- nullable `uploader` foreign key
- `created_at`
- `updated_at`

`storage_path` unique olacak şekilde tanımlandı. Böylece aynı object path için birden fazla metadata row oluşması engelleniyor.

Migration:

```text
tosca_api/apps/core/migrations/0001_mediaasset.py
```

### Upload davranışı

EditorJS upload akışında:

1. Görsel mevcut image policy ile doğrulanıyor.
2. Dosya `default_storage.save(...)` üzerinden storage'a yazılıyor.
3. Storage path, metadata ve boyut bilgileri `MediaAsset` row'u olarak kaydediliyor.
4. Mevcut EditorJS response contract korunuyor:

```json
{
  "success": 1,
  "file": {
    "url": "...",
    "mime": "image/png",
    "width": 240,
    "height": 240
  }
}
```

### Media library davranışı

Önceki davranış:

- `default_storage.listdir(...)` ile recursive scan.
- Her dosya için storage açılması.
- PIL ile image metadata okunması.
- S3/Garage üzerinde request başına çok sayıda object/list/read işlemi.

Yeni davranış:

```python
MediaAsset.objects.filter(
    storage_path__startswith="geocontext/editorjs/"
)[:limit]
```

Media library artık DB metadata'sını okuyor. Her request'te storage dosyalarını açıp PIL ile yeniden incelemiyor.

## 6. Test ve doğrulama sonuçları

Tüm development/container kontrolleri `tosca-django-api` container'ı içinde çalıştırıldı.

### Storage foundation testleri

```text
3 passed
```

Kontrol edilenler:

- Filesystem backend default davranışı.
- S3 backend config shape.
- Staticfiles backend'in local kalması.
- S3 bucket zorunluluğu.

### Derivative ve EditorJS testleri

```text
12 passed
```

Kontrol edilenler:

- Existing image derivative generation.
- Derivative cache davranışı.
- Filesystem storage regression.
- EditorJS image normalization.

### Gerçek DB-backed upload/media API testleri

İzole `test_tosca` PostGIS test database'i oluşturulduktan sonra:

```text
10 passed
```

Kontrol edilen public endpoint'ler:

- EditorJS upload-by-file.
- EditorJS upload-by-url.
- EditorJS media library.
- Authentication requirement.
- Invalid image rejection.
- Oversized remote download rejection.
- OpenAPI schema registration.

### Birleşik workflow testleri

```text
49 passed
```

Birlikte çalıştırılan alanlar:

- Upload API.
- Media library API.
- Image derivatives.
- EditorJS validation and normalization.
- OpenAPI endpoint documentation.

### Migration ve lint

```text
No changes detected in app 'core'
All checks passed!
```

Başarılı kontroller:

- `python manage.py makemigrations core --check --dry-run`
- `python manage.py migrate --check`
- Ruff check for changed files.
- `git diff --check`.
- Production Compose config validation.
- Django startup with real `storages.backends.s3.S3Storage` selection.

Test çalıştırma sırasında repository'nin test ayarları önceden oluşturulmuş `test_tosca` database'i beklediği için izole test database'i oluşturuldu. Application database'i silinmedi veya değiştirilmedi.

## 7. Şu anda tamamlanmayan işler

Önceki iki commit'in hedeflerine ek olarak local Garage stack'i de eklendi. Aşağıdaki hedefler hâlâ tamamlanmış değildir:

### Private originals / public derivatives

Şu anda storage foundation default storage'ı S3'e bağlayabiliyor. Local stack raw originals için private bucket ve public derivatives için ayrı bucket ile geliyor. Django `STORAGES["default"]` private bucket'ı, `STORAGES["media_public"]` ise query-string auth kapalı public bucket'ı temsil ediyor. Mevcut derivative üreticinin public alias'ı kullanması ve GeoStory response'larının bu politikaya göre tamamlanması sonraki adımdır.

Sonraki adımda:

- Private original storage.
- Public derivative storage.
- Derivative generator'ın public storage alias kullanması.
- GeoStory serializer'larının raw original yerine public derivative URL döndürmesi.
- EditorJS response'larına derivative metadata eklenmesi.
- `MEDIA_PUBLIC_BASE_URL` ile browser-facing URL üretiminin tamamlanması.

### Media migration command

Henüz şu command yazılmadı:

```text
migrate_media_to_storage
```

Gerekli özellikler:

- `--dry-run`.
- Idempotent upload.
- Missing object detection.
- Object size verification.
- CSV/JSON report.
- Partial failure sonrası devam edebilme.
- Rollback planı.

### SSRF hardening

Mevcut upload-by-url akışında timeout, redirect limiti ve download size limiti bulunuyor. Ancak production için şu kontroller ayrıca tamamlanmalı:

- Loopback IP.
- RFC1918 private IPv4.
- Link-local adresler.
- IPv6 private/loopback/link-local adresler.
- DNS'in private IP'ye resolve olması.
- Redirect sonrası tekrar IP kontrolü.
- DNS rebinding riski.

### Throttling

EditorJS view'larında throttle sınıfları mevcut olsa da production rate değerlerinin ve endpoint-specific scope'ların ayrıca tamamlanması gerekiyor.

## 8. Local Garage stack'i

Local development Compose dosyasına sabit ve multi-arch `dxflrs/garage:v2.3.0` image'ı eklendi. Image üzerinde `platform: linux/amd64` zorlaması yoktur; Docker Desktop Apple Silicon ve Linux host kendi uygun image mimarisini seçer.

Garage metadata ve object data ayrı named volume'larda tutulur:

- `garage_meta:/var/lib/garage/meta`
- `garage_data:/var/lib/garage/data`

SQLite metadata engine, local single-node development için metadata'nın mimariler arasında taşınabilir ve yeniden başlatma sonrası korunabilir olmasını sağlar. Django static files bu stack'e dahil edilmez ve local `StaticFilesStorage` üzerinde kalır.

`garage-bootstrap` servisi idempotent olarak şunları yapar:

1. Garage single-node layout'unun hazır olmasını bekler.
2. `tosca-media-private` ve `tosca-media-public` bucket'larını oluşturur veya mevcut olanları kullanır.
3. Django service access key'e her iki bucket için read/write/owner izni verir.

Service credential Garage içindir. Keycloak veya Django application user hesabı değildir.

Örnek local değişkenler:

```dotenv
DJANGO_STORAGE_BACKEND=s3
S3_ENDPOINT_URL=http://garage:3900
S3_REGION_NAME=garage
S3_ACCESS_KEY_ID=GKtosca-local-django
S3_SECRET_ACCESS_KEY=tosca-local-django-secret-change-me
S3_BUCKET_NAME=tosca-media-private
S3_PUBLIC_BUCKET_NAME=tosca-media-public
S3_ADDRESSING_STYLE=path
```

Gerçek local acceptance akışı:

```sh
ENV_FILE=.env.dev ./scripts/verify_garage_s3.sh
```

Bu script Django `default_storage` üzerinden byte upload eder, Garage restart sonrasında aynı object'i okur ve SHA-256 eşleşmesini kontrol eder.

## 9. Önerilen sonraki uygulama sırası

```text
10. Storage guardrails
11. Configurable S3 backend          tamamlandı
48. MediaAsset model                 tamamlandı
12. Upload surfaces                  foundation tamamlandı, S3 staging doğrulaması bekliyor
13. Private originals/public derivatives
14. Existing media migration command
15. Static files decision
16. Storage operations and lifecycle
```

Issue 48'in Issue 12'den önce gelmesi önerilir. S3 üzerinde DB tracking olmadan media library ve orphan cleanup operasyonları gereksiz pahalı ve güvenilmez olur.

## 10. Branch'in güncel durumu

Son commit:

```text
838cb94 feat(storage): add local Garage S3 stack
```

Önceki storage commit'i:

```text
cab9fe6 feat(storage): add configurable S3 backend foundation
```

Branch:

```text
epic-11-s3-production-media
```

İlgisiz mevcut çalışma ağacı değişiklikleri korunuyor:

```text
M  docker/geoserver_docker
?? .orchestry/
```

Bu değişiklikler Epic 11 S3 commit'lerine dahil edilmemiştir.

## 11. Son doğrulama ve requirement traceability

Bu bölüm, dokümanda yazan her ana gereksinimi doğrudan bir kontrol ve gözlenen sonuçla eşleştirir. Toplam test sayısı tek başına yeterli kabul edilmemiştir.

| Gereksinim / değişen çıktı | Doğrudan kontrol | Gözlenen sonuç |
|---|---|---|
| Local development filesystem backend kullanabilmeli | `test_storage_settings.py::test_filesystem_is_the_default_shape_for_local_development` | Filesystem backend ve local staticfiles backend doğru seçildi. Passed. |
| S3/Garage backend settings ile seçilebilmeli | Container içinde `DJANGO_STORAGE_BACKEND=s3` ile Django startup smoke testi | `storages.backends.s3.S3Storage`, Garage endpoint, bucket, path-style ve S3v4 options yüklendi. Passed. |
| S3 bucket olmadan unsafe startup engellenmeli | `test_storage_settings.py::test_s3_requires_a_bucket_name` | `ImproperlyConfigured` üretildi. Passed. |
| Packaging dependency'leri lockfile'a dahil olmalı | Container içinde `uv sync --frozen` | `boto3`, `botocore`, `django-storages`, `s3transfer` kuruldu. Passed. |
| Production Compose S3 environment değerlerini iletmeli | `ENV_FILE=.env.example docker compose -f docker-compose-prod.yml config --quiet` | Compose config başarıyla oluşturuldu. Passed. |
| EditorJS file upload public response contract'ını korumalı | Gerçek PostGIS-backed `test_upload_by_file_stores_original_bytes_and_returns_editorjs_contract` | HTTP 200, `success=1`, URL/mime/width/height ve original bytes doğrulandı. Passed. |
| Upload metadata DB'ye yazılmalı | Aynı public upload testi içinde `MediaAsset.objects.get(...)` | `storage_path`, `original_name`, `mime`, width, height ve byte size doğrulandı. Passed. |
| EditorJS URL upload çalışmalı | Gerçek `test_upload_by_url_downloads_rehosts_and_preserves_bytes` | HTTP 200, EditorJS contract ve rehost edilen bytes doğrulandı. Passed. |
| Invalid image ve büyük remote download reddedilmeli | `test_upload_by_file_rejects_invalid_image`, `test_upload_by_url_rejects_oversized_download` | HTTP 400 ve failure contract doğrulandı. Passed. |
| Upload endpoint'leri authentication istemeli | `test_upload_endpoints_require_authentication` | Yetkisiz istekler 401/403 döndürdü. Passed. |
| Media library storage scan yapmamalı | `test_media_library_lists_previous_editorjs_uploads` içinde `default_storage.listdir/open` fail-fast monkeypatch'leri | Library response DB'den döndü; storage scan veya object open çağrılmadı. Passed. |
| Media library response contract'ı korunmalı | Aynı public GET testi | `results`, name, mime, dimensions ve URL doğrulandı. Passed. |
| Derivative workflow bozulmamalı | `test_image_derivatives.py` ve EditorJS/core testleri | Derivative generation, cache, EXIF orientation ve normalization geçti. Passed. |
| Migration model ile uyumlu olmalı | `manage.py makemigrations core --check --dry-run` | `No changes detected in app 'core'`. Passed. |
| Applied migration state eksiksiz olmalı | `manage.py migrate --check` | Pending migration bulunmadı. Passed. |
| Changed Python code lint edilebilir olmalı | Container içinde Ruff | `All checks passed!`. Passed. |
| Local Garage Compose stack'i ve persistent volumes | `ENV_FILE=.env.dev ./scripts/verify_garage_s3.sh` | 52 filesystem regression testi, Compose config ve shell syntax kontrolleri geçti. Live Garage akışı private/public alias upload-read ve restart persistence doğruladı. Passed. |
| Private ve public Garage bucket alias'ları | `scripts/garage_e2e.py` üzerinden `default_storage` ve `storages["media_public"]` | Her iki bucket'a upload edildi, Garage restart sonrasında her iki object byte-for-byte okundu. Passed. |
| Implementation documentation takip edilebilir olmalı | Dosya existence, line count, opening metadata ve commit doğrulaması | İstenen dosya mevcut, 421+ satır, branch/commit/test/limitation bölümleri içeriyor ve `d6ec700` ile commit edildi. Passed. |

### Kapsam dışı veya dış bağımlılığa bağlı kontrol

Production Garage/S3 upload testi production endpoint ve credentials gerektirir. Local acceptance script'i ise gerçek local Garage endpoint'i üzerinde çalıştırıldı.

1. Django gerçek `storages.backends.s3.S3Storage` ile başlatıldı ve Garage-compatible options üretildi.
2. Gerçek local Garage service'e Django `default_storage` ve `storages["media_public"]` ile object upload edildi, bytes okundu ve SHA-256 doğrulandı.
3. Garage container restart edildi; named volumes korunurken private ve public object'ler yeniden okunarak persistence doğrulandı.

Bu nedenle mevcut sonuç **local Garage object I/O and restart persistence verified** seviyesindedir. Production Garage cluster replication, TLS, lifecycle ve public CDN davranışı bu local stack'in kapsamı dışındadır.

## 12. Grill sonrası kapatılan eksikler (14.08.2026)

Implementation grill oturumunda kodun dokümanla çeliştiği ve production öncesi blocker sayılan noktalar tespit edildi ve kapatıldı. Bu bölüm önceki bölümlerdeki "sonraki adım" ifadelerini kısmen geçersiz kılar.

### Public URL modeli (önceki §7 "sonraki adım" idi)

Sorun: EditorJS upload akışı browser-facing URL'i `default_storage.url()` üzerinden üretiyordu. `default` storage private bucket + `querystring_auth=True` olduğu için üretilen URL **imzalı ve süreli** oluyordu; yayınlanmış GeoStory içine gömülen görseller signed URL expiry sonrasında kırılırdı.

Yapılan:

- `_store_validated_upload`, `_absolute_url` ve size okuması artık `storages["media_public"]` alias'ını kullanıyor. Public S3 bucket `querystring_auth=False` ile imzasız URL döndürüyor.
- `build_storage_config` filesystem modunda da `media_public` alias'ı (FileSystemStorage) üretiyor; böylece alias her backend'de resolve oluyor ve test/local davranışı korunuyor.
- S3 backend'inde artık `S3_PUBLIC_BUCKET_NAME` **zorunludur** (eksikse `ImproperlyConfigured`). §11 traceability'de "aspirasyonel" görünen `media_public` alias iddiası artık settings katmanında gerçek ve testli.

> Not: Derivative generator ve GeoStory serializer'ının public alias'ı kullanması hâlâ ayrı bir iştir (Issue 13). Bu commit yalnızca EditorJS inline upload URL'lerini imzasız/kalıcı hale getirir.

### SSRF hardening (önceki §7'de "production için tamamlanmalı" idi)

`upload-by-url` artık download öncesinde ve redirect sonrasında hedef host'u doğruluyor:

- IP literal'leri DNS'siz kontrol edilir; hostname'ler `getaddrinfo` ile resolve edilip **tüm** dönen adresler kontrol edilir.
- Reddedilenler: loopback, RFC1918 private, link-local (169.254/16, cloud metadata dahil), reserved, multicast, unspecified — IPv4 ve IPv6.
- Redirect sonrası final URL tekrar doğrulanır (public→internal redirect saldırısı kapatıldı).
- Kalan bilinen sınır: DNS rebinding TOCTOU (resolve ile connect arası). Pinned-connection adapter kapsam dışı bırakıldı; endpoint authenticated-only olduğu için bu oransal bir mitigasyon.

### Throttling (önceki §7'de "production rate değerleri tamamlanmalı" idi)

- Çıplak `UserRateThrottle` yerine scope'lu sınıflar: `editorjs_upload` ve `editorjs_media`.
- `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]` env-configurable defaults ile eklendi (`THROTTLE_EDITORJS_UPLOAD=30/minute`, `THROTTLE_EDITORJS_MEDIA=120/minute`). `.env.example` ve prod compose güncellendi.

### Bilinçli kapsam dışı bırakılanlar

- **Org/tenant scoping**: Media library hâlâ tüm asset'leri listeler. CLAUDE.md POC'u superadmin-only tanımladığı ve fine-grained RBAC'i out-of-scope bıraktığı için bu commit'te org filtresi eklenmedi; multi-tenant'a geçişte `MediaAsset` scope alanı gerekecek.
- **Orphan/lifecycle cleanup** ve `migrate_media_to_storage` command'ı: değişmedi, Issue 14 kapsamındadır.

### Test ve kontroller

- `test_storage_settings.py`: filesystem `media_public` alias, s3 public bucket zorunluluğu, private bucket'ın imzalı kalması eklendi.
- `test_editorjs_uploads.py`: IP literal blocking (metadata/loopback/RFC1918/IPv6), private-resolving hostname blocking, redirect-to-internal blocking, throttle scope wiring testleri eklendi.
- `tosca_api/apps/geocontext` ve `tosca_api/apps/core`: 203 passed (ilgili tüm testler). İki başarısızlık — `test_database_settings.py` CONN_MAX_AGE — bu değişiklikle ilgisizdir ve çalıştırıldığı container'ın `CONN_MAX_AGE=0` env'inden kaynaklanır.
- Ruff temiz; `makemigrations --check` model değişikliği yok (davranış değişikliği tamamen storage/view/settings katmanındadır, yeni migration gerekmez).
