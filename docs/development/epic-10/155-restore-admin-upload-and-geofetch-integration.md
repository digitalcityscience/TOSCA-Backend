> Development note: Run Django, uv, pytest, and migration checks inside the
> `tosca-django-api` container, not on the host.

# #155 SEF-P10 - Restore Admin Upload and GeoFetch Integration

## Purpose

Admin upload and GeoFetch ingestion should work with the provider domain model
and the new storage direction. Uploaded files should become provider resources
through services, not one-off admin logic.

## Scope

Restore or repair admin upload flows and connect them to GeoFetch where the repo
already expects that workflow.

Likely touch points:

- `tosca_api/apps/geodata_providers/admin.py`
- `tosca_api/apps/geodata_providers/admin_forms.py`
- `tosca_api/apps/geodata_providers/admin_views/`
- `tosca_api/apps/geodata_providers/services/commands/layer_service.py`
- `tosca_api/apps/geodata_providers/services/commands/store_service.py`
- templates under `templates/admin/geodata_providers/`
- upload/storage tests

## Requirements

- Admin users can upload supported geospatial files again.
- Supported file types should align with existing store support:
  GeoPackage, GeoJSON, Shapefile/directory, and GeoTIFF if currently supported.
- Uploaded files should be stored via the configured Django storage backend.
- GeoFetch/import processing should create or update PostGIS/source resources in
  a service-controlled way.
- Publishing to GeoServer should still follow remote-first command service
  semantics.
- Errors from upload, GeoFetch, database import, or GeoServer publish should be
  shown clearly in admin.

## Acceptance Criteria

- Admin upload form renders and submits successfully.
- A valid upload reaches storage and the expected import/publish service.
- Failed upload/import/publish leaves no misleading synced catalog resource.
- Tests cover at least the admin form/service handoff and failure handling.

## Notes For Agent

First identify what "GeoFetch" means in the current repo before implementing a
new integration. If code is missing, document the missing boundary and restore
the admin upload path up to a clearly named service interface.
