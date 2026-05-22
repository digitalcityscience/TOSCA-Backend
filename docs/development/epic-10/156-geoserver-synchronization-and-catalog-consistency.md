> Development note: Run Django, uv, pytest, and migration checks inside the
> `tosca-django-api` container, not on the host.

# #156 SEF-P10 - GeoServer Synchronization and Catalog Consistency

## Purpose

This is the final bug/pass issue for the epic. Its job is to verify that sync,
provider state, and catalog reads agree after the earlier implementation issues.

## Scope

Run through catalog and provider workflows, fix inconsistencies, and add missing
regression tests.

Likely touch points:

- `tosca_api/apps/geodata_providers/sync_service.py`
- `tosca_api/apps/geodata_providers/services/queries/`
- `tosca_api/apps/catalog_api/services/v1/`
- `tosca_api/apps/catalog_api/tests/`
- `tosca_api/apps/geodata_providers/tests/`

## Requirements

- Catalog responses should reflect Django's synchronized provider state.
- Inactive providers should remain hidden from public catalog responses.
- Failed/stale sync state should not be presented as cleanly published data
  unless explicitly required by a compatibility endpoint.
- Layer naming must stay consistent:
  - `Layer.name`: GeoServer resource/featuretype name
  - `Layer.table_name`: native PostGIS table/view
  - `Layer.title`: display title
- Styles and style assignments should survive sync without duplicate or
  cross-workspace mistakes.
- Drift recovery should be documented through tests:
  remote-created, remote-deleted, local-stale, and idempotent cases.

## Acceptance Criteria

- Full provider sync followed by catalog read produces expected provider,
  workspace, layer, and style payloads.
- Deactivating a provider removes it from catalog output without deleting data.
- Store/schema updates do not leave stale catalog layer metadata.
- Regression tests cover the bugs found during the pass.

## Notes For Agent

Treat this issue as an integration hardening pass. Avoid broad refactors unless
they directly remove an observed inconsistency or unblock a failing regression
test.

## Implementation Notes

This pass covers provider/sync/catalog consistency only. Garage storage, admin
upload, and GeoFetch ingestion remain in #154 and #155.

- Catalog workspace visibility now checks for the existence of at least one
  valid visible layer instead of excluding an entire workspace because another
  layer in the same workspace is `FAILED` or `STALE`.
- Catalog layer visibility excludes layers backed by `FAILED` or `STALE`
  stores/workspaces, not only failed/stale layer records.
- Regression tests now cover:
  - full provider sync followed by catalog provider/workspace/layer/style reads
  - inactive/failed/stale resources staying out of public catalog payloads
  - a workspace remaining visible when it still has another valid published
    layer
  - idempotent workspace sync
  - remote-deleted local workspace recovery
  - layer drift recovery with `Layer.name`, `Layer.table_name`, and
    `Layer.title` semantics preserved
  - default style assignment preservation during layer sync

## Verification

Targeted regression command:

```bash
docker exec tosca-django-api uv run pytest \
  tosca_api/apps/catalog_api/tests/test_v1_api.py \
  tosca_api/apps/geodata_providers/tests/test_provider_query_service.py \
  tosca_api/apps/geodata_providers/tests/test_workspace_query_service.py \
  tosca_api/apps/geodata_providers/tests/test_layer_query_service.py \
  tosca_api/apps/geodata_providers/tests/test_style_query_service.py \
  tosca_api/apps/geodata_providers/tests/test_geodata_engine_service.py \
  tosca_api/apps/geodata_providers/tests/test_store_service.py
```

Result: `78 passed`.
