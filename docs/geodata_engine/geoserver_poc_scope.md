# GeoServer POC Scope

## Included
- Engine-aware services with GeoServer adapter support
- Idempotent create/publish/unpublish/sync/validate behavior
- Standard CRUD endpoints for engine/workspace/store/layer
- Operations endpoints separated from CRUD behavior
- Public-read layer support via `is_public`

## Excluded (Later Phases)
- Martin / pg_tileserv concrete adapters
- Celery async workflows
- Advanced RBAC and organization ownership model
- Non-GeoServer sync adapters

## Operational Notes
- `/api/geodata/` remains as a backward-compatible alias to `/api/geoengine/` for now.
- Existing `geoserver/client.py` is reused and wrapped by adapter layer.
