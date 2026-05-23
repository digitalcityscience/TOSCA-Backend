> Development note: Run Django, uv, pytest, and migration checks inside the
> `tosca-django-api` container, not on the host.

# Follow-up - Provider-Scoped Catalog Endpoints

Status: implemented in the backend catalog API. Provider-scoped routes are the
only public catalog read routes; unscoped catalog reads have been removed.

## Purpose

The final catalog shape should be provider-scoped. The frontend first asks for
providers, then uses the returned `provider_id` to request workspaces, layers,
and styles for that provider only.

This avoids using provider names in URLs and removes ambiguity when multiple
providers expose the same workspace or layer names.

## Target API Shape

Provider bootstrap:

```http
GET /api/v1/catalog/providers
```

Response:

```json
[
  {
    "id": "a1d7e7d0-0000-4000-9000-000000000000",
    "name": "local geo data server",
    "base_url": "http://localhost:8080/geoserver"
  }
]
```

Provider-scoped reads:

```http
GET /api/v1/catalog/providers/{provider_id}/workspaces
GET /api/v1/catalog/providers/{provider_id}/layers
GET /api/v1/catalog/providers/{provider_id}/workspaces/{workspace_name}/layers
GET /api/v1/catalog/providers/{provider_id}/workspaces/{workspace_name}/layers/{layer_name}
GET /api/v1/catalog/providers/{provider_id}/workspaces/{workspace_name}/resources/{layer_name}
GET /api/v1/catalog/providers/{provider_id}/styles
GET /api/v1/catalog/providers/{provider_id}/styles/{style_ref}
```

Where:

- `provider_id` is `GeodataEngine.id` as a UUID string.
- `workspace_name`, `layer_name`, and `style_ref` remain provider-scoped names
  or identifiers.
- Public reads include active providers only.
- Public reads exclude failed/stale resources according to the existing catalog
  visibility rules.

## Scope

Add provider-scoped v1 catalog endpoints and remove the older unscoped catalog
read routes from the public URL contract.

Likely touch points:

- `tosca_api/apps/catalog_api/urls.py`
- `tosca_api/apps/catalog_api/views.py`
- `tosca_api/apps/catalog_api/services/v1/visibility_service.py`
- `tosca_api/apps/catalog_api/services/v1/geoserver_v1_builder.py`
- `tosca_api/apps/geodata_providers/services/queries/`
- `tosca_api/apps/catalog_api/tests/test_v1_api.py`
- `docs/development/catalog_api_summary.md`

## Requirements

- Add provider lookup by UUID and return public `404` for:
  - missing provider
  - inactive provider
  - invalid UUID
- Provider-scoped workspace list returns only workspaces owned by that provider.
- Provider-scoped layer list returns only visible layers owned by that provider.
- Provider-scoped style list returns only styles owned by that provider.
- Provider-scoped style detail must not resolve a style from another provider
  when a name collision exists.
- Provider-scoped layer detail must not dedupe across providers; it should read
  from the provider identified in the URL.
- Existing unscoped catalog endpoints are removed; frontend work must use
  provider-scoped routes.
- Avoid direct GeoServer reads for bootstrap/listing. Continue reading from
  Django state and use remote calls only where the existing compatibility layer
  intentionally enriches resource detail.

## Visibility Rules

Provider-scoped reads still apply all current public catalog constraints:

- `GeodataEngine.is_active=True`
- `Layer.is_public=True`
- `Layer.publishing_state="PUBLISHED"`
- `Layer.sync_state` not in `FAILED`, `STALE`
- parent `Workspace` and `Store` sync state not in `FAILED`, `STALE`
- inactive provider resources are treated as missing

## Acceptance Criteria

- `/api/v1/catalog/providers` returns provider UUIDs.
- `/api/v1/catalog/providers/{provider_id}/workspaces` returns only that
  provider's visible workspaces.
- `/api/v1/catalog/providers/{provider_id}/layers` returns only that provider's
  visible layers.
- Same workspace/layer names in two providers do not collide when accessed
  through provider-scoped endpoints.
- Invalid or inactive providers return `404`, not leaked empty/internal payloads.
- Existing unscoped routes return `404`; regression tests should assert that
  catalog reads require provider-scoped URLs.
- Tests cover provider-scoped workspaces, layers, layer detail, style list,
  style detail, duplicate names across providers, inactive provider, and invalid
  UUID cases.

## Notes For Agent

Prefer extending `CatalogVisibilityService` with explicit provider filters over
duplicating filtering in views. Views should parse route parameters, call query
or visibility services, and build response shapes.
