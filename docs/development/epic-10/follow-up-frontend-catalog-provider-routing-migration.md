> Development note: Run Django, uv, pytest, and migration checks inside the
> `tosca-django-api` container, not on the host.

# Follow-up - Frontend Catalog Provider Routing Migration

Status: backend contract implemented in this repository. Frontend consumers
should now use the provider bootstrap UUID and provider-scoped catalog routes
described below.

## Purpose

After provider IDs and provider-scoped catalog endpoints exist, the frontend
should stop deriving catalog URLs from provider names or global workspace names.
It should use the provider list as the bootstrap source and route follow-up
catalog reads through `provider_id`.

This file documents the frontend/API integration work. It should be implemented
after the provider public URL/identifier contract and provider-scoped catalog
endpoint follow-ups.

## Required Flow

1. Load providers:

```http
GET /api/v1/catalog/providers
```

2. Store the selected provider:

```json
{
  "id": "a1d7e7d0-0000-4000-9000-000000000000",
  "name": "local geo data server",
  "base_url": "http://localhost:8080/geoserver"
}
```

3. Use provider-scoped endpoints for all catalog reads:

```http
GET /api/v1/catalog/providers/{provider_id}/workspaces
GET /api/v1/catalog/providers/{provider_id}/layers
GET /api/v1/catalog/providers/{provider_id}/workspaces/{workspace_name}/layers
GET /api/v1/catalog/providers/{provider_id}/workspaces/{workspace_name}/layers/{layer_name}
GET /api/v1/catalog/providers/{provider_id}/styles/{style_ref}
```

4. Use the returned `base_url` only for externally reachable GeoServer/WMS/WMTS
   URLs. Do not use it as the backend sync URL.

## Scope

Likely touch points depend on the frontend repository, but the API contract here
must be documented in this backend repo.

Backend docs/tests touch points:

- `docs/development/catalog_api_summary.md`
- Epic 10 README
- catalog API tests that describe provider-scoped URLs and assert unscoped
  catalog reads are not available

Frontend expected touch points:

- provider bootstrap client
- selected provider state/store
- layer/workspace/style catalog API client functions
- map source builders that currently use a global GeoServer base URL
- UI tests or integration tests for multiple providers

## Requirements

- Frontend must treat provider `name` as display text only.
- Frontend must use provider `id` for API routing.
- Frontend must use provider `base_url` from catalog bootstrap as the public
  GeoServer URL for map service requests.
- Frontend must support spaces and non-URL-safe characters in provider names.
- Frontend should handle multiple providers exposing the same workspace/layer
  names without collisions.
- If no provider is active, the frontend should show an empty catalog state
  instead of trying to call global workspace/layer endpoints.
- Unscoped catalog endpoints are removed from the public routing contract; new
  code must route catalog reads through the selected provider UUID.

## Acceptance Criteria

- Selecting a provider changes subsequent workspace/layer/style API calls to
  include that provider's UUID.
- Provider names with spaces render correctly and are never inserted into API
  paths.
- Map/WMS/WMTS URLs use the provider public URL returned from bootstrap.
- Multiple providers with duplicate workspace/layer names can be browsed
  independently.
- UI handles inactive provider removal after refresh without stale selected
  provider errors.
- Documentation clearly states that public catalog `base_url` is external and
  `GeodataEngine.base_url` remains backend-internal.

## Notes For Agent

The backend routing contract now requires provider-scoped catalog reads. If a
frontend still calls global catalog paths, treat that as stale integration code
and migrate it to the selected provider UUID.
