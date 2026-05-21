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
