# Geodata Engine Architecture (POC)

## Scope
- Active engine for this phase: `GeoServer` only.
- Registry/adapter shape is future-ready for additional engines.

## Layering
- API: DRF viewsets and action endpoints
- Factory: engine client/sync resolver
- Clients: low-level engine REST wrappers

Flow:
`API -> EngineClientFactory -> Engine Client -> GeoServer REST`

## Model Principles
- Models are data-only.
- No external side effects in `save()` / `delete()`.

## API Structure
Base namespace: `/api/geoengine/`

CRUD:
- `/engines`
- `/workspaces`
- `/stores`
- `/layers`

Operations:
- `POST /engines/{id}/sync/`
- `POST /engines/{id}/validate/`
- `POST /layers/{id}/publish/`
- `POST /layers/{id}/unpublish/`
- `POST /layers/preview/`

## Permissions
- Public layer read (`list/retrieve`): `AllowAny`, filtered by `is_public=True` for anonymous users
- Authenticated CRUD: `IsAuthenticated`
- Operations: `IsAdminUser`

## Publishing State
- `DRAFT`
- `PUBLISHED`
- `FAILED`
- `UNPUBLISHED`
