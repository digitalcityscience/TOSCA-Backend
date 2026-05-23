> Development note: Run Django, uv, pytest, and migration checks inside the
> `tosca-django-api` container, not on the host.

# #153 SEF-P10 - Introduce Synchronization State Management

## Purpose

The system needs to record sync status and remote consistency on provider
resources. Without explicit state, catalog/admin users cannot tell whether a
resource is synced, stale, failed, or only locally known.

This is a supporting issue for #152, not the main synchronization bug itself.
#152 must make the hierarchy sync work. #153 makes that sync inspectable and
debuggable.

## Scope

Add sync state fields or a related state model for provider resources.

Likely touch points:

- `tosca_api/apps/geodata_providers/models.py`
- new migration under `tosca_api/apps/geodata_providers/migrations/`
- `tosca_api/apps/geodata_providers/sync_service.py`
- command services under `services/commands/`
- query services under `services/queries/`
- admin display configuration
- provider tests

## Requirements

- Track remote sync state for resources where it matters:
  `Workspace`, `Store`, `Layer`, and `Style`.
- Suggested state values:
  - `UNKNOWN`
  - `SYNCED`
  - `LOCAL_ONLY`
  - `REMOTE_ONLY`
  - `STALE`
  - `FAILED`
- Track useful diagnostics:
  - last sync timestamp
  - last sync error
  - optional remote identifier/hash/version if available
- Sync service should update state consistently after create/update/delete and
  reconciliation.
- Catalog visibility should be conservative for failed/stale resources unless
  existing behavior requires compatibility.
- A provider that is inactive should not be scheduled as `SYNC_PENDING`; inactive
  provider sync should be reported as skipped.

## Acceptance Criteria

- Migrations apply cleanly.
- Sync success marks resources as synced.
- Sync failures leave inspectable error state.
- Admin list/detail views expose enough state for operators.
- Tests cover state transitions for success and failure.

## Notes For Agent

Before adding separate state fields to every model, check whether a small
abstract mixin fits the app's model style. Keep field names boring and explicit;
future agents should not have to infer what a status means.
