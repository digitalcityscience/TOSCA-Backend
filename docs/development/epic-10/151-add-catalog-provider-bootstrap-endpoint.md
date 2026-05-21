> Development note: Run Django, uv, pytest, and migration checks inside the
> `tosca-django-api` container, not on the host.

# #151 SEF-P10 - Catalog / Provider API Refactor

## Purpose

The frontend needs a clean provider bootstrap endpoint that starts from active
providers and can later expand with counts/status metadata. This should come
from Django catalog state, not direct GeoServer metadata calls.

## Scope

Add or refactor a public catalog provider endpoint under the existing versioned
catalog API.

Likely touch points:

- `tosca_api/apps/catalog_api/urls.py`
- `tosca_api/apps/catalog_api/views.py`
- `tosca_api/apps/catalog_api/services/v1/`
- `tosca_api/apps/geodata_providers/services/queries/provider_query_service.py`
- `tosca_api/apps/catalog_api/tests/test_v1_api.py`
- `docs/development/catalog_api_summary.md` if endpoint docs are updated

## Requirements

- Add a read-only endpoint such as `/api/v1/catalog/providers/` or align the
  existing URL with the project router conventions.
- Response must include active providers only.
- Keep the first response minimal and frontend-friendly:

```json
[
  {
    "name": "provider-a",
    "base_url": "https://..."
  }
]
```

- Allow future expansion without breaking the contract:

```json
{
  "name": "provider-a",
  "base_url": "https://...",
  "status": "synced",
  "workspace_count": 0,
  "layer_count": 0
}
```

- If the current frontend needs workspace/layer links at bootstrap time, include
  links or nested summaries only after confirming the existing catalog v1 shape.
- Do not expose provider admin credentials, DB credentials, or internal-only
  fields.
- Keep response stable and versioned.

## Acceptance Criteria

- Provider endpoint is covered by API tests.
- Inactive providers are excluded.
- Empty active providers return a valid empty response, not a server error.
- The endpoint does not call GeoServer directly unless an existing v1 builder
  already requires a narrowly scoped compatibility lookup.

## Notes For Agent

Prefer query services from `geodata_providers/services/queries/` as the data
source. `catalog_api` should shape the response, not own provider domain logic.
