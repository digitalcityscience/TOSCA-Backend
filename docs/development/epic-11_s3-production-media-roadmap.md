# S3-Compatible Production Media Roadmap

Date: 2026-07-06

Purpose: move TOSCA media handling from single-host Docker volumes to a production-grade S3-compatible storage model while closing the production issues found in the media/upload review.

This roadmap assumes Garage, MinIO, AWS S3, or another S3-compatible service. Garage is the expected self-hosted target from the existing Epic 10 notes, but the Django implementation should stay provider-neutral.

## Current State

The current production compose setup stores media on a local Docker volume:

```text
Django /app/media        -> media_files  -> Nginx /usr/share/nginx/media
Django /app/staticfiles  -> static_files -> Nginx /usr/share/nginx/staticfiles
```

This works for one Docker host. It is not enough for multi-host deployment, rolling releases, multiple Django replicas, or disaster recovery.

Current media surfaces:

- GeoStory `hero_image` via `ImageField`.
- EditorJS upload-by-file endpoint using `default_storage.save(...)`.
- EditorJS upload-by-url endpoint that downloads, validates, and rehosts an image.
- EditorJS media library endpoint that lists files from `default_storage`.
- On-demand image derivative endpoint that reads originals from `default_storage` and writes cached derivatives back to storage.
- Static files via `collectstatic` and Nginx shared volume.

## Production Issues To Close

### 1. Local filesystem media is not durable enough

Docker volumes are bound to the deployment host. A production deployment needs media to survive container replacement and be shared across application replicas.

Target outcome:

- Uploaded media is stored through S3-compatible object storage.
- Django instances can be horizontally scaled without copying media volumes.
- Backups, lifecycle rules, and retention are managed at the object-storage layer.

### 2. Public raw originals expose metadata

The current image policy intentionally preserves original bytes. That means originals can include EXIF/GPS metadata. Today `/media/` serves originals directly.

Target outcome:

- Raw originals are private by default.
- Public image delivery uses metadata-clean derivatives or explicitly public objects.
- The frontend should not need direct access to private original keys.

### 3. Media URL generation depends on proxy correctness

Code currently uses `request.build_absolute_uri(...)` and `default_storage.url(...)`. Behind reverse proxies, incorrect `X-Forwarded-Proto`/host handling can produce wrong `http://` URLs or wrong hosts.

Target outcome:

- Public media URLs are controlled by explicit settings such as `MEDIA_PUBLIC_BASE_URL`.
- Proxy headers are configured consistently for API URLs.
- API responses do not depend accidentally on internal container hostnames.

### 4. Upload-by-URL is SSRF-prone

The remote image upload endpoint accepts arbitrary HTTP(S) URLs and does not block private/internal addresses after DNS resolution or redirects.

Target outcome:

- Remote URL upload is either disabled in production or hardened before S3 rollout.
- SSRF tests cover loopback, private, link-local, IPv6, redirects, and DNS names resolving to private IPs.

### 5. Derivatives can become a write-amplification path

The derivative endpoint is public and creates cached derivative files on demand.

Target outcome:

- Derivative generation is throttled and bounded.
- Generated derivative keys are deterministic and cacheable.
- For important public assets, derivatives can be pre-generated during upload/save.

## Architecture Decision

Use Django's storage abstraction as the integration boundary.

Do:

- Configure S3 through Django `STORAGES`.
- Keep application code using `default_storage`, `ImageField`, and storage paths.
- Isolate any direct S3 client usage behind a small service only if Django storage cannot express the need.

Avoid:

- Direct boto calls inside views, serializers, models, or admin classes.
- Returning bucket names, access keys, internal endpoints, or private storage keys as public contracts.
- Coupling frontend payloads to one S3 provider.

## Proposed Storage Model

Use separate logical storage classes or aliases:

```text
default/private-originals
  Raw uploaded originals.
  Private bucket or private prefix.
  Used for GeoStory hero originals and EditorJS originals.

public-media
  Public, metadata-clean derivatives and safe assets.
  Can be a public bucket/prefix or served through CDN/reverse proxy.

staticfiles
  Collected static files.
  Can stay on WhiteNoise/Nginx initially; S3 staticfiles is optional.
```

Pragmatic v1 option:

- Use one bucket with prefixes:
  - `media/originals/...`
  - `media/derivatives/...`
  - `static/...` if staticfiles later move to S3
- Keep originals private.
- Make only derivatives public or serve derivatives through Django/CDN with controlled URLs.

## Environment Variables

Recommended settings:

```dotenv
DJANGO_STORAGE_BACKEND=filesystem|s3

S3_ENDPOINT_URL=https://garage.example.org
S3_REGION_NAME=garage
S3_ACCESS_KEY_ID=...
S3_SECRET_ACCESS_KEY=...
S3_BUCKET_NAME=tosca-media
S3_PUBLIC_BUCKET_NAME=tosca-public-media
S3_ADDRESSING_STYLE=path
S3_SIGNATURE_VERSION=s3v4

MEDIA_PUBLIC_BASE_URL=https://assets.example.org/media/
MEDIA_PRIVATE_PREFIX=media/originals/
MEDIA_DERIVATIVE_PREFIX=media/derivatives/

AWS_QUERYSTRING_AUTH=false
AWS_DEFAULT_ACL=None
AWS_S3_FILE_OVERWRITE=false
```

