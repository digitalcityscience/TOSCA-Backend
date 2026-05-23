> Development note: Run Django, uv, pytest, and migration checks inside the
> `tosca-django-api` container, not on the host.

#148 inactive provider filtering
#152 GeoServer workspace/store/layer sync fix
#153 sync state gerekiyorsa
#149 store test connection : tested and works
#150 schema update + clone behavior
#151 catalog providers endpoint
#154, #155
#156 final consistency pass


# SEF-P10 GeoServer Synchronization and Catalog Consistency

Epic issue: #157

This folder is the working handoff for Epic 10 branches. Before implementing an
Epic 10 issue, read this file first, then read the issue-specific file that
matches the branch or GitHub issue number.

The issue numbers here come from the existing GitHub issues/branches. Do not
create new branches just because the internal priority plan uses "Epic 1",
"Epic 2", etc. Map the work to the existing issue files below.

## Epic Goal

Make the provider catalog reliable by keeping Django's provider domain aligned
with GeoServer and by making catalog reads depend on Django state rather than
ad-hoc remote reads.

The intended direction is:

- `geodata_providers` owns provider domain state and operational workflows.
- `catalog_api` is read-only and consumer-facing.
- GeoServer mutations go through command/service classes.
- Sync and reconciliation are explicit services, not admin/view side effects.
- Catalog responses include only active providers and valid published resources.

## Existing Architecture To Preserve

The current app split is intentional:

- `tosca_api/apps/geodata_providers/`
  - models: `GeodataEngine`, `Workspace`, `Store`, `Layer`, `Style`
  - GeoServer client integration
  - command services
  - query services
  - admin actions/views
  - synchronization logic
- `tosca_api/apps/catalog_api/`
  - public read API
  - v1 compatibility builders
  - visibility filtering

Do not move provider mutations into `catalog_api`.
Do not make `catalog_api` call internal DRF endpoints inside the same Django
project. Use Python services.

## Shared Implementation Rules

- Keep business logic out of Django admin classes and DRF views.
- Prefer command services for mutations and query services for reads.
- Keep remote-first mutation semantics:
  - pre-check
  - remote mutate
  - verify remote state
  - local database persist/delete
- Treat "already exists" and "already deleted" as explicit idempotent outcomes.
- Make sync/reconciliation the recovery path for drift.
- Tests should mock GeoServer for unit tests and reserve live GeoServer for
  integration tests.
- Do not depend on `docker-compose.yml`; this branch currently has it deleted.
  Use the repo's current compose layout.

## Existing Branch / Issue Assignment

This is the authoritative assignment for Epic 10 work:

| Priority plan item | Existing issue/branch | File |
| --- | --- | --- |
| EPIC 1 - Provider activation state handling | #148 Inactive Provider Filtering | `148-inactive-provider-filtering.md` |
| EPIC 2 - GeoServer workspace/store/layer synchronization | #152 Introduce GeoServer Synchronization Service Layer | `152-introduce-geoserver-synchronization-service-layer.md` |
| EPIC 3 - Store validation + test connection | #149 Validate Datastore Connection Before Save | `149-validate-datastore-connection-before-save.md` |
| EPIC 4 - Store schema update support + complete store clone behavior | #150 Support Store Schema and Connection Updates | `150-support-store-schema-and-connection-updates.md` |
| EPIC 5 - Catalog/provider API refactor | #151 Add Catalog Provider Bootstrap Endpoint | `151-add-catalog-provider-bootstrap-endpoint.md` |
| EPIC 6 - Garage S3-compatible storage integration | #154 Integrate Garage S3-Compatible Storage | `154-integrate-garage-s3-compatible-storage.md` |
| EPIC 7 - Upload / GeoFetch integration | #155 Restore Admin Upload and GeoFetch Integration | `155-restore-admin-upload-and-geofetch-integration.md` |
| Technical support - sync state model | #153 Introduce Synchronization State Management | `153-introduce-synchronization-state-management.md` |
| Final regression pass | #156 GeoServer Synchronization and Catalog Consistency | `156-geoserver-synchronization-and-catalog-consistency.md` |

## Suggested Development Order

1. #148 Filter inactive providers globally.
2. #152 Fix GeoServer workspace/store/layer synchronization.
3. #153 Add sync state fields if needed by #152 debugging and visibility.
4. #149 Add store pre-save test connection validation.
5. #150 Add store schema/connection update support and complete store clone
   behavior.
6. #151 Refactor catalog/provider bootstrap endpoint.
7. #154 Add Garage S3-compatible storage integration.
8. #155 Restore upload / GeoFetch integration.
9. #156 Run final synchronization/catalog consistency regression pass.

This order follows the original priority plan: first stop inactive provider
leaks, then make the provider hierarchy deterministic, then harden store
operations and frontend bootstrap behavior.

## Acceptance For The Epic

- A provider can be created, validated, synced, deactivated, and read through
  the catalog without stale or inactive resources leaking.
- Admin workflows and catalog API agree on the same Django provider state.
- Sync results are inspectable enough to diagnose failures.
- Store connection changes do not silently leave GeoServer and Django diverged.
- Store clone does not create a half-copy; copied stores bring along the
  expected dependent configuration/resources or clearly report what cannot be
  copied.
- Upload-backed datasets have a stable storage path that works locally and in
  production.
