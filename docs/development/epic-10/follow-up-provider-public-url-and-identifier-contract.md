> Development note: Run Django, uv, pytest, and migration checks inside the
> `tosca-django-api` container, not on the host.

# Follow-up - Provider Public URL and Identifier Contract

Status: implemented in the backend provider/catalog contract. Catalog bootstrap
returns `id`, display `name`, and public `base_url`; internal
`GeodataEngine.base_url` remains for Django-to-provider connectivity.

## Purpose

Catalog provider bootstrap must expose a stable provider identifier and a public
GeoServer URL. Provider names are user-facing labels and may contain spaces,
mixed case, or translated text, so they must not be used as URL identifiers.

The current model already has a stable UUID primary key on `GeodataEngine`.
Use that UUID as the public `provider_id` unless a later product requirement
explicitly asks for a separate slug. Do not add a duplicate identifier field
just for routing.

## Implemented State

- `GeodataEngine.id` is a UUID primary key and is suitable for
  `/providers/{provider_id}/...` routes.
- `GeodataEngine.name` is a display name and is not URL-safe enough to be a
  route identifier.
- `GeodataEngine.base_url` is the internal engine URL used by Django to talk to
  GeoServer. In local Docker this may be `http://geoserver:8080/geoserver`.
- `GeodataEngine.public_url` stores the externally reachable provider URL used
  by public catalog consumers.
- The public catalog provider response exposes `id`, display `name`, and
  `base_url`, where `base_url` is populated from `GeodataEngine.public_url`.
- `.env.example` documents `GEOSERVER_PUBLIC_URL`; local development can fall
  back to `http://localhost:{GEOSERVER_PORT}/geoserver` when the setting is not
  configured.

## Scope

The backend scope is implemented. Keep this checklist as the contract for
future frontend and regression work: provider state has a required public URL,
and catalog bootstrap returns the UUID identifier with the externally reachable
URL.

Likely touch points:

- `tosca_api/apps/geodata_providers/models.py`
- migration under `tosca_api/apps/geodata_providers/migrations/`
- `tosca_api/settings/base.py`
- `tosca_api/apps/geodata_providers/management/commands/setup_default_engine.py`
- `tosca_api/apps/geodata_providers/admin.py`
- `tosca_api/apps/geodata_providers/api/serializers.py`
- `tosca_api/apps/geodata_providers/services/queries/provider_query_service.py`
- `tosca_api/apps/catalog_api/views.py`
- provider/catalog tests
- `.env.example`, `.env.dev`, and relevant README/development docs

## Requirements

- Add `GeodataEngine.public_url` as a required URL/char field.
- Keep `GeodataEngine.base_url` as the internal service URL used by backend
  clients and sync services.
- Catalog responses must not expose the internal Docker network URL when a
  public URL is configured.
- Provider bootstrap response must include the stable UUID identifier:

```json
[
  {
    "id": "a1d7e7d0-0000-4000-9000-000000000000",
    "name": "local geo data server",
    "base_url": "http://localhost:8080/geoserver"
  }
]
```

- Keep the response key `base_url` for frontend compatibility, but populate it
  from `public_url`. If a future API version wants both values, expose the
  internal URL only behind authenticated provider-management endpoints, not the
  public catalog endpoint.
- Existing records need a safe migration path:
  - data migration should backfill `public_url` from `base_url` for existing
    providers
  - after backfill, make the field non-blank/non-null
- Default engine bootstrap should set:
  - internal `base_url`: `http://{GEOSERVER_HOST}:{GEOSERVER_PORT}/geoserver`
    for the Django-to-GeoServer network path
  - public `public_url`: `GEOSERVER_PUBLIC_URL` when configured
  - local fallback public URL:
    `http://localhost:{GEOSERVER_PORT}/geoserver`
- The local fallback is acceptable for development because GeoServer is exposed
  through the host port in `docker-compose-dev.yml`.
- Production configuration must require `GEOSERVER_PUBLIC_URL`; do not silently
  publish an internal Docker hostname in production.

## Settings Contract

Add or standardize these settings:

```text
GEOSERVER_HOST=geoserver
GEOSERVER_PORT=8080
GEOSERVER_PUBLIC_URL=http://localhost:8080/geoserver
```

For production:

```text
GEOSERVER_PUBLIC_URL=https://geoserver.example.com/geoserver
```

If `GEOSERVER_PUBLIC_URL` is not set in development, derive it from
`GEOSERVER_PORT`. If it is not set in production, fail loudly during default
engine setup or settings validation.

## Acceptance Criteria

- Provider catalog list returns `id`, `name`, and `base_url`.
- `id` equals `str(GeodataEngine.id)`.
- public catalog `base_url` equals `GeodataEngine.public_url`, not the internal
  `GeodataEngine.base_url`.
- Existing provider rows migrate successfully and receive a populated
  `public_url`.
- Default engine setup stores both internal and public URLs correctly.
- Admin/API provider forms require `public_url` for create/update.
- Tests cover:
  - provider list includes UUID id
  - provider list uses public URL
  - setup default engine derives localhost public URL in dev
  - existing providers are migrated/backfilled safely

## Notes For Agent

Do not rename `base_url` in this pass. It is currently used by sync clients,
remote catalog compatibility lookups, and provider command services as the
backend connection URL.