Notes:

- Garage commonly uses path-style addressing. Keep it configurable.
- Do not put secrets in API responses, generated docs, or frontend config.
- `MEDIA_PUBLIC_BASE_URL` should be the browser-facing URL, not the internal S3 endpoint.

## Dependency Plan

Add:

- `django-storages[s3]`
- `boto3`

Keep dependency additions explicit in `pyproject.toml` and `uv.lock`.

## Implementation Phases

### Phase 0: Guardrails Before S3

Goal: remove high-risk production behavior before introducing object storage.

Tasks:

- Fix SSRF protection for `EditorJSImageUploadByUrlView` or disable upload-by-url in production.
- Add DRF throttle scopes for:
  - EditorJS upload-by-file
  - EditorJS upload-by-url
  - media library listing
  - image derivative generation
- Add settings for route-specific throttle rates.
- Fix proxy header handling:
  - confirm whether TLS terminates at public Nginx or an upstream proxy
  - preserve trusted `X-Forwarded-Proto=https`
  - consider `USE_X_FORWARDED_HOST=True` only if the proxy chain is trusted
- Add a production system check that fails for unsafe media settings.

Acceptance criteria:

- SSRF tests pass.
- Upload and derivative endpoints have explicit throttles.
- Production startup warns or fails if media settings are unsafe.

### Phase 1: Configurable S3 Storage Backend

Goal: allow the app to boot with either filesystem or S3 storage.

Tasks:

- Add `django-storages[s3]` and `boto3`.
- Add storage settings in `tosca_api/settings/base.py`.
- Configure `STORAGES` conditionally from `DJANGO_STORAGE_BACKEND`.
- Keep filesystem storage as the default for local development and tests.
- Add a small custom storage class if needed to:
  - apply private/public prefixes
  - prevent overwrites
  - build public URLs from `MEDIA_PUBLIC_BASE_URL`
- Update `.env.example`, README, and production docs.

Suggested shape:

```python
if DJANGO_STORAGE_BACKEND == "s3":
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "bucket_name": S3_BUCKET_NAME,
                "endpoint_url": S3_ENDPOINT_URL,
                "region_name": S3_REGION_NAME,
                "access_key": S3_ACCESS_KEY_ID,
                "secret_key": S3_SECRET_ACCESS_KEY,
                "default_acl": None,
                "file_overwrite": False,
                "querystring_auth": True,
            },
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
```

Acceptance criteria:

- App starts with filesystem storage.
- App starts with S3 storage.
- Existing tests using `override_settings(MEDIA_ROOT=..., MEDIA_URL=...)` still pass or are adapted.
- New tests assert configured storage backend and URL generation behavior.

### Phase 2: Upload Surfaces Use S3 Safely

Goal: route all existing media writes through the configured storage backend without application-level S3 coupling.

Tasks:

- Verify GeoStory `hero_image` writes to S3 through `ImageField`.
- Verify EditorJS upload-by-file writes to S3 through `default_storage`.
- Verify EditorJS upload-by-url writes to S3 after SSRF hardening.
- Ensure returned URLs are public-facing and stable.
- Ensure `core/editorjs.py` can resolve stored image URLs back to storage keys when `MEDIA_PUBLIC_BASE_URL` is absolute.
- Add integration-style tests with mocked S3 storage, not live S3.

Acceptance criteria:

- Hero image upload returns a browser-usable URL.
- EditorJS upload returns the expected `{success: 1, file: {...}}` contract.
- Canonical EditorJS validation accepts configured public media URLs.
- No endpoint leaks S3 credentials or internal endpoints.

### Phase 3: Private Originals And Public Derivatives

Goal: prevent direct public exposure of raw originals.

Tasks:

- Decide whether v1 keeps originals private immediately or uses a transition window.
- Add derivative URL helpers to serializers so frontend does not need to parse storage paths from `/media/...` URLs.
- Update GeoStory payloads to include derivative URL sets or a preferred public image URL.
- Update EditorJS image normalization/response shape to include derivative-ready metadata.
- Change public frontend guidance: render derivatives by default, not originals.
- Optionally pre-generate derivatives during upload for hero images and EditorJS images.

Acceptance criteria:

- Public API payloads provide a derivative/public image URL.
- Frontend does not need raw original URLs for normal rendering.
- Raw original access can be disabled without breaking story/detail pages.
- EXIF-bearing original test proves derivative output is metadata-clean.

### Phase 4: Deployment And Migration

Goal: migrate existing local media to S3 without breaking stored references.

Tasks:

- Write a management command:
  - scans known media prefixes
  - uploads missing files to S3
  - verifies object existence and size
  - supports dry-run
  - emits a CSV/JSON report
