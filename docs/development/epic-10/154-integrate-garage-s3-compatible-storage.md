> Development note: Run Django, uv, pytest, and migration checks inside the
> `tosca-django-api` container, not on the host.

# #154 SEF-P10 - Integrate Garage S3-Compatible Storage

## Purpose

Uploaded geospatial source files need durable object storage that works outside
the local container filesystem. Garage provides an S3-compatible backend for
this.

## Scope

Add configuration and storage integration for upload-backed provider workflows.

Likely touch points:

- `tosca_api/settings/base.py`
- environment files and README docs
- upload/admin code in `geodata_providers`
- any file fields or storage helpers used by geospatial uploads
- tests for storage path generation/configuration

## Requirements

- Add settings for S3-compatible storage:
  - endpoint URL
  - bucket
  - access key
  - secret key
  - region if required by the library
  - public/private URL behavior
- Keep local development usable without Garage by preserving filesystem storage
  fallback where appropriate.
- Store uploaded source files in object storage when enabled.
- Do not expose secrets in API responses or docs.
- Document required `.env` variables.
- Keep production Nginx/media behavior in mind: object storage is for durable
  uploaded sources, not necessarily every static/media serving path.

## Acceptance Criteria

- App starts with storage disabled/fallback settings.
- App starts with Garage/S3 settings enabled.
- Upload workflow stores files through Django storage abstraction.
- Tests cover storage key/path generation or configured storage backend.
- README or development docs describe required env vars.

## Notes For Agent

Prefer Django's storage abstraction over direct boto calls in admin/views.
Direct S3 client usage should be isolated behind a small service only if the
storage API is insufficient.
