# Epic 11 S3 Implementation Log

**Tarih:** 2026-08-14  
**Branch:** `epic-11-s3-production-media`  
**Başlangıç branch'i:** `main`  
**Durum:** Storage foundation ve MediaAsset entegrasyonu tamamlandı. Private original/public derivative ayrımı henüz sonraki aşamadır.

> Bu dosyanın adı, takip kolaylığı için istenen biçimde bırakılmıştır: `epic-11-s3-implemtation-14082026.md`.

## 1. Çalışmanın amacı

Epic 11 kapsamında production media dosyalarını tek Docker host'una bağlı filesystem volume modelinden Garage/S3-compatible object storage modeline taşımak için ilk güvenli entegrasyon adımları gerçekleştirildi.

Temel hedefler:

- Django storage abstraction üzerinden filesystem veya S3 backend seçebilmek.
- Garage, MinIO, AWS S3 veya başka bir S3-compatible provider'a uygulama kodunu bağlayabilmek.
- View, serializer ve model katmanlarına doğrudan boto3 çağrıları eklememek.
- Local development ve test davranışını filesystem üzerinde korumak.
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

Filesystem varsayılan olarak korunur:

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

Bu iki commit aşağıdaki hedefleri henüz tamamlamıyor:

### Private originals / public derivatives

Şu anda storage foundation default storage'ı S3'e bağlayabiliyor. Ancak raw originals ve derivatives'ın ayrı public/private politikaları henüz tamamlanmış değildir.

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

## 8. Önerilen sonraki uygulama sırası

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

## 9. Branch'in güncel durumu

Son commit:

```text
fa68176 feat(media): track uploaded assets in database
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
