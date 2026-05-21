> Development note: Run Django, uv, pytest, and migration checks inside the
> `tosca-django-api` container, not on the host.

# #152 SEF-P10 - GeoServer Workspace / Store / Layer Synchronization

## Purpose

Fix the incomplete GeoServer synchronization contract.

The frontend expects normalized provider metadata in this shape:

```text
Provider
  -> Workspace
      -> Store
          -> Layer
```

The backend must guarantee that this hierarchy is imported and reconciled from
GeoServer. The problem we discussed first was not just "create a nicer service
layer"; the real bug is that sync may create workspaces while stores/layers are
missing or unreliable.

The service layer refactor is only the implementation technique. The business
goal is deterministic workspace/store/layer synchronization.

## Current Branch

Expected branch:

`152-sef-p10-introduce-geoserver-synchronization-service-layer`

## Scope

Make sync recursively fetch and persist the full GeoServer hierarchy:

1. Provider connection
2. Workspaces
3. Stores per workspace
4. Layers / feature types per store
5. Coverage stores / raster layers if supported by the current model

Also keep inactive providers out of sync runs. If `GeodataEngine.is_active` is
false, sync should skip that provider and return a clear skipped result.

Likely touch points:

- `tosca_api/apps/geodata_providers/sync_service.py`
- `tosca_api/apps/geodata_providers/engine_factory.py`
- `tosca_api/apps/geodata_providers/geoserver/client.py`
- `tosca_api/apps/geodata_providers/services/commands/geodata_engine_service.py`
- `tosca_api/apps/geodata_providers/admin_views/engine.py`
- `tosca_api/apps/geodata_providers/admin_views/workspace.py`
- `tosca_api/apps/geodata_providers/management/commands/sync_geoserver.py`
- `tosca_api/apps/geodata_providers/management/commands/sync_geoengine_dev.py`
- tests under `tosca_api/apps/geodata_providers/tests/`

## Current Problem

Known or suspected current behavior:

- workspace sync exists
- store sync may be incomplete or skipped
- layer sync may be incomplete or blocked by store import failures
- frontend expects workspace/store/layer metadata and breaks when the hierarchy
  is partial

Root causes to investigate:

- only workspace REST endpoint is called
- store iteration is missing or only handles one store type
- datastore parsing ignores GeoServer response shapes
- store import requires password even though GeoServer usually hides it
- featuretype endpoint is not called per datastore
- coverage stores are ignored
- namespace/workspace mismatch causes layers to be skipped
- sync errors are swallowed and reported as partial success

## Required GeoServer REST Flow

Fetch workspaces:

```http
/rest/workspaces.json
```

Fetch stores per workspace:

```http
/rest/workspaces/{workspace}/datastores.json
/rest/workspaces/{workspace}/coveragestores.json
```

Fetch vector layers / feature types per datastore:

```http
/rest/workspaces/{workspace}/datastores/{store}/featuretypes.json
```

Fetch raster layers per coverage store if raster support is active:

```http
/rest/workspaces/{workspace}/coveragestores/{store}/coverages.json
```

Persist every level that exists remotely unless the provider is inactive.

## Requirements

- Add or fix a clear sync service API for:
  - full provider sync
  - workspace sync
  - store sync per workspace
  - layer sync per store
  - style sync if already part of the current flow
- Store password must not be required during sync. GeoServer normally does not
  return datastore passwords. If credentials are unavailable:
  - save the store anyway
  - mark password/credentials unavailable if the model supports it
  - continue layer synchronization
- Persist stores even when only partial connection metadata is available from
  GeoServer.
- Fetch layers per store and attach them to the correct `Workspace` and `Store`.
- Keep `Layer.name`, `Layer.table_name`, and `Layer.title` semantics aligned:
  - `Layer.name`: GeoServer resource/featuretype name
  - `Layer.table_name`: native PostGIS table/view when known
  - `Layer.title`: display title
- Do not let a single failed store or layer abort the whole provider sync unless
  the provider connection itself is unusable.
- Keep the result shape normalized:
  - `success`
  - per-resource counters: `synced`, `created`, `updated` if needed,
    `deleted`, `errors`
  - top-level `error` when the whole sync fails
- Include enough detail in errors to identify workspace/store/layer scope.
- Avoid spreading direct `GeoServerClient(...)` construction outside the
  service/factory boundary.
- Preserve existing admin and management-command behavior.
- Make sync methods unit-testable with mocked GeoServer responses.

## Expected Behavior

After full sync, Django should contain a deterministic hierarchy:

```text
GeodataEngine
  Workspace
    Store
      Layer
```

The catalog/frontend layer should then be able to read this hierarchy from
Django without making ad-hoc GeoServer metadata calls just to discover basic
workspace/store/layer structure.

## Acceptance Criteria

- Admin "sync" buttons still work.
- Management commands still work.
- Full sync imports workspaces, stores, and layers.
- Stores are saved even when GeoServer does not expose datastore passwords.
- Layer sync continues after passwordless store import.
- Frontend/catalog can receive `workspace + store + layers` structure from
  Django state.
- Inactive providers are skipped by sync.
- Tests cover:
  - workspace import
  - datastore import
  - coverage store handling if supported
  - featuretype/layer import
  - passwordless store import
  - partial failures with scoped errors

## Notes For Agent

Issue number `#152` comes from the existing GitHub issue/branch name. It should
not be interpreted as a separate abstract refactor task. Implement the concrete
sync hierarchy fix described here.

Do not rewrite all sync internals in one pass unless tests force it. First make
the full hierarchy deterministic, then clean up the service boundary where it
helps.
