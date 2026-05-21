# #148 SEF-P10 - Inactive Provider Filtering

## Purpose

Inactive providers must not leak into catalog responses or bootstrap metadata.
Deactivation is the safe alternative to destructive provider deletion, so the
read side must treat `GeodataEngine.is_active=False` as a hard visibility
boundary.

## Scope

Update catalog-facing query paths so they include only active providers by
default.

Likely touch points:

- `tosca_api/apps/geodata_providers/services/queries/provider_query_service.py`
- `tosca_api/apps/geodata_providers/services/queries/workspace_query_service.py`
- `tosca_api/apps/geodata_providers/services/queries/layer_query_service.py`
- `tosca_api/apps/catalog_api/services/v1/visibility_service.py`
- `tosca_api/apps/catalog_api/views.py`
- catalog API tests under `tosca_api/apps/catalog_api/tests/`

## Requirements

- Exclude inactive `GeodataEngine` records from public catalog provider lists.
- Exclude workspaces, stores, layers, and styles that belong to inactive
  providers from public catalog responses.
- Skip synchronization jobs for inactive providers. Return an explicit skipped
  result rather than syncing hidden provider data.
- Keep admin/internal management views able to see inactive providers unless
  they already have their own filters.
- Do not delete inactive provider records or their children.
- Make the default read behavior safe. If internal code needs inactive records,
  require an explicit opt-in parameter such as `include_inactive=True`.

## Acceptance Criteria

- Catalog provider/bootstrap endpoint returns active providers only.
- Catalog layer/workspace endpoints do not return resources from inactive
  providers.
- Existing active-provider behavior remains unchanged.
- Tests cover active and inactive providers with otherwise identical child
  resources.

## Notes For Agent

Look for duplicated filtering in catalog builders before adding more. If a
shared visibility service already owns this rule, extend it there and make query
services call it consistently.

## Implementation Notes

The inactive-provider boundary is implemented as a default read-side rule, not a
delete or cascade operation. Provider records and their child workspaces, stores,
layers, and styles remain in the database, but catalog-facing reads exclude
resources whose provider has `GeodataEngine.is_active=False`.

### Query Service Contracts

Provider, workspace, layer, and style query services use an explicit
`include_inactive` opt-in for internal/admin callers that need inactive records.
The default path remains safe for catalog consumers.

- `ProviderQueryService.list_providers(include_inactive=False)`
  - Filters `GeodataEngine.objects` with `is_active=True` by default.
  - Keeps `include_inactive=True` available for internal management reads.
  - Serializes provider metadata and provider-level workspace/layer counts.
- `WorkspaceQueryService.get_workspace_detail(..., include_inactive=False)`
  - Filters through `workspace.geodata_engine.is_active=True` by default.
  - Raises `Workspace.DoesNotExist` for inactive-provider workspaces unless
    explicitly opted in.
  - Prefetches only visible public/published layers for catalog-shaped details.
- `WorkspaceQueryService.list_provider_workspaces(..., include_inactive=False)`
  - Returns no workspace summaries for inactive providers by default.
  - Allows inactive-provider workspace summaries only with `include_inactive=True`.
- `LayerQueryService.get_layer_detail(..., include_inactive=False)`
  - Filters through `layer.workspace.geodata_engine.is_active=True`.
  - Raises `Layer.DoesNotExist` for inactive-provider layers unless opted in.
- `LayerQueryService.list_workspace_layers(..., include_inactive=False)`
  - Returns an empty list for inactive-provider workspaces by default.
- `StyleQueryService`
  - Applies the same provider-active filter in base/catalog style querysets.
  - Public style resolution/content reads reject inactive-provider styles unless
    `include_inactive=True` is passed.

### Catalog API Visibility

`CatalogVisibilityService` is the public catalog boundary for v1 endpoints.

- `list_visible_workspaces()`
  - Filters workspaces with `geodata_engine__is_active=True`.
  - Also requires at least one public/published layer.
  - Dedupes duplicated workspace names across provider integrations.
- `get_visible_workspace(workspace_name=...)`
  - Uses the same active-provider queryset.
  - Inactive-provider workspaces are treated the same as missing workspaces.
- `list_visible_layers(workspace_name=None)`
  - Filters layers with `workspace__geodata_engine__is_active=True`,
    `is_public=True`, and `publishing_state="PUBLISHED"`.
  - Dedupes duplicated `(workspace_name, layer_name)` pairs across provider
    integrations.
- `get_visible_layer(workspace_name=..., layer_name=...)`
  - Uses the active-provider workspace and layer querysets.
  - Inactive-provider layers are treated as missing and mapped to public `404`
    responses by the DRF views.

### Sync Behavior

`GeoServerSyncService` does not synchronize inactive providers.

- `sync_all_resources()` returns a successful skipped payload:
  - `success=True`
  - `skipped=True`
  - `reason="Engine '<name>' is inactive."`
- Section sync methods for workspaces, stores, styles, and layers return the
  same skipped section shape.
- Push sync helpers also return skipped results for inactive engines.
- No inactive-provider child records are deleted as part of this behavior.

### Tests

Coverage exists at both service and public API levels.

- Provider query tests assert active-only provider lists by default and
  `include_inactive=True` opt-in behavior.
- Workspace query tests assert inactive-provider workspaces are excluded by
  default and available with explicit opt-in.
- Layer query tests assert inactive-provider layers are excluded by default and
  available with explicit opt-in.
- Style query tests assert inactive-provider styles cannot be resolved or read
  through catalog defaults.
- Catalog v1 tests assert inactive-provider workspaces and layers return public
  `404` responses when accessed directly by URL.
- Sync service tests assert inactive engines are skipped and no remote workspace
  pull is attempted.

Targeted verification command used on the #152 development container:

```bash
docker exec tosca-django-api uv run pytest \
  tosca_api/apps/geodata_providers/tests/test_provider_query_service.py \
  tosca_api/apps/geodata_providers/tests/test_workspace_query_service.py \
  tosca_api/apps/geodata_providers/tests/test_layer_query_service.py \
  tosca_api/apps/geodata_providers/tests/test_style_query_service.py \
  tosca_api/apps/catalog_api/tests/test_v1_api.py
```

Result: `46 passed`.
