> Development note: Run Django, uv, pytest, and migration checks inside the
> `tosca-django-api` container, not on the host.

# #149 SEF-P10 - Validate Datastore Connection Before Save

## Purpose

PostGIS store records should not be saved as usable provider stores when their
connection details are invalid. A failed connection must be surfaced before the
local database and GeoServer state drift.

## Scope

Add validation around PostGIS store creation/update flows and expose a test
connection path that the admin/API can call before saving.

Likely touch points:

- `tosca_api/apps/geodata_providers/models.py`
- `tosca_api/apps/geodata_providers/admin_forms.py`
- `tosca_api/apps/geodata_providers/services/commands/store_service.py`
- `tosca_api/apps/geodata_providers/postgis_inspector.py`
- `tosca_api/apps/geodata_providers/api/urls.py`
- `tosca_api/apps/geodata_providers/api/views.py`
- `tosca_api/apps/geodata_providers/tests/test_store_service.py`
- `tosca_api/apps/geodata_providers/tests/test_admin_forms.py`

## Requirements

- Validate required PostGIS fields for `Store.StoreType.POSTGIS`:
  `host`, `port`, `database`, `username`, `password`, and `schema`.
- Attempt a real connection check in service/admin save paths where credentials
  are available.
- Add a reusable service/helper such as `test_store_connection(payload)` that
  checks:
  - host reachable
  - database reachable
  - credentials valid
  - schema exists
  - GeoServer can accept or validate the datastore config
- Return a normalized failure result from services instead of throwing raw
  database or driver exceptions to callers.
- Admin forms should show a field/form error and must not create the remote
  GeoServer datastore when connection validation fails.
- Add an API endpoint for explicit pre-save validation if the current API
  surface supports store management, for example:

```http
POST /stores/test-connection
```

Expected response:

```json
{
  "success": true,
  "details": {}
}
```

- Validation should be testable without a real database by mocking the
  connection/inspector boundary.

## Acceptance Criteria

- Invalid PostGIS credentials block store creation before GeoServer mutation.
- Valid PostGIS credentials still create the GeoServer store and then the local
  `Store`.
- The test connection endpoint returns actionable success/failure details.
- Unit tests cover validation success, validation failure, and skipped validation
  for non-PostGIS stores.
- Error messages are actionable enough for admin users.

## Notes For Agent

Do not put direct socket/psycopg checks inside the admin class. Keep a small
helper/service boundary so command services and admin forms share the same
validation behavior.
