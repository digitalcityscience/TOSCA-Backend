> Development note: Run Django, uv, pytest, and migration checks inside the
> `tosca-django-api` container, not on the host.

# #150 SEF-P10 - Store Schema / Connection Updates and Complete Store Clone

## Purpose

Store connection details and schema need a safe update path. Editing these
fields locally without updating/verifying GeoServer leaves layers and catalog
metadata inconsistent.

There is also a related operational bug: when copying/cloning a store, the copy
should not be a shallow record-only clone. The expected dependent
configuration/resources should be copied or regenerated automatically so the
new store is usable without manual repair.

## Scope

Implement a command-service update flow for stores and complete the store clone
behavior.

Likely touch points:

- `tosca_api/apps/geodata_providers/services/commands/store_service.py`
- `tosca_api/apps/geodata_providers/geoserver/client.py`
- `tosca_api/apps/geodata_providers/admin.py`
- `tosca_api/apps/geodata_providers/admin_views/store.py`
- `tosca_api/apps/geodata_providers/admin_forms.py`
- `tosca_api/apps/geodata_providers/tests/test_store_service.py`
- `tosca_api/apps/geodata_providers/tests/test_admin_forms.py`

## Requirements

### Store Update

- Add an explicit store update service method, for example
  `StoreService.update_store_connection(...)`.
- Support updates for PostGIS connection fields:
  `host`, `port`, `database`, `username`, `password`, `schema`, and workspace
  binding if the current model/admin allows it.
- Validate the target connection before remote mutation.
- Update GeoServer datastore configuration and verify the resulting remote
  state.
- Persist Django changes only after remote update succeeds.
- Preserve encrypted password behavior.
- Return a normalized result contract with `success`, `message`, `error`,
  `verified`, `resource`, and `remote_result`.

When schema changes:

- validate the new schema
- refresh available layer/featuretype list
- detect removed, new, and changed layers
- preserve existing Django layer IDs where the same remote layer still exists
- mark or remove stale dependent layers according to the sync policy

### Store Clone / Copy

When a store is copied/cloned from admin or service code:

- copy all store connection/config fields that are safe to copy
- require a new password only if the source password cannot be decrypted or
  should not be reused
- create/update the remote GeoServer datastore for the clone
- automatically sync or copy dependent layers for the cloned store
- preserve style assignments where the referenced styles are valid for the
  target workspace/provider
- avoid creating a store clone that has no layers when the source store has
  layers and the remote provider exposes them
- report exactly which dependent resources could not be copied/synced

## Acceptance Criteria

- Updating schema/connection fields changes both GeoServer and Django state.
- If remote update or verification fails, Django local fields remain unchanged.
- Existing store create/delete tests still pass.
- Tests cover successful update, validation failure, remote failure, and
  idempotent no-op update.
- Cloning a store automatically brings over or regenerates the expected layer
  structure.
- Store clone reports partial failures instead of silently creating an unusable
  shallow copy.
- Tests cover store clone with dependent layers and style assignments where the
  current model supports them.

## Notes For Agent

Check the geoserver-rest wrapper first. If it cannot update datastore connection
parameters safely, add a controlled REST call in `geoserver/client.py` with
request logging and normalized responses.

For clone behavior, check the existing `StoreService.clone_store(...)` and
`admin_views/store.py` first. Extend that path instead of adding a parallel clone
implementation.