- Keep storage keys unchanged where possible, e.g. `geostories/<uuid>/hero/...`.
- If introducing new prefixes such as `media/originals/...`, define a compatibility mapping.
- Run migration in staging first.
- Switch `DJANGO_STORAGE_BACKEND=s3` only after migration verification.
- Keep local `/media/` volume mounted read-only during rollback window if needed.

Acceptance criteria:

- Dry-run report lists all expected files.
- Migration report verifies all copied files.
- Staging API can read pre-existing GeoStory and EditorJS images from S3.
- Rollback plan is documented.

### Phase 5: Static Files Decision

Goal: decide whether static files stay on Nginx/WhiteNoise or move to S3/CDN.

Recommendation:

- Keep static files on Nginx/collectstatic for the first S3 media release.
- Do not combine staticfiles migration with media migration unless deployment needs it.
- Revisit S3/CDN staticfiles after media is stable.

Acceptance criteria:

- Media migration does not require changing staticfiles serving.
- Staticfiles remain covered by current `collectstatic` and Nginx behavior.

### Phase 6: Operations, Backup, And Lifecycle

Goal: make object storage operationally safe.

Tasks:

- Enable bucket backup/replication strategy.
- Define retention/lifecycle rules for:
  - originals
  - derivatives
  - temporary uploads
  - orphaned files
- Add object storage metrics and alerting:
  - request errors
  - latency
  - bucket size
  - object count
  - failed uploads
- Add OpenTelemetry spans around storage reads/writes at upload and derivative generation boundaries.
- Add a media cleanup command for orphaned files.

Acceptance criteria:

- Backups are documented and tested.
- Orphan cleanup has dry-run mode.
- Upload and derivative failures are visible in logs/metrics/traces.

## Code Touch Points

Expected files:

- `pyproject.toml`
- `uv.lock`
- `tosca_api/settings/base.py`
- `tosca_api/settings/production.py`
- `tosca_api/apps/geocontext/views.py`
- `tosca_api/apps/core/editorjs.py`
- `tosca_api/apps/core/image_derivatives.py`
- `tosca_api/apps/geostories/serializers.py`
- `tosca_api/apps/core/views.py`
- `.env.example`
- `README.md`
- `docker-compose-prod.yml`

Possible new files:

- `tosca_api/apps/core/storage.py`
- `tosca_api/apps/core/checks.py`
- `tosca_api/apps/core/management/commands/migrate_media_to_storage.py`
- `tosca_api/apps/core/tests/test_storage_settings.py`
- `tosca_api/apps/core/tests/test_media_url_contract.py`
- `tosca_api/apps/geocontext/tests/test_upload_by_url_ssrf.py`

## Testing Strategy

Unit tests:

- storage backend selection from settings
- public URL generation
- storage path normalization from public URLs
- SSRF URL rejection
- derivative cache key generation
- private original/public derivative policy

Integration-style tests:

- use Django `override_settings(STORAGES=...)` with temporary filesystem storage for most tests
- use `moto` or a fake storage class for S3 behavior if needed
- avoid requiring live Garage/S3 in the normal unit suite

Manual staging checks:

- upload GeoStory hero image
- upload EditorJS image by file
- upload EditorJS image by URL
- list EditorJS media library
- generate derivative
- reload story/detail page from SPA
- verify returned URLs are HTTPS and public-facing
- verify raw original policy

## Rollout Plan

1. Ship Phase 0 guardrails.
2. Add S3 settings behind `DJANGO_STORAGE_BACKEND=s3`, default still filesystem.
3. Deploy to staging with empty S3 bucket.
4. Upload new media in staging and verify browser rendering.
5. Run migration dry-run for existing staging media.
6. Copy existing media to S3 and verify.
7. Switch staging to S3.
8. Repeat dry-run and copy for production.
9. Switch production to S3 during a low-traffic window.
10. Keep local media volume available for rollback until verification is complete.
11. Disable public raw original serving or move public rendering to derivatives.

## Rollback Plan

Keep rollback simple for the first release:

- Do not delete local `/app/media` immediately after migration.
- Keep storage keys stable.
- If S3 access fails, set `DJANGO_STORAGE_BACKEND=filesystem` and redeploy.
- If new uploads happened during S3 mode, run reverse copy for those keys before rollback or accept a short maintenance window.

## Open Questions

- Will production use Garage behind the same public domain, a separate asset domain, or private internal-only endpoint plus CDN?
- Should raw originals ever be public, or should only derivatives be public?
- Should upload-by-url be available in production after SSRF hardening, or should editors only upload local files?
- Do we need presigned direct browser uploads for large geospatial files, or are Django-mediated uploads acceptable for v1?
- Are geospatial source uploads in scope for the first S3 release, or should image media ship first?

## Recommended First Milestone

The first milestone should not be "all media on S3." It should be:

- SSRF protection or production disable for upload-by-url.
- Storage backend switch via settings.
- GeoStory and EditorJS uploads working on S3 in staging.
- Public URL contract documented and tested.
- Derivatives still generated correctly from S3 originals.

That milestone removes the single-host media limitation while keeping the blast radius controlled.

