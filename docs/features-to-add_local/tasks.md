# Implementation Tasks: Geostories × Calendar × Participation

> Each task is a separate branch/PR. Merge to main before starting next task.

---

## Open Questions — Decision Guide

### 1. FeatureLink Validation: PostgreSQL Trigger vs Django `clean()`

| Approach                  | Pros                                                                                                | Cons                                                                     |
| ------------------------- | --------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| **PostgreSQL Trigger**    | Enforced at DB level (impossible to bypass), works even for raw SQL imports, single source of truth | Harder to debug, requires SQL knowledge, logic duplicated outside Django |
| **Django `clean()` only** | Pythonic, easier to test, all logic in one place, better error messages for API                     | Can be bypassed by raw SQL, bulk operations may skip validation          |
| **Hybrid** (recommended)  | Best of both: Django provides UX-friendly errors, DB prevents data corruption                       | Slightly more maintenance                                                |

**Recommendation**: Start with **Django `clean()` only** for Phase 2. Add DB trigger in a later hardening phase if needed. Rationale: Easier to iterate, all operations go through Django anyway, and we can add the trigger later without breaking changes.

---

### 3. Media Assets: MediaAsset Model vs FileField

| Approach                        | Pros                                                                               | Cons                                                  |
| ------------------------------- | ---------------------------------------------------------------------------------- | ----------------------------------------------------- |
| **FileField directly on model** | Simple, no extra models, Django handles storage                                    | No reusability, no metadata, harder to manage orphans |
| **Dedicated MediaAsset model**  | Reusable across features, supports metadata (alt text, dimensions), easier cleanup | More complexity, extra joins                          |

**Recommendation**: Use **FileField for MVP** (`cover_image = models.ImageField(...)`). Create MediaAsset in a future "Media Management" phase when we need shared assets, galleries, or video support.

---

## Phase 0: Infrastructure (Tech Stack Fixes)

> **Goal**: Prepare the codebase for GeoDjango and PostGIS features.

---

### Task 0.1: Add GDAL to Docker Image

**Branch**: `feat/gdal-docker`

**Description**: Install GDAL/GEOS/PROJ libraries in the Django Docker image to enable GeoDjango.

**Changes**:

- [x] Update `docker/django/Dockerfile` to install `gdal-bin`, `libgdal-dev`, `libgeos-dev`, `libproj-dev`
- [x] Rebuild image and verify GDAL is accessible

**Acceptance Criteria**:

- [x] `docker compose build django` succeeds
- [x] Inside container: `python -c "from django.contrib.gis.gdal import HAS_GDAL; print(HAS_GDAL)"` prints `True`
- [x] Inside container: `gdalinfo --version` returns version info

**Tests**:

```bash
# Manual verification after container starts
docker compose exec django python -c "from django.contrib.gis.gdal import HAS_GDAL; print('GDAL available:', HAS_GDAL)"
docker compose exec django gdalinfo --version
```

**Commit Message**:

```text
feat(docker): add GDAL/GEOS/PROJ to Django image

Install geospatial libraries required for GeoDjango:
- gdal-bin, libgdal-dev
- libgeos-dev
- libproj-dev

This enables django.contrib.gis for PostGIS geometry fields.
```

---

### Task 0.2: Configure GeoDjango in Settings

**Branch**: `feat/geodjango-config`

**Description**: Enable GeoDjango by updating Django settings to use PostGIS backend.

**Changes**:

- [x] Add `django.contrib.gis` to `INSTALLED_APPS` in `settings/base.py`
- [x] Change database engine from `django.db.backends.postgresql` to `django.contrib.gis.db.backends.postgis`

**Acceptance Criteria**:

- [x] `make up` starts without errors
- [x] `python manage.py check` passes
- [x] `python manage.py dbshell` connects and `SELECT PostGIS_Version();` returns version

**Tests**:

```bash
docker compose exec django python manage.py check
docker compose exec django python -c "from django.contrib.gis.db import models; print('GeoDjango OK')"
docker compose exec django python manage.py dbshell -c "SELECT PostGIS_Version();"
```

**Commit Message**:

```
feat(settings): configure GeoDjango with PostGIS backend

- Add django.contrib.gis to INSTALLED_APPS
- Switch database engine to django.contrib.gis.db.backends.postgis

Enables use of PointField, GeometryField for spatial models.

- **Check**: Ensure `CursorPagination` is configured as the default pagination class in settings or for spatial viewsets (avoids OFFSET performance issues).

```

---

### Task 0.3: Verify PostGIS Extension

**Branch**: `feat/postgis-verify`

**Description**: Verify PostGIS extension is enabled and document verification in tests.

**Changes**:

- [x] Add a simple test in `tosca_api/apps/core/tests/test_postgis.py` to verify PostGIS
- [x] Verify `init001.sh` has `CREATE EXTENSION IF NOT EXISTS postgis;` (already present ✓)

**Acceptance Criteria**:

- [x] Test passes: `pytest tosca_api/apps/core/tests/test_postgis.py -v --ds=tosca_api.settings.base`
- [x] PostGIS version query works from Django

**Tests**:

```python
# tosca_api/apps/core/tests/test_postgis.py
import pytest
from django.db import connection

@pytest.mark.django_db
def test_postgis_extension_enabled():
    """Verify PostGIS extension is available."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT PostGIS_Version();")
        version = cursor.fetchone()[0]
        assert version is not None
        assert "3." in version  # PostGIS 3.x
```

**Commit Message**:

```
test(core): add PostGIS extension verification test

Add test to ensure PostGIS is enabled and accessible.
This catches misconfiguration early in CI/CD.
```

---

### Task 0.4: Create `campaigns` App Skeleton

**Branch**: `feat/campaigns-app`

**Description**: Create the campaigns app with Campaign model (no API yet).

**Changes**:

- [x] Create `tosca_api/apps/campaigns/` directory structure
- [x] Add `Campaign` model with fields: id (UUID), title, summary, status, visibility, created_by, timestamps
- [x] Register in `INSTALLED_APPS`
- [x] Create and apply migrations
- [x] Register in Django Admin

**Acceptance Criteria**:

- [x] `python manage.py makemigrations campaigns` succeeds
- [x] `python manage.py migrate` succeeds
- [x] Campaign model visible in Django Admin
- [x] Can create Campaign via Admin UI

**Tests**:

```python
# tosca_api/apps/campaigns/tests/test_models.py
import pytest
from django.contrib.auth import get_user_model
from tosca_api.apps.campaigns.models import Campaign

User = get_user_model()

@pytest.mark.django_db
def test_campaign_creation():
    user = User.objects.create_user(username="testuser", password="testpass")
    campaign = Campaign.objects.create(
        title="Test Campaign",
        created_by=user
    )
    assert campaign.id is not None
    assert campaign.status == Campaign.Status.DRAFT
    assert campaign.visibility == Campaign.Visibility.PRIVATE

@pytest.mark.django_db
def test_campaign_str():
    user = User.objects.create_user(username="testuser", password="testpass")
    campaign = Campaign.objects.create(title="My Campaign", created_by=user)
    assert str(campaign) == "My Campaign"
```

**Commit Message**:

```
feat(campaigns): add Campaign model

Create campaigns app with core Campaign model:
- UUID primary key
- title, summary fields
- status enum (draft/active/archived)
- visibility enum (public/private)
- created_by FK to User
- timestamps (created_at, updated_at)

Includes Django Admin registration and model tests.
```

---

### Task 0.5: Create `geocontext` App

**Branch**: `feat/geocontext-app`

**Description**: Create the geocontext app for shared rich text content.

**Changes**:

- [x] Create `tosca_api/apps/geocontext/` with GeoContext model
- [x] Fields: id (UUID), content, content_type (simple/rich), created_by, timestamps
- [x] Register in `INSTALLED_APPS`, Admin, migrations

**Acceptance Criteria**:

- [x] Migrations apply successfully
- [x] Model visible in Admin
- [x] Tests pass

**Tests**:

```python
# tosca_api/apps/geocontext/tests/test_models.py
import pytest
from django.contrib.auth import get_user_model
from tosca_api.apps.geocontext.models import GeoContext

User = get_user_model()

@pytest.mark.django_db
def test_geocontext_creation():
    user = User.objects.create_user(username="testuser", password="testpass")
    ctx = GeoContext.objects.create(
        content="Sample content",
        created_by=user
    )
    assert ctx.id is not None
    assert ctx.content_type == GeoContext.ContentType.SIMPLE
```

**Commit Message**:

```
feat(geocontext): add GeoContext model

Create geocontext app for shareable content blocks:
- UUID primary key
- content TextField
- content_type enum (simple/rich)
- created_by FK
- timestamps

Will be linked 1:1 to GeoStory, CalendarEvent, GeoFeedback.
```

---

### Task 0.6: Create `layerrefs` App

**Branch**: `feat/layerrefs-app`

**Description**: Create the layerrefs app for GeoServer layer pointers.

**Changes**:

- [x] Create `tosca_api/apps/layerrefs/` with LayerRef model
- [x] Fields: id (UUID), layer_name (unique), created_at
- [x] Register in `INSTALLED_APPS`, Admin, migrations

**Acceptance Criteria**:

- [x] Migrations apply
- [x] LayerRef enforces unique layer_name
- [x] Tests pass

**Tests**:

```python
# tosca_api/apps/layerrefs/tests/test_models.py
import pytest
from django.db import IntegrityError
from tosca_api.apps.layerrefs.models import LayerRef

@pytest.mark.django_db
def test_layerref_creation():
    ref = LayerRef.objects.create(layer_name="workspace:roads")
    assert ref.id is not None

@pytest.mark.django_db
def test_layerref_unique_name():
    LayerRef.objects.create(layer_name="workspace:roads")
    with pytest.raises(IntegrityError):
        LayerRef.objects.create(layer_name="workspace:roads")
```

**Commit Message**:

```
feat(layerrefs): add LayerRef model

Create layerrefs app for GeoServer layer references:
- UUID primary key
- layer_name (unique, format: workspace:layer)
- created_at timestamp

Minimal model - just a pointer for M2M with features.
```

---

## Phase 1: GeoStory Feature

> **Goal**: First complete feature with CRUD API.

---

### Task 1.1: Create `geostories` App with GeoStory Model

**Branch**: `feat/geostories-model`

**Description**: Create geostories app with GeoStory model and M2M to LayerRef.

**Changes**:

- [x] Create `tosca_api/apps/geostories/` with GeoStory, GeoStoryLayer models
- [x] Fields: id, campaign (FK), title, summary, status, author (FK), context (OneToOne), layers (M2M), timestamps
- [x] Register in `INSTALLED_APPS`, Admin, migrations

**Acceptance Criteria**:

- [x] GeoStory links to Campaign and GeoContext
- [x] GeoStoryLayer junction table with display_order
- [x] Tests pass

**Tests**:

```python
@pytest.mark.django_db
def test_geostory_with_layers():
    # Setup campaign, user, layers
    story = GeoStory.objects.create(campaign=campaign, title="Test", author=user)
    layer = LayerRef.objects.create(layer_name="workspace:test")
    GeoStoryLayer.objects.create(geostory=story, layer=layer, display_order=1)
    assert story.layers.count() == 1
```

**Commit Message**:

```
feat(geostories): add GeoStory and GeoStoryLayer models

Create geostories app with:
- GeoStory: FK to Campaign, OneToOne to GeoContext
- GeoStoryLayer: M2M junction with display_order
- Status enum (draft/published/archived)

Includes Admin registration and model tests.
```

---

### Task 1.2: Campaign API Endpoints (Read-Only/Basic CRUD)

**Branch**: `feat/campaigns-api`

**Description**: Add REST API for Campaign CRUD.

**Changes**:

- [x] Create `serializers.py`, `views.py`, `urls.py` in campaigns app
- [x] Endpoints: `GET/POST /api/v1/campaigns/`, `GET/PATCH /api/v1/campaigns/{id}/`
- [x] Wire to main urls.py

**Acceptance Criteria**:

- [x] API returns 401 for unauthenticated requests
- [x] Authenticated user can create and list campaigns
- [x] Tests pass

**Tests**:

```python
@pytest.mark.django_db
def test_campaign_list_authenticated(api_client, user):
    api_client.force_authenticate(user=user)
    response = api_client.get("/api/v1/campaigns/")
    assert response.status_code == 200
```

**Commit Message**:

```
feat(campaigns): add REST API endpoints

Implement Campaign API:
- GET /api/v1/campaigns/ - list campaigns
- POST /api/v1/campaigns/ - create campaign
- GET /api/v1/campaigns/{id}/ - retrieve
- PATCH /api/v1/campaigns/{id}/ - update

Uses DRF ModelViewSet with authentication.
- **Performance**: Use `CursorPagination` to avoid OFFSET scanning on large datasets.

```

---

### Task 1.2a: API Documentation (Swagger/OpenAPI)

**Branch**: `feat/api-docs`

**Description**: Integrate `drf-spectacular` for auto-generated API documentation.

**Changes**:

- [x] Add `drf-spectacular` to dependencies via `uv add`
- [x] Configure `SPECTACULAR_SETTINGS` in `settings/base.py`
- [x] Add schema and documentation views to `urls.py`
- [x] Verify `/api/schema/`, `/api/docs/` endpoints

**Acceptance Criteria**:

- [x] Swagger UI accessible at `/api/docs/`
- [x] Schema downloads correctly
- [x] Campaign API visible in documentation

**Commit Message**:

```
feat(docs): integrate drf-spectacular

Add OpenAPI 3.0 support via drf-spectacular:
- /api/schema/ (YAML)
- /api/docs/ (Swagger UI)
- /api/redoc/ (Redoc)
```

---

### Task 1.2b: Authentication API Documentation

**Branch**: `feat/auth-docs-fix`

**Description**:
The `authentication` app currently generates warnings in the Swagger/OpenAPI schema generation because custom authentication classes and views are not properly annotated for `drf-spectacular`. This task is to fix those warnings and ensure `KeycloakTokenAuthentication` is properly documented in the schema.

**Issues to Fix**:

1. **Unresolved Authenticator**: `KeycloakTokenAuthentication` is not recognized by `drf-spectacular`.
   - _Fix_: Implement an `OpenApiAuthenticationExtension`.
2. **Missing Serializers**: `test_token_auth` and other function-based views lack serializer definitions.
   - _Fix_: Use `@extend_schema` to define request/response bodies or serializers.

**Implementation Plan**:

1. Create `tosca_api/apps/authentication/schema.py`:
   - Register `KeycloakTokenAuthenticationScheme` extension.
   - Define `Bearer <token>` security scheme.
2. Update `tosca_api/apps/authentication/views.py`:
   - Add `@extend_schema` to `test_token_auth`.
   - Verify other views like `KeycloakLogoutView` or `KeycloakRedirectView` if they are API views.

**Acceptance Criteria**:

- [ ] No warnings in `docker compose logs django` regarding `OpenApiAuthenticationExtension` or `unable to guess serializer`.
- [ ] Swagger UI shows "Authorize" button compatible with Bearer tokens.
- [ ] `test_token_auth` endpoint appears in documentation with correct response schema.

**Commit Message**:

```
feat(auth): fix api documentation warnings

- Register KeycloakTokenAuthentication with drf-spectacular.
- Add schema annotations to authentication views.
```

---

### Task 1.3a: GeoStory Basic CRUD API

**Branch**: `feat/geostories-api-basic`

**Description**: Add REST API for GeoStory core fields (Title, Summary, Status).

**Changes**:

- [x] Create serializers, views, urls in geostories app
- [x] Standard ModelViewSet for GeoStory
- [x] Filter by campaign_id query param

**Acceptance Criteria**:

- [x] Can create/list/update GeoStory objects
- [x] Validation: Title required, Campaign valid
- [x] Tests pass

**Tests**:

```python
@pytest.mark.django_db
def test_geostory_api_creation(api_client, user, campaign):
    api_client.force_authenticate(user=user)
    resp = api_client.post("/api/v1/stories/", {"title": "Story 1", "campaign": campaign.id})
    assert resp.status_code == 201
```

**Commit Message**:

```
feat(geostories): add basic REST API endpoints

Implement GeoStory API:
- GET/POST /api/v1/stories/
- GET/PATCH/DELETE /api/v1/stories/{id}/

Basic CRUD only (no nested writes yet).
```

---

### Task 1.3b: GeoStory Nested Writes (Context & Layers)

**Branch**: `feat/geostories-api-nested`

**Description**: Enable creating/updating GeoContext and Layer links inline with GeoStory.

**Changes**:

- [ ] Update `GeoStorySerializer.create/update` to handle `context` dict
- [ ] Update `GeoStorySerializer.create/update` to handle `layers` list
- [ ] Use atomic transaction for writes

**Acceptance Criteria**:

- [ ] POST includes `context: {"content": "..."}` -> Creates GeoContext
- [ ] POST includes `layers: ["workspace:roads"]` -> Creates GeoStoryLayer links
- [ ] Tests pass for nested creation

**Tests**:

```python
@pytest.mark.django_db
def test_nested_creation_full(api_client, user, campaign, layer):
    payload = {
        "title": "Full Story",
        "campaign": campaign.id,
        "context": {"content": "Rich text here"},
        "layers": [layer.layer_name]
    }
    resp = api_client.post("/api/v1/stories/", payload)
    assert resp.data['context']['content'] == "Rich text here"
    assert resp.data['layers'][0] == layer.layer_name
```

**Commit Message**:

```
feat(geostories): enable nested writes for context and layers

Update GeoStorySerializer to support:
- Inline creation/update of GeoContext
- Inline assignment of Layers via list of names
- Atomic transactions for data integrity
```

---

### Task 1.4a: LayerRef Sync Client

**Branch**: `feat/layerrefs-client`

**Description**: Implement the GeoServer REST client adapter.

**Changes**:

- [ ] Add `requests` dependency
- [ ] Create `tosca_api/apps/layerrefs/client.py`
- [ ] Implement `fetch_layers_from_geoserver()` function

**Acceptance Criteria**:

- [ ] Function returns list of layer names
- [ ] Handles connection errors gracefully
- [ ] Unit tests with mocked responses

**Tests**:

```python
def test_fetch_layers_mocked(mocker):
    m_get = mocker.patch('requests.get')
    m_get.return_value.json.return_value = {'layers': {'layer': [{'name': 'ws:l1'}]}}
    layers = fetch_layers_from_geoserver()
    assert layers == ['ws:l1']
```

**Commit Message**:

```
feat(layerrefs): add GeoServer REST client

Implement HTTP client to fetch layer list from GeoServer:
- Handles basic auth
- Parses JSON response
- Error handling
```

---

### Task 1.4b: LayerRef Sync Endpoint

**Branch**: `feat/layerrefs-endpoint`

**Description**: Add the Admin API endpoint to trigger sync.

**Changes**:

- [ ] Add `POST /api/v1/layers/sync/`
- [ ] Call client, diff sets, create/delete LayerRef objects
- [ ] Return stats

**Acceptance Criteria**:

- [ ] Only Admin/Editor can call
- [ ] Returns added/removed counts
- [ ] Tests integration

**Tests**:

```python
def test_sync_action(api_client, admin_user, mocker):
    mocker.patch('...fetch_layers', return_value=['new_layer'])
    api_client.force_authenticate(admin_user)
    resp = api_client.post("/api/v1/layers/sync/")
    assert resp.data['added'] == 1
```

**Commit Message**:

```
feat(layerrefs): add manual sync endpoint

Add POST /api/v1/layers/sync/:
- Triggers fetch from GeoServer
- Updates local LayerRef table
- Returns sync statistics
```

---

### Task 1.5: Create `featurelinks` App

**Branch**: `feat/featurelinks-app`

**Description**: Create featurelinks app with FeatureLink model (GenericForeignKey).

**Changes**:

- [x] Create `tosca_api/apps/featurelinks/` with FeatureLink model
- [x] Use Django ContentTypes for polymorphic source/target
- [x] Add `clean()` validation for same-campaign and no-self-link

**Acceptance Criteria**:

- [x] Can link GeoStory → GeoStory
- [x] Validation rejects cross-campaign links
- [x] Validation rejects self-links
- [x] Tests pass

**Tests**:

```python
@pytest.mark.django_db
def test_featurelink_same_campaign():
    # Can link two stories in same campaign
    ...

@pytest.mark.django_db
def test_featurelink_rejects_cross_campaign():
    # Should raise ValidationError
    ...

@pytest.mark.django_db
def test_featurelink_rejects_self_link():
    # Should raise ValidationError
    ...
```

**Commit Message**:

```
feat(featurelinks): add FeatureLink model

Create featurelinks app for polymorphic entity linking:
- GenericForeignKey for source and target
- campaign FK for boundary enforcement
- link_type enum (direct/read_more/action)

Includes clean() validation:
- Source and target must be in same campaign
- Entity cannot link to itself
```

---

### Task 1.6: Enhanced GeoStory API (Read-Optimized)

**Branch**: `feat/geostory-read-api`

**Description**:
Enhance `GeoStory` API to support public consumption with list/detail separation, nested serialization, and status filtering.

**Changes**:

- [x] Create `GeoContextSerializer` to expose fields `content`, `content_type`
- [x] Create `LayerRefSerializer` to expose `id`, `layer_name`
- [x] Create `FeatureLinkSerializer` for outgoing links (source -> target)
- [x] Update `GeoStoryViewSet`:
  - [x] `list` action: Filter `status=PUBLISHED` (for public users). Returns slim payload.
  - [x] `retrieve` action: Returns full nested payload.
  - [x] Optimize queries (`select_related`, `prefetch_related`)
- [x] Define dual serializers in `views.py` (`get_serializer_class`)

**API Specs**:

1. **List API** (`GET /api/v1/geostories/`):
   - **Filter**: `status='published'`, `campaign_id=<uuid>`
   - **Pagination**: Inherit default (Cursor)
   - **Fields**: `id`, `title`, `summary`, `campaign_id`, `created_at`

2. **Detail API** (`GET /api/v1/geostories/<uuid>/`):
   - **Fields**:
     - `id`, `title`, `summary`, `status`
     - `campaign_id`
     - `context`: `{ content: "...", content_type: "simple" }`
     - `layers`: `[{ id: "...", layer_name: "...", display_order: 1 }]` (Ordered)
     - `feature_links`: `[{ id: "...", target_object_id: "...", link_type: "exact" }]` (Outgoing)
     - `created_at`, `updated_at`

**Acceptance Criteria**:

- [x] List endpoint returns only published stories (unless admin)
- [x] List endpoint returns minimal fields
- [x] Detail endpoint returns nested Context object (not just UUID)
- [x] Detail endpoint returns list of LayerRef objects with order
- [x] Detail endpoint returns outgoing FeatureLinks
- [x] Tests verify payload structure and filtering

**Tests**:

```python
def test_geostory_list_published_only(api_client):
    # Create draft and published stories
    # Verify response contains only published
    ...

def test_geostory_detail_payload(api_client):
    # Verify context, layers, links are present
    ...
```

**Commit Message**:

```
feat(geostories): enhance API for read consumption

- Add GeoStoryListSerializer (slim) and GeoStoryDetailSerializer (nested).
- Implement nested serializers for GeoContext, LayerRef, FeatureLink.
- Update GeoStoryViewSet to use dual serializers and optimize queries.
- Enforce status=PUBLISHED filtering for list view.
```

---

## Phase 2: CalendarEvent Feature

> **Goal**: Add time-bound spatial events.

---

### Task 2.1: Create `events` App with CalendarEvent Model

**Branch**: `feat/events-model`

**Description**: Create events app with CalendarEvent model using PointField.

**Changes**:

- [x] Create `tosca_api/apps/events/` with CalendarEvent, EventLayer models
- [x] Fields include: location (PointField), start_datetime, end_datetime
- [x] Add CHECK constraint for end >= start

**Acceptance Criteria**:

- [x] PointField migration applies successfully
- [x] Can save model with SRID 4326 point
- [x] IntegrityError if end < start

**Tests**:

```python
@pytest.mark.django_db
def test_event_constraints():
    # Should fail if end < start
    with pytest.raises(IntegrityError):
        CalendarEvent.objects.create(..., start=now, end=now-timedelta(1))
```

**Commit Message**:

```
feat(events): add CalendarEvent model

Create events app with:
- CalendarEvent: FK to Campaign, PointField for location
- EventLayer: M2M junction with display_order
- start_datetime, end_datetime with validation

Uses GeoDjango PointField for spatial storage.
```

---

### Task 2.2: CalendarEvent API Endpoints

**Branch**: `feat/events-api`

**Description**: Add REST API for CalendarEvent CRUD with spatial filtering support.

**Changes**:

- [x] Create serializers, views, urls
- [x] Implement `GeoFeatureModelSerializer` for GeoJSON output
- [x] Filter by campaign_id, start_date, end_date
- [x] Add `include_past` filter (default: false, only future events)
- [x] Add `bbox` parameter for spatial filtering
- [x] Add `POST /events/within/` for polygon-based spatial filter

**Acceptance Criteria**:

- [x] GET /api/v1/events/ returns paginated JSON (calendar view)
- [x] GET /api/v1/events/?bbox=... returns GeoJSON FeatureCollection (map view)
- [x] POST /api/v1/events/within/ returns events inside polygon
- [x] Can filter: ?start_after, ?start_before, ?include_past
- [x] Map view only returns events WITH location
- [x] CursorPagination enabled for calendar view

**Tests**:

```python
@pytest.mark.django_db
def test_event_geojson_output(api_client):
    resp = api_client.get("/api/v1/events/?bbox=9,53,11,54")
    assert resp.data['type'] == "FeatureCollection"
    assert resp.data['features'][0]['geometry']['type'] == "Point"
```

**Commit Message**:

```
feat(events): add REST API endpoints with spatial filtering

Implement CalendarEvent API:
- GET /api/v1/events/ (calendar view, paginated JSON)
- GET /api/v1/events/?bbox=... (map view, GeoJSON)
- POST /api/v1/events/within/ (polygon filter)
- Full CRUD support

Features:
- Default: future events only (include_past=false)
- BBox and polygon spatial filters
- GeoJSON FeatureCollection output for map views
- CursorPagination for calendar view
```

---

### Task 2.3: Extend FeatureLink for Events

**Branch**: `feat/featurelinks-events`

**Description**: Verify FeatureLink works with CalendarEvent content type.

**Changes**:

- [x] Add tests for Story → Event links
- [x] Add tests for Event → Event links

**Acceptance Criteria**:

- [x] Tests pass for linking events to other features
- [x] Admin UI allows selecting events in generic relation

**Tests**:

```python
@pytest.mark.django_db
def test_link_story_to_event(story, event):
    link = FeatureLink.objects.create(
        campaign=story.campaign,
        source=story,
        target=event
    )
    assert link.id is not None
```

**Commit Message**:

```
test(featurelinks): verify CalendarEvent linking

Add tests to confirm FeatureLink supports:
- GeoStory → CalendarEvent links
- CalendarEvent → CalendarEvent links

No model changes required (GenericFK handles it).
```

---

## Phase 2B: Event Model V2

> **Goal**: Evolve the existing event implementation into a flexible, typed, recurring-capable event platform while preserving the current event authoring and map/list experience.

### Implementation Notes After 2B.4

- `2B.2` depended on `event_type` and `series` before the original plan introduced the full `EventType` and `EventSeries` schemas. The codebase now already contains minimal placeholder `EventType` and `EventSeries` models, and later tasks must extend them in place instead of creating replacements.
- `Event.context` is no longer 1:1. It is a nullable `ForeignKey` override so contexts can be reused across occurrences when appropriate.
- Effective context resolution is already implemented in code as `event.context -> series.default_context -> none`, and the event detail API returns the resolved context.
- `2B.4` satisfies the GiST requirement through GeoDjango's spatial index on `Event.location`; no duplicate explicit GiST index was added.
- `EventSeries.campaign` and `EventSeries.event_type` are currently nullable at the DB layer for destructive-reset-friendly development, but any event attached to a series is validated against populated series values.
- Local tests use a persistent `test_tosca` database with `--reuse-db`. After destructive event-schema changes, reset or repair the reusable test DB before trusting failures that mention old event tables or content types.

---

### Task 2B.1: Replace `CalendarEvent` With `Event`

**Branch**: `feat/event-v2-rename`

**Description**: Replace `CalendarEvent` with `Event` after schema freeze and update all direct dependencies in one sweep.

**Changes**:

- [x] Replace the Django model name and ORM references
- [x] Update `FeatureLink` allowed content types and generic relation names
- [x] Update serializers, viewsets, admin, tests, and docs that still reference `CalendarEvent`
- [x] Use a pre-production destructive schema reset strategy; no row backfill required

**Acceptance Criteria**:

- [x] No `CalendarEvent` references remain in the event app or `FeatureLink`
- [x] Event CRUD still works under the new `Event` model name
- [x] Tests covering event creation and linking pass

**Test Cases**:

- [x] Event can be created and retrieved through the renamed model/API path
- [x] `FeatureLink` can link `Event` to `GeoStory`, `GeoFeedback`, and another `Event`
- [x] Old `CalendarEvent` content-type references are removed or rejected
- [x] Admin and serializer layers resolve the renamed model correctly

---

### Task 2B.2: Add Event Core Fields and Mode Validation

**Branch**: `feat/event-v2-core-fields`

**Description**: Add the new core fields required for online, hybrid, and provider-aware events.

**Changes**:

- [x] Add `event_type`, `location_mode`, online fields, provider fields, `series`, `occurrence_index`, `is_exception`, and `original_start_datetime`
- [x] Implement validation for `physical`, `hybrid`, and `online`
- [x] Keep `Event.start_datetime` and `Event.end_datetime` as `TIMESTAMPTZ`
- [x] Keep event context optional

**Implementation Notes**:

- `EventType` and `EventSeries` were introduced as minimal placeholders here because the original task order referenced them before `2B.7` and `2B.12`.
- Online access validation currently accepts either `online_url` or `online_platform` as sufficient access data.

**Acceptance Criteria**:

- [x] Online events allow `location=NULL` and require online access data
- [x] Hybrid events require both geometry and online access data
- [x] Standalone events can exist without context
- [x] Tests for mode validation pass

**Test Cases**:

- [x] `physical` event requires geometry and does not require online fields
- [x] `online` event requires online URL/platform and allows null geometry
- [x] `hybrid` event requires both geometry and online URL/platform
- [x] `end_datetime < start_datetime` is rejected
- [x] Standalone event with null context is valid

---

### Task 2B.3: Add Series Default Context and Event Overrides

**Branch**: `feat/event-v2-context`

**Description**: Implement the final shared-content model using series defaults plus per-occurrence overrides.

**Changes**:

- [x] Add `EventSeries.default_context`
- [x] Keep `Event.context` as an optional override
- [x] Document and implement effective context resolution as `event.context -> series.default_context -> none`
- [x] Ensure events without effective context are valid

**Implementation Notes**:

- `Event.context` was changed from `OneToOneField` to `ForeignKey` so occurrence overrides can coexist with shared defaults.
- The current detail serializer returns the resolved effective context, not only the direct override row.

**Acceptance Criteria**:

- [x] Series events can resolve shared context from `EventSeries.default_context`
- [x] Individual occurrences can override shared context through `Event.context`
- [x] Published events are valid without any effective context
- [x] Tests for context resolution pass

**Test Cases**:

- [x] Event without series and without context resolves to no context
- [x] Event with series default context and no override resolves series context
- [x] Event with both series default and event override resolves the event override
- [x] Editing one occurrence override does not change the series default context
- [x] Published event without any effective context remains valid

---

### Task 2B.4: Add Core Constraints and Indexes

**Branch**: `feat/event-v2-core-constraints`

**Description**: Add the minimum production-ready constraints and indexes for event core queries.

**Changes**:

- [x] Add event constraints for `end_datetime >= start_datetime`
- [x] Add series-linked invariants for `campaign` and `event_type`
- [x] Add GiST index on `location`
- [x] Add b-tree indexes on `(campaign_id, status, start_datetime)`, `(event_type_id, start_datetime)`, `(location_mode, start_datetime)`, `(series_id, start_datetime)`
- [x] Add unique partial index on `(series_id, occurrence_index)` where `series_id IS NOT NULL`

**Implementation Notes**:

- The GiST requirement is satisfied by GeoDjango's spatial index on `Event.location`; no redundant explicit GiST index was added.
- `EventSeries` now carries `campaign` and `event_type` so attached events can be validated against the series identity before the full recurrence schema lands.
- `EventSeries.campaign` and `EventSeries.event_type` remain nullable in the DB schema for destructive-reset development, but series-linked event validation requires them to be populated.

**Acceptance Criteria**:

- [x] Series-linked events cannot change `campaign` or `event_type` while attached
- [x] Duplicate `(series_id, occurrence_index)` values are rejected
- [x] Schema contains the agreed minimum index set
- [x] Constraint tests pass

**Test Cases**:

- [x] `end_datetime >= start_datetime` constraint accepts valid rows
- [x] `end_datetime < start_datetime` constraint rejects invalid rows
- [x] Series-linked event rejects `campaign` change while attached
- [x] Series-linked event rejects `event_type` change while attached
- [x] Duplicate `(series_id, occurrence_index)` values are rejected when `series_id` is set
- [x] Expected GiST and b-tree indexes are present in the schema

---

### Task 2B.5: Add Taxonomy Schema and Admin

**Branch**: `feat/event-v2-taxonomy-schema`

**Description**: Add dynamic taxonomy models and admin configuration.

**Changes**:

- [x] Create `TaxonomyDimension`, `TaxonomyTerm`, and `EventTerm`
- [x] Add support for `single` vs `multiple` selection modes
- [x] Support parent-child terms inside a dimension
- [x] Add admin configuration for dimensions and terms
- [x] Add unique `TaxonomyTerm(dimension_id, code)` and unique `EventTerm(event_id, term_id)`

**Additional Todo For Next Task**:

- Reuse the existing `EventType` and `EventSeries` models already present in the app. Do not introduce replacement models while adding taxonomy support.
- Reuse the newly added `EventTerm` table and layer `2B.6` assignment validation on top of it instead of introducing a second event-taxonomy relation path.

**Implementation Notes**:

- `TaxonomyDimension` now owns the `single` vs `multiple` selection mode contract, but enforcement of single-select assignment conflicts is intentionally deferred to `2B.6`.
- Admin configuration now exists for `TaxonomyDimension`, `TaxonomyTerm`, and `EventTerm`, with inline term management under dimensions.

**Acceptance Criteria**:

- [x] Admin can create dimensions and terms
- [x] Parent term validation rejects cross-dimension hierarchies
- [x] Duplicate term assignment is rejected
- [x] Schema and admin tests pass

**Test Cases**:

- [x] Admin can create active and inactive taxonomy dimensions
- [x] Term codes are unique within a dimension
- [x] Parent term must belong to the same dimension as the child
- [x] Duplicate `(event_id, term_id)` assignment is rejected
- [x] Admin lists and forms expose the taxonomy models correctly

---

### Task 2B.6: Add Event-Taxonomy Assignment Rules

**Branch**: `feat/event-v2-taxonomy-assignment`

**Description**: Implement assignment-time validation and query support for event taxonomy.

**Changes**:

- [x] Enforce single-select dimension rules on `EventTerm`
- [x] Add index `EventTerm(term_id, event_id)` for filtering
- [x] Add serializer/admin validation for event-term assignment
- [x] Add API/query support for filtering by term and dimension

**Implementation Notes**:

- `EventTerm` already exists from `2B.5`; `2B.6` should add validation, filtering index, and assignment entry-point checks on that existing table.
- The current API contract now supports `term_id` and `dimension_id` filters on the existing event endpoints.
- Event write operations now accept `taxonomy_term_ids` and replace the event's assignments when that field is provided.

**Additional Todo For Next Task**:

- `2B.9` should lift the existing `term_id` and `dimension_id` semantics into the shared event filter layer instead of redefining taxonomy filter names.

**Acceptance Criteria**:

- [x] Single-select dimensions reject conflicting assignments
- [x] Event-term assignment works from admin and API
- [x] Event filtering by taxonomy terms works
- [x] Assignment tests pass

**Test Cases**:

- [x] Single-select dimension rejects a second term for the same event
- [x] Multiple-select dimension allows multiple terms for the same event
- [x] Event-term assignment works through serializer validation
- [x] Event-term assignment works through admin save flow
- [x] Filtering by term returns matching events only
- [x] Filtering by dimension/term combination returns the expected event set

---

### Task 2B.7: Add Event Type Registry and Seeds

**Branch**: `feat/event-v2-types`

**Description**: Introduce the `EventType` registry as the source of truth for event-type behavior.

**Changes**:

- [x] Add `EventType` with `code`, `label`, `profile_mode`, `profile_key`, and `is_active`
- [x] Seed `general`, `public_health`, `sports`, and `culture`
- [x] Add admin support for event type management

**Additional Todo**:

- Extend the existing placeholder `EventType` model in place.
- Because event data can be reset destructively, reshaping the existing placeholder table is preferable to building compatibility-heavy backfills.
- `2B.8` should treat the seeded `general`, `public_health`, `sports`, and `culture` rows as the canonical registry bindings for profile validation.

**Implementation Notes**:

- `EventType` now validates `profile_mode=core` with no `profile_key`, and `profile_mode=extension` with a required `profile_key`.
- The seed migration populates `general=core`, and `public_health` / `sports` / `culture` as `extension` rows with matching `profile_key` values.

**Acceptance Criteria**:

- [x] Event types can be created and managed in admin
- [x] Seeded event types exist and match the agreed profile bindings
- [x] Tests for registry creation and seed data pass

**Test Cases**:

- [x] Seed creates `general`, `public_health`, `sports`, and `culture`
- [x] Seeded rows use the correct `profile_mode` / `profile_key` combinations
- [x] Duplicate event type codes are rejected
- [x] Inactive event types can be stored without breaking existing rows
- [x] Admin can create and edit custom event types

---

### Task 2B.8: Add Profile Extension Models and Compatibility Validation

**Branch**: `feat/event-v2-profiles`

**Description**: Add extension profile tables and validate them against `EventType`.

**Changes**:

- [x] Add `PublicHealthEventProfile`, `SportsEventProfile`, and `CultureEventProfile`
- [x] Validate `profile_mode=core` vs `profile_mode=extension`
- [x] Reject mismatched event-type/profile combinations

**Implementation Notes**:

- `2B.7` already established the registry contract: `general` is `core`, while `public_health`, `sports`, and `culture` are `extension` types with matching `profile_key` values.
- The profile tables now validate against `event.event_type.profile_mode` and `event.event_type.profile_key` rather than hardcoded type-name checks scattered across the app.
- Core event types still do not require any extension profile row, and extension event types are not yet forced to have one.

**Acceptance Criteria**:

- [x] Core event types work without extension rows
- [x] Extension types reject mismatched profile rows
- [x] Profile compatibility tests pass

**Test Cases**:

- [x] `general` event can exist without any extension profile
- [x] `public_health` event accepts `PublicHealthEventProfile`
- [x] `sports` event accepts `SportsEventProfile`
- [x] `culture` event accepts `CultureEventProfile`
- [x] Core event type rejects creation of any extension profile
- [x] Mismatched event-type/profile combinations are rejected

---

### Task 2B.9: Add Shared Event Filter Layer

**Branch**: `feat/event-v2-filters`

**Description**: Centralize event filtering so list and map endpoints share one contract.

**Changes**:

- [x] Add shared parsing/validation for campaign, date range, status, visibility, taxonomy, `include_past`, and spatial filters
- [x] Make spatial predicates apply only to `physical` and `hybrid`
- [x] Keep `online` events subject to all non-spatial filters

**Implementation Notes**:

- Taxonomy filtering already exists on the current event endpoints via `term_id` and `dimension_id`; `2B.9` should preserve those semantics while centralizing the logic.
- The shared filter layer now lives in a dedicated events filter helper and is used by both list and polygon/bbox spatial queries.
- Current spatial responses still serialize through the existing GeoJSON endpoint shape, so eligible online events now surface as GeoJSON features with `null` geometry until `2B.11` introduces separate spatial and online buckets.

**Additional Todo For Next Task**:

- `2B.10` and `2B.11` should reuse the existing shared filter helper and preserve the current `campaign_id`, `status`, `visibility`, `include_past`, `start_after`, `start_before`, `term_id`, and `dimension_id` filter contract.

**Acceptance Criteria**:

- [x] List and map endpoints can reuse the same filter layer
- [x] Spatial filters exclude out-of-area `physical`/`hybrid` events
- [x] Eligible `online` events remain included after non-spatial filtering
- [x] Filter-layer tests pass

**Test Cases**:

- [x] Campaign, status, visibility, and date filters produce the same result set for list and map queries
- [x] `include_past=false` excludes past events across both list and map
- [x] BBox/polygon filtering removes out-of-area `physical` and `hybrid` events
- [x] Eligible `online` events remain after spatial filtering
- [x] Non-spatial filters still remove `online` events when they do not match
- [x] Taxonomy filters apply consistently in the shared filter layer

---

### Task 2B.10: Add Event List API V2

**Branch**: `feat/event-v2-list-api`

**Description**: Build the list endpoint as one chronological mixed stream using `location_mode`.

**Changes**:

- [x] Add `GET /api/v1/events/list/`
- [x] Return one paginated stream ordered by `start_datetime`
- [x] Include `location_mode` in each item
- [x] Reuse the shared event filter layer

**Implementation Notes**:

- The new dedicated list endpoint now returns paginated JSON even when spatial filters like `bbox` are supplied.
- The legacy `/api/v1/events/` route still exists for backward compatibility and still switches to GeoJSON on bbox requests; `2B.11` should target the dedicated map surface instead.

**Additional Todo For Next Task**:

- `2B.11` should reuse the shared filter helper and keep `/api/v1/events/list/` as the canonical mixed-stream list endpoint while introducing the separate map buckets.

**Acceptance Criteria**:

- [x] List endpoint returns `physical`, `hybrid`, and `online` events in one chronological stream
- [x] Filtering semantics match the shared contract
- [x] Pagination works as expected
- [x] List API tests pass

**Test Cases**:

- [x] List endpoint returns mixed `physical`, `hybrid`, and `online` events ordered by `start_datetime`
- [x] Each item includes `location_mode`
- [x] Pagination cursors/page boundaries work with mixed event types
- [x] Area-filtered list includes matching spatial events plus eligible online events
- [x] List payload remains stable when no online events match

---

### Task 2B.11: Add Event Map API V2

**Branch**: `feat/event-v2-map-api`

**Description**: Build the map endpoint with separate spatial and online result buckets.

**Changes**:

- [x] Add `GET /api/v1/events/map/`
- [x] Return `spatial_events` as GeoJSON for `physical` and `hybrid`
- [x] Return `online_events` as a separate JSON array
- [x] Reuse the shared event filter layer

**Implementation Notes**:

- The dedicated map endpoint now owns the bucketed v2 map response contract, while the legacy `/api/v1/events/` bbox behavior remains in place for backward compatibility.
- `spatial_events` includes only mapped `physical` and `hybrid` events, and `online_events` contains the eligible `online` events that passed the same shared non-spatial filters.

**Acceptance Criteria**:

- [x] Map endpoint returns valid GeoJSON in `spatial_events`
- [x] Map endpoint returns `online_events` separately from spatial GeoJSON
- [x] Spatial filtering behavior matches the agreed semantics
- [x] Map API tests pass

**Test Cases**:

- [x] Map endpoint returns valid GeoJSON FeatureCollection in `spatial_events`
- [x] `online_events` are returned as a separate JSON array
- [x] `spatial_events` contains only `physical` and `hybrid` events with geometry
- [x] Area-filtered map response includes eligible online events separately
- [x] Empty spatial result sets still return a valid GeoJSON structure

---

### Task 2B.12: Add EventSeries and EventSeriesDate Schema

**Branch**: `feat/event-v2-series-schema`

**Description**: Add the structural models required for batch and recurring event generation.

**Changes**:

- [x] Create `EventSeries`
- [x] Create `EventSeriesDate`
- [x] Add unique `EventSeriesDate(series_id, occurrence_date)` constraint
- [x] Add recurrence field validation for daily, weekly, and monthly rules

**Additional Todo**:

- Extend the existing `EventSeries` table in place rather than introducing a new grouping model.
- After the destructive reset for the new event system, revisit whether `EventSeries.campaign` and `EventSeries.event_type` should be tightened from nullable to non-nullable at the schema level.
- `2B.13` should build on the live `EventSeries` / `EventSeriesDate` schema instead of introducing a second recurrence-definition path.

**Implementation Notes**:

- The existing placeholder `EventSeries` model is now the real recurrence-definition table and includes recurrence, schedule, timezone, and creator fields.
- `EventSeries.campaign` and `EventSeries.event_type` remain nullable at the DB layer as previously documented, but the new recurrence fields are validated strictly at the model layer.
- `manual_batch` explicit dates now persist through `EventSeriesDate`, while the "series must already have at least one date" rule remains deferred to the creation flow in `2B.13`.

**Acceptance Criteria**:

- [x] Manual batch series can persist explicit dates
- [x] Weekly/monthly validation rules are enforced
- [x] Duplicate manual batch dates are rejected
- [x] Series schema tests pass

**Test Cases**:

- [x] `manual_batch` series accepts explicit dates and persists them in `EventSeriesDate`
- [x] Duplicate `(series_id, occurrence_date)` rows are rejected
- [x] Weekly recurrence requires at least one weekday
- [x] Monthly recurrence requires either `day_of_month` or `week_of_month + weekday_of_month`
- [x] Invalid combinations of `end_date` and `occurrence_count` are rejected

---

### Task 2B.13: Add Recurrence Generation, Exceptions, and DST Handling

**Branch**: `feat/event-v2-series-generation`

**Description**: Implement generation, exception behavior, and local-time recurrence semantics.

**Changes**:

- [x] Add preview and creation flow for manual and recurring series
- [x] Persist generated events with `series_id`, `occurrence_index`, and `original_start_datetime`
- [x] Mark diverging occurrences as `is_exception`
- [x] Update only future non-exception occurrences by default
- [x] Generate occurrences in local wall time using `EventSeries.timezone`

**Implementation Notes**:

- `2B.12` already added the concrete `EventSeries` and `EventSeriesDate` schema; `2B.13` should focus on generation services and exception behavior on top of that schema.
- Implemented dedicated `POST /api/v1/event-series/preview/`, `POST /api/v1/event-series/`, and `PATCH /api/v1/event-series/{id}/` endpoints rather than overloading the existing event CRUD routes with bulk recurrence semantics.
- Generated occurrences are synchronized by `occurrence_index`; future non-exception rows are updated or deleted by default, while exception rows are skipped and left attached to the series.
- Direct edits to generated occurrences through the existing `/api/v1/events/{id}/` endpoint now mark the row as `is_exception=True` when schedule/content/location or taxonomy fields diverge.
- Recurring generation is currently limited to same-day occurrence durations because the existing `EventSeries.end_date` field is already used as the recurrence termination boundary. Multi-day recurring occurrence duration still needs explicit schema support if required later.
- Series `campaign` and `event_type` changes are now rejected once occurrences exist, because exceptions must remain aligned with the series identity while attached.

**Acceptance Criteria**:

- [x] Recurring weekly series generate correct occurrences
- [x] Weekly recurring series preserve the same local wall time across DST boundaries
- [x] Exception occurrences are skipped by default during future bulk updates
- [x] Generation and exception-handling tests pass

**Test Cases**:

- [x] Manual batch preview returns one occurrence per explicit date
- [x] Weekly recurring preview/generation returns the expected count and dates
- [x] Generated occurrences receive `series_id`, `occurrence_index`, and `original_start_datetime`
- [x] Editing one generated occurrence can mark it as `is_exception`
- [x] Bulk future updates skip exception occurrences by default
- [x] Weekly recurrence across DST preserves the same local clock time in the series timezone

---

### Task 2B.14: Review Cleanup for Event Filters and Validation

**Branch**: `feat/event-v2-review-cleanups`

**Description**: Apply the concrete follow-up improvements identified during the Phase 2B implementation review.

**Changes**:

- [x] Verify and document that the `2B.7` event type seed migration already exists and runs as a `RunPython` data migration
- [x] Convert `EventLayer` uniqueness from `unique_together` to `UniqueConstraint`
- [x] Move `VALID_WEEKDAYS` above `EventSeries` for readability
- [x] Add serializer-level GeoJSON `Point` validation for event-series template locations
- [x] Auto-assign `EventSeriesDate.display_order` when omitted during direct model creation
- [x] Add `event_type_id` to the shared event filter layer used by list/map/within endpoints
- [x] Avoid unnecessary FK object comparison when deciding whether a direct occurrence edit should become an exception

**Implementation Notes**:

- This cleanup task intentionally covers only the review items that are concrete, local to the events app, and low ambiguity.
- It does not introduce role-based permissions yet; that remains a broader cross-app authorization task rather than an events-only cleanup.
- The `2B.7` event type seeds are confirmed to live in `events/migrations/0007_eventtype_is_active_eventtype_profile_key_and_more.py` via `RunPython(seed_event_types, unseed_event_types)`.
- Queryset deletion during occurrence sync remains unchanged because `Event` still has no custom `delete()` behavior to preserve.

**Acceptance Criteria**:

- [x] Event-series preview/create returns clear 400 errors for malformed or non-point location GeoJSON
- [x] Shared event filtering supports `event_type_id` on list and map endpoints
- [x] `EventLayer` uniqueness uses a schema-level `UniqueConstraint`
- [x] `EventSeriesDate` can be created without manually supplying `display_order`
- [x] Cleanup tests pass

**Test Cases**:

- [x] Event-series preview rejects invalid `location` GeoJSON with a clear serializer error
- [x] Legacy list filtering works with `event_type_id`
- [x] Map v2 filtering works with `event_type_id`
- [x] `EventSeriesDate` auto-assigns display order when omitted
- [x] Existing duplicate `EventLayer(event, layer)` rejection still passes after the constraint migration

---

### Task 2B.15: Add Admin Event-Series Authoring and Occurrence Generation

**Branch**: `feat/event-series-admin-generation`

**Description**: Extend Django admin so admins can author full event-series definitions, including occurrence template data, and generate or synchronize occurrence events with the same semantics as the existing recurrence API.

**Changes**:

- [x] Extend `EventSeriesAdminForm` to capture the event-template fields required to create valid `Event` rows
- [x] Add admin support for taxonomy term selection that applies to generated occurrences
- [x] Introduce a shared orchestration path for series validation, explicit-date persistence, and occurrence generation/synchronization that can be called from both admin and API
- [x] Update the admin save flow so manual-batch generation runs only after inline `EventSeriesDate` formsets have been saved
- [x] Generate occurrences when admins create a new series from admin
- [x] Synchronize future non-exception occurrences when admins edit an existing generated series from admin
- [x] Preserve existing exception occurrences during admin bulk updates by default
- [x] Preserve the existing restriction that `campaign` and `event_type` cannot be changed after occurrences exist
- [x] Fail admin saves transactionally if series validation, template validation, taxonomy validation, or event generation fails
- [x] Add clear admin-facing validation errors for legacy series that have no base occurrence/template to derive update defaults from
- [x] Prevent duplicate occurrences when a generated series is re-saved from admin

**Implementation Notes**:

- `2B.13` implemented recurrence generation only through `EventSeriesWriteSerializer` and the `/api/v1/event-series/` endpoints.
- The current admin path saves only the lightweight `EventSeries` model plus inline `EventSeriesDate` rows; it never calls `create_occurrence_events()` or `sync_occurrence_events()`.
- The implementation should not move generation into `EventSeries.save()`, signal handlers, or arbitrary `EventSeries.objects.create()` usage. Generation should remain tied to explicit authoring flows.
- Because `EventSeries` does not persist fields like `title` or `location_mode`, admin edit behavior for an existing series must either:
  - derive a base template from the first non-exception occurrence, matching the API update pattern
  - or require admins to re-enter the missing template fields before sync
- Admin orchestration should run after inline formsets are available so manual-batch series can generate from the saved explicit dates instead of stale or missing values.
- Validation parity matters more than implementation location. If the serializer is too tightly coupled to request objects, extract the shared series-authoring logic into a dedicated service and make the serializer/admin thin wrappers around it.
- Existing recurrence behavior should remain unchanged:
  - future non-exception rows are updated or deleted during sync
  - future exception rows are skipped by default
  - past rows are left as historical records
  - local wall time in `EventSeries.timezone` remains authoritative across DST transitions

**Edge Cases**:

- [x] Manual-batch admin create without any inline `EventSeriesDate`
- [x] Manual-batch admin update that removes one explicit date and adds another
- [x] Recurring admin create with `weekly` recurrence but empty `by_weekday`
- [x] Recurring admin create/update with invalid monthly rule combinations
- [x] Online template missing both `online_url` and `online_platform`
- [x] Physical template with missing geometry
- [x] Online template incorrectly containing geometry
- [x] Invalid GeoJSON or non-`Point` location entered through admin
- [x] Admin update after one occurrence was manually edited into an exception
- [x] Admin update that shortens the series and should delete only future non-exception occurrences
- [x] Admin update that changes timezone or start time across a DST boundary
- [x] Admin re-save of an API-created series without introducing duplicate occurrences
- [x] Admin update of a legacy series that has no non-exception occurrence available as a base template
- [x] Taxonomy term selections violating single-select dimension rules
- [x] Transaction rollback when one generated occurrence fails validation

**Acceptance Criteria**:

- [x] Admin add-form can create a recurring series and generate all expected occurrence events
- [x] Admin add-form can create a manual-batch series from inline explicit dates and generate one event per date
- [x] Admin change-form can synchronize an existing generated series without duplicating occurrences
- [x] Admin bulk edits preserve future exception occurrences by default
- [x] Admin validation matches API validation for recurrence rules, event-template rules, location GeoJSON, and taxonomy terms
- [x] Admin errors are actionable and prevent partial persistence
- [x] Automated tests cover both create and update behavior from the admin path

**Test Cases**:

- [x] Admin recurring create generates events with `series_id`, `occurrence_index`, and `original_start_datetime`
- [x] Admin manual-batch create generates one event per inline explicit date in display order
- [x] Admin create rolls back the saved series if template validation fails after inline dates are submitted
- [x] Admin update changes future non-exception titles/status/location fields across generated events
- [x] Admin update leaves an edited exception occurrence untouched
- [x] Admin update deleting one future occurrence removes only the matching non-exception row
- [x] Admin update across DST preserves the same local clock time in the configured timezone
- [x] Admin update of an API-created series does not create duplicate `occurrence_index` values
- [x] Admin validation rejects invalid location GeoJSON with a clear form error
- [x] Admin validation rejects missing required template fields such as `title` and `location_mode`
- [x] Admin validation rejects invalid taxonomy combinations
- [x] Admin update of a series with no base occurrence/template fails clearly or requires explicit replacement fields, per chosen implementation

---

### Task 2B.16: Replace Term-Centric Event Taxonomy Writes with Dimension Assignments

**Branch**: `feat/event-taxonomy-dimension-assignments`

**Description**: Refactor event and event-series taxonomy authoring away from raw `taxonomy_term_ids` lists and onto a grouped `taxonomy_assignments` contract that matches the product model of taxonomy as optional event attributes.

**Changes**:

- [x] Introduce a shared taxonomy-assignment parser/validator that accepts grouped assignments by dimension
- [x] Remove `taxonomy_term_ids` from event and event-series write serializers; no backward-compatibility layer is required
- [x] Add validation that assigned terms:
  - belong to the stated dimension
  - are active
  - are leaf terms only
  - satisfy `single` vs `multiple` dimension rules
- [x] Keep taxonomy optional for all event statuses, including `published`
- [x] Continue persisting assignments through `EventTerm` without changing the storage model
- [x] Reuse the same taxonomy-assignment contract from both direct event writes and shared series-authoring flows
- [x] Add clear serializer/service errors for malformed grouped assignments and invalid term/dimension combinations

**Implementation Notes**:

- `TaxonomyDimension` remains the attribute definition, `TaxonomyTerm` remains the selectable vocabulary, and `EventTerm` remains the internal persistence layer.
- This task intentionally changes the API contract because taxonomy authoring is not yet a published feature.
- The grouped write shape should be explicit about dimension ownership, for example:
  - `taxonomy_assignments: [{dimension_id, term_ids[]}]`
- Validation should reject assignments to inactive dimensions or inactive terms for new writes while still allowing older data to remain stored.
- The shared validator should be reusable from serializers, admin forms, and any orchestration service extracted by `2B.15`.
- Event and event-series writes now resolve grouped assignments through one shared service-layer validator instead of duplicating taxonomy checks inside each serializer.

**Edge Cases**:

- [x] Empty or omitted `taxonomy_assignments`
- [x] Duplicate `dimension_id` entries in the same payload
- [x] Duplicate term IDs within the same dimension
- [x] A term assigned under the wrong dimension
- [x] Multiple terms supplied for a `single` dimension
- [x] Parent and child terms both supplied for the same dimension
- [x] Inactive dimension or inactive term supplied in a new write payload
- [x] Unknown dimension IDs or term IDs

**Acceptance Criteria**:

- [x] Event writes accept only the grouped taxonomy-assignment contract
- [x] Event-series writes accept the same grouped taxonomy-assignment contract
- [x] Taxonomy remains optional for draft and published events
- [x] Leaf-only assignment and selection-mode rules are enforced consistently
- [x] Existing `EventTerm` persistence remains the only storage layer for event taxonomy
- [x] Automated tests cover grouped assignment validation on both event and event-series write paths

**Test Cases**:

- [x] Event create with empty taxonomy assignments succeeds
- [x] Event create with one single-select dimension and one multi-select dimension succeeds
- [x] Event create rejects duplicate dimension entries
- [x] Event create rejects non-leaf term assignment
- [x] Event create rejects inactive term assignment
- [x] Event-series create accepts grouped taxonomy assignments and persists them onto generated occurrences
- [x] Event-series update rejects invalid grouped taxonomy combinations with clear errors

---

### Task 2B.17: Add Dimension-Based Taxonomy Hydration for Event and Series Authoring

**Branch**: `feat/event-taxonomy-authoring-hydration`

**Description**: Expose taxonomy as grouped, dimension-based authoring data across event detail, event-series retrieve, and admin authoring flows so taxonomy behaves like optional event attributes instead of direct `EventTerm` maintenance.

**Changes**:

- [x] Add read serializers/helpers that return grouped taxonomy assignments with enough metadata to hydrate edit forms
- [x] Return taxonomy assignments on event detail responses
- [x] Return taxonomy assignments on event-series retrieve responses, derived from the base non-exception occurrence/template model established in `2B.15`
- [x] Extend `EventAdminForm` to render taxonomy by active dimension rather than requiring direct `EventTerm` editing
- [x] Extend `EventSeriesAdminForm` to render the same taxonomy section for generated occurrences
- [x] Reuse the shared taxonomy assignment validator/parser from `2B.16` in admin form cleaning and save orchestration
- [x] Keep `EventTermAdmin` available only as a low-level maintenance/debug surface, not the primary authoring workflow
- [x] Fail clearly when a legacy series has no usable base occurrence from which taxonomy/template defaults can be derived

**Implementation Notes**:

- This task depends on the shared series-authoring orchestration introduced or extracted in `2B.15`.
- `EventSeries` itself should remain lightweight; grouped taxonomy on series reads should be derived from the base template occurrence rather than persisted separately on the series row.
- Read payloads should expose taxonomy in the same grouped shape used for writes, with optional nested dimension/term labels if helpful for admin or frontend hydration.
- Admin save behavior should continue to preserve exception occurrences by default during series sync.
- Admin forms now render one dynamic taxonomy field per active dimension, while still including inactive already-assigned terms and dimensions for edit hydration.

**Edge Cases**:

- [x] Event detail for an event with no taxonomy assignments
- [x] Event detail for an event carrying terms from multiple dimensions
- [x] Series retrieve where the first occurrence is an exception but a later non-exception base occurrence exists
- [x] Series retrieve where no non-exception occurrence exists and all occurrences were deleted or converted to exceptions
- [x] Admin edit of an event that still references inactive assigned terms
- [x] Admin series edit that changes taxonomy and should update only future non-exception occurrences

**Acceptance Criteria**:

- [x] Event detail responses expose grouped taxonomy assignments for edit hydration
- [x] Event-series retrieve responses expose grouped taxonomy assignments derived from the series template/base occurrence
- [x] Admin event create/edit can manage taxonomy by dimension without using direct `EventTerm` admin
- [x] Admin event-series create/edit can manage taxonomy by dimension for generated occurrences
- [x] Legacy series states without a usable template occurrence fail with actionable admin/API errors
- [x] Automated tests cover grouped taxonomy hydration and admin authoring behavior

**Test Cases**:

- [x] Event detail returns grouped taxonomy assignments in dimension order
- [x] Event detail returns an empty taxonomy assignment list for untagged events
- [x] Event-series retrieve returns grouped taxonomy assignments derived from the base occurrence
- [x] Event-series retrieve fails clearly when no base occurrence/template is available
- [x] Admin event save writes grouped taxonomy assignments through the shared validator
- [x] Admin event-series create generates tagged occurrences from grouped taxonomy assignments
- [x] Admin event-series update changes future non-exception occurrence taxonomy while preserving exceptions

---

## Phase 3: GeoFeedback Feature

> **Goal**: Citizen feedback collection with forms and ratings.

---

### Task 3.1: Create `feedback` App with Core Models

**Branch**: `feat/feedback-models`

**Description**: Create feedback app, including `GeoFeedback` model integrated with `django-basic-form-builder`.

**Changes**:

- [x] Add `django-basic-form-builder` to dependencies (`uv add django-basic-form-builder`)
- [x] Add `formbuilder` to `INSTALLED_APPS` and configure `FORMBUILDER_API_ENABLED = True`
- [x] Create `tosca_api/apps/feedback/`
- [x] Add GeoFeedback (FK -> Campaign, FK -> `formbuilder.CustomForm` nullable)
- [x] Add GeoFeedback fields: `rating_enabled` (bool), `form_enabled` (bool), `allow_drawings` (bool)
- [x] Add feedback_layers M2M
- [x] Add `clean()` validation to GeoFeedback: if `form_enabled` is True, `custom_form` must not be null. At least one of `rating_enabled` or `form_enabled` must be True.

**Acceptance Criteria**:

- [x] Admin can create CustomForms via the form builder and link them to GeoFeedback
- [x] GeoFeedback model validates its configuration correctly
- [x] Tests pass

**Tests**:

```python
@pytest.mark.django_db
def test_feedback_config_validation():
    # Should fail if form is enabled but no custom_form provided
    with pytest.raises(ValidationError):
        GeoFeedback(rating_enabled=False, form_enabled=True, custom_form=None).clean()
```

**Commit Message**:

```
feat(feedback): add GeoFeedback and integrate formbuilder

Create feedback app with:
- Install django-basic-form-builder dependency
- GeoFeedback: configurable feedback campaign linking to CustomForm
- rating_enabled and form_enabled configuration toggles
- FeedbackLayer: M2M junction
```

---

### Task 3.2: Add FeedbackSubmission Model

**Branch**: `feat/feedback-submission`

**Description**: Add FeedbackSubmission model for ratings, JSON schemas, and geometry.

**Changes**:

- [x] Add FeedbackSubmission model
- [x] Fields: feedback (FK -> GeoFeedback), rating (Integer 1-5, nullable), form_data (JSONB, nullable), geometry (GeometryField)
- [x] Add is_anonymized boolean

**Acceptance Criteria**:

- [x] Can store submissions with or without User (nullable FK)
- [x] Can store geometry (Point/Line/Poly)
- [x] JSONB field accepts arbitrary dict for dynamic answers to the CustomForm

**Tests**:

```python
@pytest.mark.django_db
def test_submission_custom_form_data():
    sub = FeedbackSubmission.objects.create(rating=5, form_data={"comment": "Hi"})
    assert sub.rating == 5
    assert sub.form_data["comment"] == "Hi"
```

**Commit Message**:

```
feat(feedback): add FeedbackSubmission model

Add submission model with:
- feedback FK
- rating (1-5)
- form_data JSONB (stores formbuilder answers)
- geometry (GeometryField for drawings)
- is_anonymized flag
```

---

### Task 3.3: GeoFeedback API Endpoints

**Branch**: `feat/feedback-api`

**Description**: Add REST API for GeoFeedback (Admin CRUD, Public Read) and submission.

**Changes**:

- [x] ViewSet for GeoFeedback (Public: ReadOnly; Admin: CRUD)
- [x] Include the linked `custom_form` slug/URL in the GeoFeedback read schema.
- [x] Custom action `POST /{id}/submit/` for submissions
- [x] Validation in submit action: `rating` required if `rating_enabled` is True; `form_data` required if `form_enabled` is True

**Acceptance Criteria**:

- [x] Admin can create/update Feedback forms
- [x] Anonymous user can submit if allowed
- [x] Submission validates against Feedback configuration (e.g., rating required if `rating_enabled`; form data validation required if `form_enabled`)
- [x] CursorPagination for Feedback list

**Tests**:

```python
def test_submit_feedback_with_rating_and_form(api_client, feedback):
    # Tests a feedback where both are enabled
    resp = api_client.post(f"/api/v1/feedback/{feedback.id}/submit/", {
        "rating": 5,
        "form_data": {"test_field": "test_answer"}
    })
    assert resp.status_code == 201
```

**Commit Message**:

```
feat(feedback): add REST API endpoints

Implement GeoFeedback API:
- GET/POST /api/v1/feedback/ (Admin CRUD, Public Read)
- POST /api/v1/feedback/{id}/submit/

Supports anonymous submissions, strict feedback config enforcement, and geometry.
- **Performance**: Use `CursorPagination`.
```

---

### Task 3.4: Extend FeatureLink for Feedback

**Branch**: `feat/featurelinks-feedback`

**Description**: Verify and test FeatureLink with GeoFeedback.

**Changes**:

- [x] Tests for linking Feedback to other entities
- [x] Verify Admin UI supports selecting GeoFeedback in generic relation

**Acceptance Criteria**:

- [x] Can link Story -> Feedback (e.g., "Rate this plan")
- [x] Tests pass
- [x] Admin UI generic relation picker includes GeoFeedback

**Commit Message**:

```
test(featurelinks): verify GeoFeedback linking

Add tests for:
- GeoStory → GeoFeedback links
- CalendarEvent → GeoFeedback links
```

---

## Phase 4: Production Readiness

> **Goal**: Optimization and infrastructure hardening.

---

### Task 4.1a: Configure PgBouncer

**Branch**: `chore/pgbouncer-infra`

**Description**: Add PgBouncer connection pooler to production Docker stack.

**Changes**:

- [ ] Add `pgbouncer` service to `docker-compose.prod.yml`
- [ ] Configure `settings/prod.py` to connect to pgbouncer port
- [ ] Add pgbouncer.ini / userlist.txt config volume

**Acceptance Criteria**:

- [ ] `docker compose -f docker-compose.prod.yml config` is valid
- [ ] Application connects via pooler

**Commit Message**:

```
chore(infra): enable pgbouncer connection pooling

Add pgbouncer service to production stack to manage
PostGIS connection overhead.
```

---

### Task 4.1b: Verify Spatial Indexes

**Branch**: `chore/verify-indexes`

**Description**: Audit and verify GiST indexes on all spatial columns.

**Changes**:

- [ ] Create management command `check_spatial_indexes`
- [ ] Query `pg_indexes` for all geometry columns
- [ ] Fail if index missing

**Acceptance Criteria**:

- [ ] Command reports OK for current schema
- [ ] Fails if we drop an index manually

**Commit Message**:

```
chore(db): add spatial index verification command

Add management command to audit GiST indexes on
all GeometryField columns.
```

---

## Phase 5: Security Hardening

> **Goal**: Address security vulnerabilities and harden the application before production deployment.

---

### Task 5.1: Input Sanitization for All Content

**Branch**: `feat/input-sanitization`

**Description**: Implement content sanitization for ALL GeoContext inputs based on `content_type`. This is a security-critical task that must be completed before any API accepts user content.

**Context**: GeoContext stores `content` as raw text. The `content_type` field indicates whether it's simple text or rich HTML. Both types require validation:

- **Simple**: No HTML allowed whatsoever (strip all tags)
- **Rich**: Only whitelisted HTML tags/attributes allowed

**Attack Vectors Addressed**:

- `<script>` tag injection
- Event handlers (`onerror`, `onclick`, etc.)
- CSS injection (`expression()`, malicious `url()`)
- HTML in "simple" content bypass

**Changes**:

- [x] Add `nh3` dependency to project (`uv add nh3`)
- [x] Create `tosca_api/apps/core/sanitization.py` with:
  - `sanitize_simple(content: str) -> str` — strips ALL HTML
  - `sanitize_rich(content: str) -> str` — allows only whitelisted HTML
  - `sanitize_content(content: str, content_type: str) -> str` — router function
- [x] Apply sanitization in GeoContext model `save()` method (enforces security at DB level since Serializers don't exist yet)
- [x] Add unit tests for both content types

**Sanitization Rules**:

```python
# For content_type="simple" — NO HTML ALLOWED
def sanitize_simple(content: str) -> str:
    """Strip ALL HTML tags, return plain text only."""
    return nh3.clean(content, tags=set())  # Empty set = strip all

# For content_type="rich" — ALLOWLIST ONLY
ALLOWED_TAGS = {
    "p", "br", "strong", "em", "b", "i", "u",
    "a", "ul", "ol", "li",
    "h1", "h2", "h3", "h4",
    "blockquote", "pre", "code",
    "img", "figure", "figcaption",
}
ALLOWED_ATTRS = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "width", "height"},
}
# No inline styles, no event handlers, no javascript: URLs

def sanitize_rich(content: str) -> str:
    """Allow only whitelisted HTML tags and attributes."""
    return nh3.clean(
        content,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        link_rel=None,
        url_schemes={"http", "https", "mailto"},  # No javascript:
    )
```

**Acceptance Criteria**:

- [x] **Simple content**: `<b>Bold</b>` becomes `Bold` (tags stripped)
- [x] **Rich content**: `<script>alert(1)</script>` is stripped
- [x] **Rich content**: `<img onerror="alert(1)">` becomes `<img>`
- [x] **Rich content**: `<a href="javascript:alert(1)">` href is stripped
- [x] **Rich content**: Valid HTML (e.g., `<p><strong>Hello</strong></p>`) passes through
- [x] Tests cover both content types and edge cases

**Tests**:

```python
# tosca_api/apps/core/tests/test_sanitization.py
import pytest
from tosca_api.apps.core.sanitization import sanitize_html

def test_removes_script_tags():
    unsafe = '<p>Hello</p><script>alert(1)</script>'
    assert '<script>' not in sanitize_html(unsafe)
    assert '<p>Hello</p>' in sanitize_html(unsafe)

def test_removes_event_handlers():
    unsafe = '<img src="x" onerror="alert(1)">'
    result = sanitize_html(unsafe)
    assert 'onerror' not in result

def test_removes_javascript_urls():
    unsafe = '<a href="javascript:alert(1)">Click</a>'
    result = sanitize_html(unsafe)
    assert 'javascript:' not in result

def test_preserves_safe_html():
    safe = '<p><strong>Bold</strong> and <em>italic</em></p>'
    assert sanitize_html(safe) == safe
```

**Commit Message**:

```
feat(security): add HTML sanitization for rich content

Implement XSS prevention for GeoContext rich content:
- Add nh3 dependency for Rust-based HTML sanitization
- Create allowlist-based sanitizer in core/sanitization.py
- Strip script tags, event handlers, javascript: URLs
- Preserve safe semantic HTML tags

Addresses security concern identified in Phase 0.5.
```

---

### Task 5.2: Rate Limiting for Public Endpoints

**Branch**: `feat/rate-limiting`

**Description**: Implement rate limiting to prevent abuse of public API endpoints.

**Changes**:

- [ ] Add `django-ratelimit` or use DRF's built-in throttling
- [ ] Configure throttle classes in settings
- [ ] Apply to public endpoints (campaign list, story detail, etc.)

**Acceptance Criteria**:

- [ ] Anonymous users limited to 100 requests/minute
- [ ] Authenticated users limited to 1000 requests/minute
- [ ] Rate limit headers returned in response

**Commit Message**:

```
feat(security): add rate limiting for API endpoints

Configure DRF throttling to prevent abuse:
- 100 req/min for anonymous users
- 1000 req/min for authenticated users
```

---

### Task 5.3: Security Headers and CORS Hardening

**Branch**: `feat/security-headers`

**Description**: Configure security headers and tighten CORS for production.

**Changes**:

- [ ] Add `django-csp` for Content Security Policy
- [ ] Configure `SECURE_*` settings in production
- [ ] Restrict CORS origins for production

**Acceptance Criteria**:

- [ ] CSP header present in responses
- [ ] X-Frame-Options, X-Content-Type-Options configured
- [ ] CORS only allows specified origins in production

**Commit Message**:

```
feat(security): add security headers and CSP

Configure production security:
- Content Security Policy via django-csp
- Strict CORS configuration
- Security headers (X-Frame-Options, etc.)
```

## Phase 6: Serializer Standardization

> **Goal**: Unify DRF serializers across all business apps, enforcing explicit read-only boundaries and strict validation.

---

### Task 6.1: Standardize `events` and `campaigns` Apps

**Branch**: `chore/standardize-serializers-core`

**Description**: Refactor serializers in the core `events` and `campaigns` applications to use distinct List/Detail/Write serializers, declare `read_only_fields = fields` on read models, and enforce `model.clean()` validation inside `Serializer.validate()`.

**Changes**:

- [x] Rename `CalendarEventCreateSerializer` to `CalendarEventWriteSerializer`
- [x] Add explicit `CalendarEvent().clean()` call inside `CalendarEventWriteSerializer.validate()`
- [x] Split `CampaignSerializer` into `CampaignListSerializer`, `CampaignDetailSerializer`, and `CampaignWriteSerializer`
- [x] Add `read_only_fields = fields` to `CampaignListSerializer` and `CampaignDetailSerializer`
- [x] Update `events/views.py` and `campaigns/views.py` to route to correct serializers

**Acceptance Criteria**:

- [x] All tests pass
- [x] `POST /api/v1/events/` validation includes DB-level rules (like start < end) without 500 errors

---

### Task 6.2: Standardize `geostories` and `feedback` Apps

**Branch**: `chore/standardize-serializers-features`

**Description**: Refactor serializers in the `geostories` and `feedback` applications using the unified pattern.

**Changes**:

- [x] Rename `GeoFeedbackCreateUpdateSerializer` to `GeoFeedbackWriteSerializer`
- [x] Add `read_only_fields = fields` to `GeoFeedbackListSerializer`
- [x] Add explicit `clean()` enforcement inside `GeoFeedbackWriteSerializer` and `FeedbackSubmissionSerializer`
- [x] Rename `GeoStorySerializer` to `GeoStoryWriteSerializer`
- [x] Update `FeatureLinkSerializer`, `GeoContextSerializer`, `LayerRefSerializer` to use `read_only_fields = fields`
- [x] Refactor `read_only_fields` on `GeoStoryListSerializer` and `GeoStoryDetailSerializer` to conform to `fields` syntax
- [x] Update respective `views.py` dependencies

**Acceptance Criteria**:

- [x] All tests pass
- [x] Security boundaries (creation vs retrieve payloads) are rigidly enforced

---

## Phase 7: GeoContext Editor.js Migration

> **Goal**: Replace legacy GeoContext text-plus-`content_type` authoring with canonical Editor.js JSON, deterministic normalization, progressive admin editing, and a staged multi-release migration.

---

### Task 7.1: Canonical GeoContext Editor.js Contract

**Branch**: `feat/geocontext-editorjs-contract`

**Description**: Replace the legacy `GeoContext` string-plus-`content_type` contract with canonical Editor.js JSON. `GeoContext.content` becomes a `JSONField` containing normalized Editor.js block data, and `content_type` is removed from the future model and all read surfaces. This task supersedes the original `content` + `content_type` GeoContext design for future implementation while preserving the old roadmap entries as history.

**Changes**:

- [x] Define `GeoContext.content` as JSON-backed Editor.js content instead of freeform text
- [x] Remove `content_type` from the future GeoContext model contract
- [x] Update future nested `GeoContext` read surfaces in `events`, `geostories`, and `feedback` to describe `context.content` as JSON
- [x] Standardize empty GeoContext content as `{ "blocks": [] }`
- [x] Remove or mark superseded any future-oriented documentation that still describes the new contract as `content` plus `content_type`

**Acceptance Criteria**:

- [x] `GeoContext.content` is defined as JSON-backed content rather than freeform text
- [x] `content_type` is removed from the future model, serializers, admin, and schema
- [x] All nested `GeoContext` read surfaces in events, geostories, and feedback describe `context.content` as JSON
- [x] Empty content is represented as `{ "blocks": [] }`, not `null`, `""`, or omitted
- [x] Historical `content_type` references in future-oriented docs for this feature are removed or marked superseded

**Implementation Notes** (completed via PR #105, commit `799f669`):

- `GeoContext.content` redefined as `JSONField(default=empty_editorjs_document, blank=True)` with helper returning `{"blocks": []}`.
- Destructive migration `0002_editorjs_canonical_contract.py` drops legacy `content_type` and text `content`, adds JSON `content` (pre-production reset; Release 1/2/3 staging in 7.4–7.6 applies only once Phase 7 ships).
- `GeoContext.save()` normalizes falsy values to the canonical empty document; legacy nh3 sanitization removed from this path (string sanitization still used by Campaign/GeoStory title/summary).
- Nested `GeoContextSerializer` in `geostories`, `events`, and `feedback` trimmed to `["id", "content"]`.
- Admin `content_preview` renders truncated JSON of `blocks` or `(empty)`; `content_type` removed from list/filter/fieldsets.
- `technical-architecture.md` GeoContext section tagged with supersession banner pointing to `decisions.md` §7.1.

**Tests**:

```python
# Representative test cases
def test_geocontext_empty_defaults_to_empty_blocks(): ...
def test_geocontext_persists_editorjs_json_content(): ...
def test_geostory_detail_serializes_context_content_as_json(): ...
def test_event_detail_serializes_context_content_as_json(): ...
def test_feedback_detail_serializes_context_content_as_json(): ...
def test_schema_contract_omits_content_type_for_future_geocontext_reads(): ...
```

- [ ] Unit: creating a new empty `GeoContext` yields `{ "blocks": [] }`
- [ ] Unit: non-empty valid Editor.js content is persisted as JSON
- [ ] API read: `context.content` serializes as JSON in geostories, events, and feedback detail responses
- [ ] Schema/doc test: `content_type` no longer appears in the future contract for the affected serializers
- [ ] Edge case: `content=None` normalizes to `{ "blocks": [] }`
- [ ] Edge case: missing `content` on write defaults safely to `{ "blocks": [] }`

**Commit Message**:

```text
feat(geocontext): define canonical Editor.js JSON contract

Replace the future GeoContext content contract with canonical Editor.js JSON:
- move content to JSON-backed storage
- remove content_type from the future contract
- standardize empty documents as {"blocks": []}
- update nested read surfaces to describe JSON content
```

---

### Task 7.2: Editor.js Validation and Normalization Layer

**Branch**: `feat/editorjs-validation`

**Description**: Introduce a dedicated `core/editorjs.py` validation and normalization layer for GeoContext Editor.js documents. This module owns block-level schema validation, deterministic normalization, inline HTML sanitization, and rejection of unsupported or ambiguous structures. It is intentionally separate from the legacy HTML sanitizer router.

**Changes**:

- [x] Create a dedicated Editor.js validation module, such as `tosca_api/apps/core/editorjs.py`
- [x] Validate only the MVP block set: `paragraph`, `header`, `list`, `quote`, `delimiter`, `code`
- [x] Allow only safe inline formatting in text-bearing fields: `a`, `strong`, `em`, `code`, `br`
- [x] Normalize `<b>` to `<strong>` and `<i>` to `<em>`
- [x] Accept full Editor.js save payloads as input but strip `time`, `version`, and block `id` on storage
- [x] Reject `quote.alignment`
- [x] Constrain `list.meta` to `{}` for MVP
- [x] Reject invalid block shapes and unsafe URLs

**Acceptance Criteria**:

- [x] Validation logic lives in a dedicated Editor.js module, not in the legacy HTML sanitizer router
- [x] Only the MVP block set is accepted: `paragraph`, `header`, `list`, `quote`, `delimiter`, `code`
- [x] Only allowed inline formatting survives in text-bearing fields: `a`, `strong`, `em`, `code`, `br`
- [x] `<b>` normalizes to `<strong>` and `<i>` normalizes to `<em>`
- [x] Full Editor.js save payloads may be accepted as input, but stored documents strip `time`, `version`, and block `id`
- [x] `quote.alignment` is rejected
- [x] `list.meta` is allowed only as `{}` for MVP
- [x] Invalid block shapes or unsafe URLs fail validation

**Implementation Notes**:

- New module `tosca_api/apps/core/editorjs.py` exposes `empty_document()` and `validate_and_normalize(value)`; raises `django.core.exceptions.ValidationError` on invalid shapes, unsupported block types, `quote.alignment`, non-empty `list.meta`, header levels outside 1–4, or `javascript:`-style URLs.
- Inline sanitization whitelists `a`, `strong`, `em`, `code`, `br` via nh3; `<b>`/`<i>` are rewritten to `<strong>`/`<em>` before sanitization; link schemes restricted to `http`, `https`, `mailto`.
- `GeoContext.save()` now delegates to `validate_and_normalize`, replacing the prior "normalize empty-only" behavior. Full save envelopes (`time`, `version`, per-block `id`, `tunes`) are stripped to canonical storage form.
- List items accept either strings (upgraded to `{content, items: []}`) or `{content, items}` dicts (recursively normalized); nested lists preserve shape.
- 18 unit tests added under `tosca_api/apps/core/tests/test_editorjs.py` covering supported blocks, envelope stripping, semantic tag normalization, script/handler stripping, `javascript:` rejection, unsupported block types, header level bounds, `quote.alignment`, `list.meta` constraint, nested lists, invalid shapes, and byte-equal round-trip.

**Tests**:

```python
# Representative test cases
def test_editorjs_accepts_supported_block_types(): ...
def test_editorjs_normalizes_save_envelope_to_canonical_storage(): ...
def test_editorjs_normalizes_b_and_i_tags(): ...
def test_editorjs_rejects_quote_alignment(): ...
def test_editorjs_rejects_non_empty_list_meta(): ...
def test_editorjs_rejects_unsupported_block_type(): ...
```

- [ ] Unit: valid paragraph/header/list/quote/delimiter/code blocks are accepted
- [ ] Unit: full Editor.js payload with `time`, `version`, and block `id` normalizes to canonical storage shape
- [ ] Unit: `<b>` and `<i>` normalize to semantic tags
- [ ] Unit: inline `<script>` and event-handler HTML are stripped or rejected appropriately
- [ ] Unit: `javascript:` links are rejected or sanitized away
- [ ] Edge case: unsupported block type fails with explicit validation error
- [ ] Edge case: header level outside `1-4` fails
- [ ] Edge case: `quote.alignment` present fails
- [ ] Edge case: `list.meta` with non-empty keys fails
- [ ] Edge case: repeated normalize-save-reload-save remains byte-equal

**Commit Message**:

```text
feat(core): add Editor.js validation and normalization layer

Introduce deterministic Editor.js validation for GeoContext:
- dedicated core/editorjs.py module
- strict MVP block schema enforcement
- inline HTML sanitization and semantic tag normalization
- canonical storage without time/version/block ids
```

---

### Task 7.3: Django Admin Editor.js Authoring

**Branch**: `feat/geocontext-editorjs-admin`

**Description**: Add Editor.js authoring to Django admin for GeoContext as progressive enhancement. The underlying form field remains a JSON textarea for no-JS fallback, while JavaScript upgrades it into an Editor.js editing surface and mirrors content back into the textarea before submit. The work also includes vendored static assets, license files, and changelist preview behavior.

**Changes**:

- [x] Add a JSON textarea-backed GeoContext admin form/widget
- [x] Enhance the textarea with Editor.js when admin JavaScript loads successfully
- [x] Mirror edited content back into the textarea before submit using standard Django POST submission
- [x] Vendor Editor.js assets into Django static files instead of loading from a CDN
- [x] Include required upstream license files with the vendored assets
- [x] Add changelist plain-text preview extraction with truncation and empty-state handling

**Acceptance Criteria**:

- [x] GeoContext admin uses an Editor.js-powered editing surface when JS loads successfully
- [x] The non-JS fallback remains a plain textarea containing canonical JSON
- [x] Form submission relies on standard Django POST behavior; no custom AJAX write path is introduced
- [x] Editor.js assets are vendored into static files, not loaded from a CDN
- [x] Required upstream license files are included alongside vendored assets
- [x] Changelist preview shows extracted plain text, truncated, and displays `(empty)` for empty documents

**Implementation Notes**:

- `tosca_api/apps/geocontext/widgets.py` defines `EditorJsWidget` (subclass of `forms.Textarea`) with a `Media` class loading six vendored scripts plus `init.js` and `editor.css`. `format_value` always emits canonical JSON (defaults to `{"blocks": []}`).
- `tosca_api/apps/geocontext/forms.py` defines `GeoContextAdminForm` using the widget for `content`; model-level `JSONField` pre-parses strings, and `clean_content` normalizes `""`/`None`/`{}` to `{"blocks": []}` and surfaces JSON-decode errors as form errors.
- `tosca_api/apps/geocontext/admin.py` wires the form into `GeoContextAdmin` and replaces the raw-JSON preview with `_extract_plain_text`, which flattens paragraph/header/quote/code/list content and returns `(empty)` when no text is present; long previews are truncated to 75 chars plus ellipsis.
- Vendored assets under `tosca_api/apps/geocontext/static/geocontext/editorjs/vendor/`: `editorjs.umd.js@2.30.7`, `header@2.8.8`, `list@1.10.0` (UMD build), `quote@2.7.3`, `delimiter@1.4.2`, `code@2.9.3`, plus `LICENSE.*` files for each upstream package (all MIT).
- `init.js` is a progressive-enhancement script: for each `textarea[data-editorjs-target]`, it hides the textarea, mounts Editor.js with the MVP toolset, mirrors content back to the textarea on change and on `submit`, and relies on standard form POST. JS disabled or missing → raw JSON textarea remains fully usable.
- Widget template at `tosca_api/apps/geocontext/templates/geocontext/widgets/editorjs.html` renders the textarea with help text describing the fallback path.
- Tests added under `tosca_api/apps/geocontext/tests/test_admin.py` (11 cases) cover widget Media, CDN absence, license file presence, admin change/add render, form parse/persist, malformed JSON rejection, `clean_content` defensive branches, plain-text extraction across block types, and changelist preview empty/truncation behavior.
- Fixes a pre-existing fixture regression in `featurelinks/tests/test_models.py` that still created GeoContext with a plain string (now JSON), uncovered once Task 7.2 tightened validation.

**Tests**:

```python
# Representative test cases
def test_geocontext_admin_includes_editorjs_assets(): ...
def test_geocontext_admin_renders_json_textarea_fallback(): ...
def test_geocontext_admin_hydrates_stored_json_on_edit(): ...
def test_geocontext_admin_preview_truncates_long_content(): ...
def test_geocontext_admin_preview_handles_empty_document(): ...
```

- [ ] Admin render: widget assets are included on the GeoContext admin form
- [ ] Admin render: raw textarea is present before enhancement
- [ ] Admin behavior: stored JSON hydrates into the editor on edit
- [ ] Admin submit: edited content is mirrored back into the form field and persists correctly
- [ ] Admin list: preview is truncated consistently for long documents
- [ ] Edge case: empty document shows `(empty)` in changelist preview
- [ ] Edge case: JS not loading still permits valid raw JSON submission
- [ ] Edge case: malformed JSON pasted into the textarea fails with a clear validation error

**Commit Message**:

```text
feat(admin): add Editor.js authoring for GeoContext

Add progressive Editor.js authoring in Django admin:
- JSON textarea fallback
- JS enhancement without custom AJAX writes
- vendored assets and license files
- plain-text preview extraction for changelists
```

---

### Task 7.4: GeoContext Preflight and HTML-to-Blocks Backfill

**Branch**: `feat/geocontext-editorjs-preflight`

**Description**: Add a read-only preflight command and the Release 1 backfill logic that converts existing GeoContext rows from legacy string or HTML content into canonical Editor.js JSON. Conversion must be deterministic and must abort on legacy media-bearing content rather than silently dropping it.

**Changes**:

- [x] Add a non-mutating preflight command that scans existing GeoContext rows and reports exact sorted IDs that cannot be migrated
- [x] Make the preflight command read-only and idempotent
- [x] Convert legacy simple content to paragraph blocks or `{ "blocks": [] }` when blank
- [x] Sanitize legacy rich HTML first, then parse it into canonical blocks
- [x] Implement fixed mappings for headers, paragraphs, nested lists, blockquotes, code blocks, inline code, and `<br>`
- [x] Abort on rows containing `img`, `figure`, or `figcaption`
- [x] Ensure the data migration uses the same detection logic as the preflight command

**Acceptance Criteria**:

- [x] A preflight command scans existing GeoContext rows and reports the exact sorted IDs that cannot be migrated
- [x] The preflight command is read-only and idempotent
- [x] Legacy simple content maps to paragraph blocks or `{ "blocks": [] }` when blank
- [x] Legacy rich content is sanitized first, then parsed into blocks
- [x] Supported HTML mappings are fixed and documented: headers, paragraphs, nested lists, blockquotes, code blocks, inline code, `<br>`
- [x] Rows containing `img`, `figure`, or `figcaption` abort migration with explicit IDs
- [x] The data migration uses the same detection logic as the preflight command

**Tests**:

```python
# Representative test cases
def test_migrate_blank_simple_content_to_empty_blocks(): ...
def test_migrate_simple_text_to_single_paragraph_block(): ...
def test_migrate_rich_html_to_deterministic_blocks(): ...
def test_migrate_nested_lists_preserves_shape(): ...
def test_preflight_is_read_only_and_repeatable(): ...
def test_preflight_reports_exact_media_blocking_ids(): ...
```

- [x] Migration unit: blank simple content becomes `{ "blocks": [] }`
- [x] Migration unit: simple text becomes one paragraph block
- [x] Migration unit: supported rich HTML becomes deterministic block JSON
- [x] Migration unit: nested `<ul>/<ol>` structures preserve nested list shape
- [x] Migration unit: `<pre><code>` becomes `code` block and inline `<code>` remains inline
- [x] Migration unit: `<br>` inside paragraphs is preserved
- [x] Preflight test: command output is sorted and stable across repeated runs
- [x] Preflight test: command does not mutate database rows
- [x] Edge case: mixed top-level text nodes outside block tags are folded into paragraph blocks
- [x] Edge case: sanitized unknown non-media tags do not create undefined block types
- [x] Edge case: rows with `img`/`figure`/`figcaption` fail and list exact IDs

**Implementation Notes**:

- `tosca_api/apps/core/legacy_html.py` — reusable converter. Exposes
  `convert_legacy_html(html) -> dict` and `LegacyHtmlMediaError`. Uses
  `html.parser.HTMLParser` (stdlib; avoids pulling in bs4/lxml).
  Sanitization runs through `sanitize_rich()` before parsing so the
  pipeline mirrors the original sanitizer → block contract.
- Blocking media detection (`img`, `figure`, `figcaption`) happens **before**
  sanitization so the preflight reports the presence of legacy media
  verbatim regardless of whether nh3 would have stripped it.
- Inline tags inside `<pre>` are suppressed so `<pre><code>...</code></pre>`
  and bare `<pre>...</pre>` both yield a canonical `code` block carrying
  only the inner text (single leading/trailing newline trimmed).
- Orphan top-level text nodes between block tags are folded into their
  own paragraph blocks; unknown non-media wrappers (e.g. `<section>`) are
  skipped without inventing block types for them.
- `tosca_api/apps/geocontext/management/commands/geocontext_preflight.py`
  — read-only Django management command. Scans existing rows with
  `validate_and_normalize` (stable sort by UUID) and, with
  `--legacy-input-json path`, dry-runs the HTML converter against a
  `{id: html}` file to surface media-blocked rows by their input ID.
  The command never writes to the DB.
- Release 1 reset means no legacy HTML rows exist in production, so the
  converter + preflight land as reusable tooling. Later releases can
  drive any real migration through the same detection logic, satisfying
  the "migration and preflight agree" acceptance bullet.

**Commit Message**:

```text
feat(geocontext): add Editor.js preflight and legacy backfill

Prepare Release 1 GeoContext migration:
- read-only idempotent preflight command
- deterministic HTML-to-block conversion
- nested list and code block handling
- explicit abort on legacy media-bearing rows
```

---

### Task 7.5: Release 2 Switch to `content_json`

**Branch**: `feat/geocontext-editorjs-switch`

**Description**: Switch application reads and writes to the new JSON-backed GeoContext field while retaining the legacy columns for rollback safety. This release moves the app behavior to the new contract without yet dropping the old fields.

**Changes**:

- [x] Switch all application reads to `content_json` as the GeoContext source of truth *(folded — Task 7.1 destructive reset already made `GeoContext.content` the canonical JSON column; there is no separate `content_json` field to switch to)*
- [x] Switch all application writes and validation paths to `content_json` *(folded — writes flow through `validate_and_normalize` in `GeoContext.save()` plus the admin form; see Task 7.2/7.3)*
- [~] Keep legacy columns present for rollback safety during Release 2 *(N/A — Task 7.1 dropped `content_type` and the legacy text column destructively in migration `0002_editorjs_canonical_contract`; phased rollback via legacy columns is no longer available)*
- [x] Update events, geostories, and feedback nested serializers to emit the JSON-backed content contract
- [x] Remove active read/write dependencies on `content_type`

**Acceptance Criteria**:

- [x] All application reads use `content_json` as the source of truth *(reads use `GeoContext.content`, canonical JSON)*
- [x] All application writes populate and validate `content_json` *(writes go through `validate_and_normalize`)*
- [~] Legacy columns remain present but are no longer the active read/write path *(N/A — see 7.1 destructive reset)*
- [x] Events, geostories, and feedback nested serializers all emit the JSON-backed content contract
- [~] Rollback remains possible because legacy columns are still available during this release *(N/A — see 7.1 destructive reset)*

**Tests**:

```python
# Representative test cases
def test_geostory_detail_reads_content_json(): ...
def test_event_detail_reads_content_json(): ...
def test_feedback_detail_reads_content_json(): ...
def test_admin_edit_updates_content_json(): ...
def test_no_active_path_depends_on_content_type(): ...
```

- [ ] Integration: geostories detail reads from JSON-backed context data
- [ ] Integration: events detail reads from JSON-backed context data
- [ ] Integration: feedback detail reads from JSON-backed context data
- [ ] Integration: admin edits update the JSON-backed field
- [ ] Regression: no active serializer or admin form still depends on `content_type`
- [ ] Edge case: rows with empty migrated content still serialize as `{ "blocks": [] }`
- [ ] Edge case: old columns remaining populated do not affect the read path once switched

**Commit Message**:

```text
feat(geocontext): switch application reads and writes to content_json

Move GeoContext behavior to the new JSON source of truth:
- read/write through content_json
- keep legacy columns for rollback safety
- update nested serializers and admin paths
```

**Implementation Notes**:

- The original phased plan (add `content_json`, switch, then drop legacy)
  was collapsed by Task 7.1, which dropped `content_type` and the legacy
  text column in one destructive migration. `GeoContext.content` is
  already the canonical Editor.js JSON field and phased rollback via
  the legacy columns is no longer a safety net. See decisions.md §[7.5].
- Nested serializers already expose the JSON contract:
  `GeoStoryDetailSerializer.context` (via `GeoContextSerializer`),
  `EventDetailSerializer.context` (via `EventGeoContextSerializer` +
  `effective_context` series-default resolution), and
  `GeoFeedbackDetailSerializer.context` (via
  `FeedbackGeoContextSerializer`). Each exposes `content` as a dict and
  does not expose a `content_type` discriminator.
- Removed the dead `tosca_api.apps.core.sanitization.sanitize_content`
  helper and its standalone test. The helper dispatched on the retired
  `content_type` string ('simple'/'rich') and was only called by the
  pre-7.1 `GeoContext.save()`. Remaining sanitization uses
  `sanitize_simple()` / `sanitize_rich()` directly at each call site.
- Added `tosca_api/apps/geocontext/tests/test_json_contract.py` (14
  cases) as the final regression guard covering geostory/event/feedback
  detail contract, empty-context serialization as `{"blocks": []}`,
  admin save round-trip, absence of `content_type` on the model, and a
  guard against re-introducing `sanitize_content`.

---

### Task 7.6: Release 3 Drop Legacy GeoContext Fields

**Branch**: `feat/geocontext-editorjs-cleanup`

**Description**: Complete the migration by removing the legacy GeoContext string fields, renaming `content_json` back to `content`, and finalizing the codebase on the canonical Editor.js contract.

**Changes**:

- [x] Drop the legacy text `content` column and `content_type` *(landed in Task 7.1 via migration `0002_editorjs_canonical_contract`)*
- [~] Rename `content_json` back to `content` *(N/A — `content_json` was never introduced; destructive 7.1 promoted JSON directly onto `content`)*
- [x] Remove any remaining model, serializer, admin, migration helper, or doc references to the old fields *(`sanitize_content` helper removed in Task 7.5; post-sweep confirms no remaining in-app references to `content_json` or to a GeoContext `content_type` discriminator)*
- [x] Align the final schema and codebase with the canonical Editor.js contract
- [x] Add final regression coverage across model, admin, and nested serializer surfaces

**Acceptance Criteria**:

- [x] Legacy `content` text column and `content_type` are dropped *(Task 7.1)*
- [~] `content_json` is renamed to `content` *(N/A — never introduced)*
- [x] No model, serializer, admin, migration helper, or doc for the new path refers to the old fields
- [x] The final schema and codebase align exactly with the canonical Editor.js contract
- [x] Regression coverage confirms behavior is unchanged after the rename

**Tests**:

```python
# Representative test cases
def test_final_schema_contains_only_json_content_field(): ...
def test_geocontext_crud_works_after_final_rename(): ...
def test_nested_serializers_preserve_json_contract_after_cleanup(): ...
def test_admin_editor_still_loads_after_final_rename(): ...
```

- [x] Migration test: final schema contains only the canonical JSON `content` field *(`test_geocontext_final_schema_snapshot` pins `{id, title, content, created_by, created_at, updated_at}` and explicitly rejects `content_type` / `content_json`)*
- [x] Regression: GeoContext create/read/update still works after rename *(`test_geocontext_crud_round_trip`)*
- [x] Regression: nested serializers still emit the same JSON contract after cleanup *(Task 7.5 `test_json_contract.py` covers geostory / event / feedback detail endpoints)*
- [x] Regression: admin editor still loads and saves correctly after rename *(Task 7.3 `test_admin.py` covers change-form render + JSON persistence)*
- [~] Edge case: migration from a Release 2 state with empty and populated rows succeeds without data loss *(N/A — no Release 2 state ever existed; see decision §[7.5b])*

**Implementation Notes**:

- Task 7.6 was almost entirely superseded: Task 7.1 already dropped
  `content_type` and the legacy text column, and `content_json` was
  never introduced, so there is no legacy schema to clean up and no
  column to rename.
- Remaining work landed as two regression locks in
  `tosca_api/apps/geocontext/tests/test_models.py`:
  1. `test_geocontext_final_schema_snapshot` pins the exact set of
     concrete `GeoContext` fields (`id`, `title`, `content`,
     `created_by`, `created_at`, `updated_at`) and fails loud if a
     retired name (`content_type`, `content_json`) reappears.
  2. `test_geocontext_crud_round_trip` exercises the full
     create / read / update / delete path through the canonical JSON
     save pipeline.
- Full-repo sweep (`grep -R content_json tosca_api/`,
  `grep -R content_type tosca_api/apps/geocontext/`) confirms no
  residual references: `content_type` matches in `featurelinks`,
  `events`, and `geostories` refer to Django's unrelated
  `contenttypes.ContentType` generic-relations, not to the retired
  GeoContext discriminator.

**Commit Message**:

```text
feat(geocontext): finalize Editor.js migration and remove legacy fields

Complete the GeoContext migration:
- drop legacy content and content_type fields
- rename content_json back to content
- remove obsolete references
- preserve the canonical Editor.js JSON contract
```

---

## Phase 8: GeoData Provider Integration & LayerRef Refactor

> **Goal**: Replace the standalone `layerrefs` indirection with direct foreign keys to `geodata_providers.Layer` so GeoStory, Event, and GeoFeedback consume canonical layer metadata (geometry, SRID, publishing state, public flag, published URL) from one source of truth, and stop maintaining a parallel layer registry.
>
> **Context**:
>
> - `geodata_providers` already models `GeodataEngine → Workspace → Store → Layer → Style` and synchronizes layer state with GeoServer/Martin/pg_tileserv via `GeoServerSyncService` and `EngineClientFactory`. It is the canonical layer registry.
> - `layerrefs.LayerRef` currently holds a free-form `layer_name` (`"workspace:layername"`) and is the M2M target of `GeoStory.layers`, `GeoFeedback.layers`, and `Event.layers` via per-app through models with `display_order`.
> - There is no FK or resolver between `LayerRef` and `Layer`. Consumers cannot verify a layer exists, is published, is public, or read its geometry/SRID/published URL without parsing the string.
> - The system is not yet in production. Backward-compatible shims are not required. A destructive schema reset for the affected through-tables is acceptable.
>
> **Key Outcomes**:
>
> - `GeoStoryLayer`, `EventLayer`, and `FeedbackLayer` reference `geodata_providers.Layer` directly via FK, with `display_order` preserved per parent.
> - `layerrefs` app, `LayerRefSerializer`, and string-based layer references are removed.
> - All three consumer apps validate that assigned layers are public + published.
> - Detail responses for GeoStory / Event / GeoFeedback expose layer summaries (`id`, `name`, `workspace`, `geometry_type`, `srid`, `published_url`, `is_public`, `publishing_state`) instead of just `layer_name`.
> - Admins deleting a `Layer` see a usage count across consuming apps before confirming.
> - `Task 1.4a` (LayerRef Sync Client) and `Task 1.4b` (LayerRef Sync Endpoint) are superseded by the existing `GeoServerSyncService` in `geodata_providers`.
> - `Task 1.3b` (GeoStory Nested Writes) is reframed: the `layers` write contract becomes a list of `Layer.id` UUIDs instead of `layer_name` strings.

---

### Task 8.1: Replace `layerrefs.LayerRef` FK with `geodata_providers.Layer` FK

**Branch**: `feat/layerref-direct-fk`

**Description**: Swap the FK target on `GeoStoryLayer.layer`, `EventLayer.layer`, and `FeedbackLayer.layer` from `layerrefs.LayerRef` to `geodata_providers.Layer`. Re-declare the M2M `layers` field on `GeoStory`, `Event`, and `GeoFeedback` to point at `geodata_providers.Layer` through the existing through models. Preserve `display_order`, the auto-increment `save()` logic, and the `(parent, layer)` uniqueness constraint on each through model.

**Changes**:

- [ ] Change `GeoStoryLayer.layer` FK from `"layerrefs.LayerRef"` to `"geodata_providers.Layer"`, `on_delete=CASCADE`, `related_name="geostory_uses"`
- [ ] Change `EventLayer.layer` FK from `"layerrefs.LayerRef"` to `"geodata_providers.Layer"`, `on_delete=CASCADE`, `related_name="event_uses"`
- [ ] Change `FeedbackLayer.layer` FK from `"layerrefs.LayerRef"` to `"geodata_providers.Layer"`, `on_delete=CASCADE`, `related_name="feedback_uses"`
- [ ] Re-declare `GeoStory.layers`, `Event.layers`, `GeoFeedback.layers` M2M with `to="geodata_providers.Layer"` and `through=` unchanged
- [ ] Preserve existing `display_order` semantics, `Meta.ordering`, the auto-increment `save()` logic, and the `(parent, layer)` uniqueness constraints
- [ ] Generate migrations for `geostories`, `events`, `feedback` that swap the FK target (destructive: existing through-rows referencing `LayerRef` are dropped during the migration since the system is not yet in production)
- [ ] Update model docstrings and admin registrations that mention `LayerRef`

**Acceptance Criteria**:

- [ ] All three through models reference `geodata_providers.Layer` directly
- [ ] `display_order` ordering still works the same way per parent
- [ ] The same `Layer` can be assigned to multiple parents at different `display_order` values without conflict
- [ ] Migrations apply cleanly on a destructive reset
- [ ] No `LayerRef` import remains in `geostories`, `events`, or `feedback`

**Tests**:

- [ ] Each through model can be created with a `Layer` FK and an explicit `display_order`
- [ ] The same `Layer` assigned to two different parents persists with independent `display_order` values
- [ ] Adding a layer without `display_order` auto-increments per parent
- [ ] `(parent, layer)` uniqueness still rejects duplicate assignments to the same parent

**Commit Message**:

```text
feat(layers): point GeoStory/Event/Feedback through-models at Layer

Replace the layerrefs.LayerRef FK on GeoStoryLayer, EventLayer, and
FeedbackLayer with a direct FK to geodata_providers.Layer. Re-declare
the M2M layers field on each parent. Preserve display_order, ordering,
auto-increment, and (parent, layer) uniqueness.

Destructive migration: pre-production, no row preservation required.
```

---

### Task 8.2: Remove `layerrefs` App

**Branch**: `feat/layerrefs-app-removal`

**Description**: Now that no consumer references `layerrefs.LayerRef`, delete the app entirely. Drop the `layerrefs_layerref` table, remove the directory, deregister from `INSTALLED_APPS`, and delete the `LayerRefSerializer` from `geostories`.

**Changes**:

- [ ] Add a destructive migration that drops the `layerrefs_layerref` table
- [ ] Remove `tosca_api.apps.layerrefs` from `INSTALLED_APPS`
- [ ] Delete the `tosca_api/apps/layerrefs/` directory in full (models, admin, migrations, tests)
- [ ] Remove `LayerRefSerializer` from `tosca_api/apps/geostories/serializers.py`
- [ ] Remove any remaining `from tosca_api.apps.layerrefs...` imports across the codebase
- [ ] Update `Task 0.6` ("Create `layerrefs` App") to reference this removal in implementation notes

**Acceptance Criteria**:

- [ ] `INSTALLED_APPS` no longer lists `layerrefs`
- [ ] `tosca_api/apps/layerrefs/` no longer exists
- [ ] `LayerRefSerializer` no longer exists in `geostories`
- [ ] `grep -R LayerRef tosca_api/` returns zero matches in production code
- [ ] Migrations apply cleanly

**Tests**:

- [ ] Repo-level grep test (or equivalent CI assertion) confirms no `LayerRef` references in `tosca_api/apps/`
- [ ] Existing geostories / events / feedback tests still pass after removal

**Commit Message**:

```text
chore(layerrefs): remove layerrefs app

The LayerRef indirection has been replaced by direct FKs to
geodata_providers.Layer. Drop the app, the layerrefs_layerref table,
INSTALLED_APPS entry, the LayerRefSerializer, and any remaining imports.
```

---

### Task 8.3: Public + Published Validation on Layer Assignment

**Branch**: `feat/layer-assignment-validation`

**Description**: Add `clean()` validation on `GeoStoryLayer`, `EventLayer`, and `FeedbackLayer` so only layers with `is_public=True` and `publishing_state="PUBLISHED"` can be assigned. Use Django `ValidationError` so admin and DRF surface form-friendly errors.

**Changes**:

- [ ] Add `clean()` to each through model rejecting layers where `is_public=False` or `publishing_state != "PUBLISHED"`
- [ ] Surface validation errors through DRF write serializers on each consuming app so API clients see 400 with a clear message
- [ ] Surface validation errors through admin inline saves
- [ ] Document the rule in each through model's docstring

**Acceptance Criteria**:

- [ ] Assigning a non-public layer is rejected from API and admin
- [ ] Assigning a non-published layer is rejected from API and admin
- [ ] Assigning a public + published layer succeeds
- [ ] Existing valid through-rows continue to round-trip without revalidation errors

**Tests**:

- [ ] Through-model `clean()` rejects `is_public=False`
- [ ] Through-model `clean()` rejects `publishing_state` other than `PUBLISHED`
- [ ] Through-model `clean()` accepts public + published layers
- [ ] DRF writes return 400 with the expected validation message on each consuming app
- [ ] Admin inline save shows the validation error instead of partially persisting

**Commit Message**:

```text
feat(layers): enforce public + published layer assignment

Add clean() validation on GeoStoryLayer, EventLayer, and FeedbackLayer
so only layers with is_public=True and publishing_state=PUBLISHED can
be attached to a GeoStory, Event, or GeoFeedback.
```

---

### Task 8.4: `LayerSummarySerializer` and Detail Response Hydration

**Branch**: `feat/layer-summary-serializer`

**Description**: Add a slim, reusable `LayerSummarySerializer` in `geodata_providers` (or a dedicated shared module) that exposes the fields consumers actually need — `id`, `name`, `workspace`, `geometry_type`, `srid`, `published_url`, `is_public`, `publishing_state`. Wire it into GeoStory detail, Event detail, and GeoFeedback detail responses so the frontend gets full layer metadata in one call.

**Changes**:

- [ ] Add `LayerSummarySerializer` exposing `id`, `name`, `workspace` (nested `{id, name}`), `geometry_type`, `srid`, `published_url`, `is_public`, `publishing_state`
- [ ] Replace nested layer output in GeoStory detail serializer with `LayerSummarySerializer` plus `display_order`
- [ ] Add nested layer output to Event detail serializer using the same shape
- [ ] Add nested layer output to GeoFeedback detail serializer using the same shape
- [ ] Optimize querysets with `select_related("workspace")` and `prefetch_related` on the through tables to avoid N+1 queries
- [ ] Update OpenAPI schema annotations for the three detail endpoints

**Acceptance Criteria**:

- [ ] GeoStory detail returns each linked layer as a `LayerSummary` object (not just `layer_name`)
- [ ] Event detail returns linked layers as `LayerSummary` objects
- [ ] GeoFeedback detail returns linked layers as `LayerSummary` objects
- [ ] Each linked-layer entry includes `display_order`
- [ ] No N+1 queries on detail endpoints (verified via test or `assertNumQueries`)
- [ ] OpenAPI schema reflects the new layer summary contract

**Tests**:

- [ ] GeoStory detail response includes `geometry_type`, `srid`, `published_url`, `is_public`, `publishing_state`, and `display_order` for each linked layer
- [ ] Event detail response shape matches the GeoStory shape
- [ ] GeoFeedback detail response shape matches the GeoStory shape
- [ ] `assertNumQueries` test confirms detail-endpoint queries are bounded regardless of layer count

**Commit Message**:

```text
feat(layers): add LayerSummarySerializer and wire it into detail APIs

Expose canonical layer metadata (geometry_type, srid, published_url,
is_public, publishing_state) on GeoStory, Event, and GeoFeedback detail
endpoints by replacing the legacy LayerRef name-only output with a
shared LayerSummarySerializer plus display_order from the through model.
```

---

### Task 8.5: Replace String Layer Writes With UUID-Based Writes

**Branch**: `feat/layer-uuid-writes`

**Description**: Update the write contract for GeoStory, Event, and GeoFeedback so the `layers` payload accepts a list of `Layer.id` UUIDs (with optional `display_order`) instead of `layer_name` strings. This supersedes `Task 1.3b`'s nested-writes contract for layers.

**Changes**:

- [ ] Update GeoStory write serializer to accept `layers` as a list of `{id: <uuid>, display_order: <int>}` objects (or a plain UUID list with auto-incremented order)
- [ ] Update Event write serializer with the same contract
- [ ] Update GeoFeedback write serializer with the same contract
- [ ] Drop any `layer_name` string parsing path
- [ ] Resolve UUIDs to `Layer` instances and feed them through the through-model `clean()` so validation runs (Task 8.3)
- [ ] Atomic transactions for nested writes
- [ ] Update OpenAPI schema and any consumer-facing examples
- [ ] Update `Task 1.3b` to mark its "layers list of layer_name" sub-bullet superseded; the rest of `1.3b` (context nested writes) is unchanged

**Acceptance Criteria**:

- [ ] POST/PATCH bodies accept layers by UUID and persist them through the existing through models
- [ ] An invalid / unknown / non-public / non-published UUID returns a clear 400 error
- [ ] Nested writes are atomic
- [ ] String-based `layer_name` payloads are no longer accepted
- [ ] OpenAPI schema reflects the UUID contract

**Tests**:

- [ ] GeoStory create with a list of `Layer` UUIDs persists `GeoStoryLayer` rows in the supplied order
- [ ] Event create with the UUID contract persists `EventLayer` rows
- [ ] GeoFeedback create with the UUID contract persists `FeedbackLayer` rows
- [ ] Unknown UUID returns 400
- [ ] Non-public layer UUID returns 400
- [ ] Non-published layer UUID returns 400
- [ ] String `layer_name` payloads are rejected with a clear error

**Commit Message**:

```text
feat(layers): switch nested layer writes to Layer UUIDs

Replace the legacy layer_name string contract on GeoStory, Event, and
GeoFeedback nested writes with a Layer UUID list. Validation runs
through the through-model clean() introduced in Task 8.3.

Supersedes the layers sub-bullet of Task 1.3b.
```

---

### Task 8.6: Pre-delete Usage Warning for `Layer`

**Branch**: `feat/layer-delete-usage-warning`

**Description**: Before a `Layer` is deleted (admin or API path), surface a count of GeoStory / Event / GeoFeedback rows that reference it via the new `geostory_uses`, `event_uses`, `feedback_uses` related managers. Admins must confirm the cascade. API deletes return the usage count in the response so callers can warn the user.

**Changes**:

- [ ] Add a helper `Layer.usage_summary() -> dict[str, int]` returning counts via the related managers
- [ ] Override the `Layer` admin delete confirmation template (or `delete_view` / `delete_selected` action) to show usage counts and a warning banner
- [ ] In the `LayerViewSet` `destroy` action, return `{usage: {...}, deleted: true/false}`. If `?confirm=true` is not supplied and usage is non-zero, return `409 Conflict` with the usage breakdown instead of deleting
- [ ] Document the confirmation behavior in OpenAPI

**Acceptance Criteria**:

- [ ] Admin delete confirmation shows usage counts for each consuming app
- [ ] API delete without `?confirm=true` against a referenced layer returns 409 with usage counts
- [ ] API delete with `?confirm=true` against a referenced layer cascades and removes through-rows
- [ ] API delete against an unused layer succeeds without requiring confirmation
- [ ] Cascade behavior is unchanged at the schema level (`on_delete=CASCADE` on through-models stays)

**Tests**:

- [ ] `Layer.usage_summary()` returns the correct counts across the three consuming apps
- [ ] Admin delete confirmation page renders the usage warning
- [ ] API destroy without `confirm=true` on a referenced layer returns 409 + usage payload
- [ ] API destroy with `confirm=true` on a referenced layer cascades and removes the through-rows
- [ ] API destroy on an unreferenced layer returns 204

**Commit Message**:

```text
feat(geodata-providers): warn before deleting a referenced Layer

Add Layer.usage_summary() and surface counts of dependent GeoStory,
Event, and GeoFeedback rows in admin delete confirmation and the
LayerViewSet destroy action. API destroy returns 409 unless the caller
opts in with ?confirm=true.
```

---

### Task 8.7: Supersede `Task 1.4a` and `Task 1.4b`

**Branch**: `chore/supersede-layerref-sync`

**Description**: Mark the legacy LayerRef sync tasks as superseded by `geodata_providers`. The `geodata_providers` app already exposes `GeoServerSyncService` and `EngineClientFactory` for syncing layers from GeoServer / Martin / pg_tileserv, plus admin actions and an `engines/{id}/sync` API path. There is no remaining work for a separate LayerRef sync client or endpoint.

**Changes**:

- [ ] Update `Task 1.4a` and `Task 1.4b` entries in `tasks.md` and `issues.md` with a "Superseded by Phase 8" note pointing at `GeoServerSyncService`
- [ ] Update the `Phase 1 - GeoStory Feature` epic in `issues.md` so the `1.4a` / `1.4b` checkboxes are marked superseded rather than open
- [ ] No code changes

**Acceptance Criteria**:

- [ ] `Task 1.4a` and `Task 1.4b` are clearly marked superseded with a pointer to the canonical sync path
- [ ] Phase 1 epic reflects the change

**Tests**:

- [ ] None (docs-only)

**Commit Message**:

```text
docs(layerrefs): mark Task 1.4a/1.4b superseded by geodata_providers

The geodata_providers app's GeoServerSyncService is the canonical layer
sync path. There is no remaining work for a separate LayerRef sync
client or endpoint.
```

---

## Phase 9: GeoStory & GeoContext Image Support

> **Goal**: Add strict, secure image support for GeoStories via a dedicated hero image field and extend GeoContext Editor.js with canonical image blocks backed by authenticated backend uploads.

---

### Task 9.1: GeoStory Hero Image Storage Contract

**Branch**: `feat/geostory-hero-image-model`

**Description**: Add first-class hero image support to `GeoStory` using Django `ImageField` so each story can carry a validated cover image and required alt text. The storage contract must stay backend-agnostic (local now, S3-compatible via Django storage settings).

**Changes**:

- [ ] Add `hero_image = models.ImageField(...)` to `GeoStory`
- [ ] Add `hero_image_alt = models.CharField(...)` (required when hero image is present)
- [ ] Add deterministic upload path helper for hero images (e.g. `geostories/hero/%Y/%m/...`)
- [ ] Add migration for the new model fields
- [ ] Document that storage is Django-storage-backed and swappable via settings

**Acceptance Criteria**:

- [ ] GeoStory can persist a hero image file via Django storage
- [ ] GeoStory hero image metadata includes required alt text when image is set
- [ ] Upload path is deterministic and namespaced for GeoStories
- [ ] Model migration applies cleanly

**Tests**:

- [ ] Model test: story saves without hero image
- [ ] Model test: story saves with hero image + alt
- [ ] Model test: missing alt with hero image fails validation
- [ ] Migration test: new fields exist with expected null/blank behavior

**Commit Message**:

```text
feat(geostories): add hero image storage contract

Add GeoStory hero_image and hero_image_alt fields with deterministic
upload path strategy using Django ImageField-backed storage.
```

---

### Task 9.2: Strict Image Validation Policy

**Branch**: `feat/image-validation-policy`

**Description**: Implement strict image validation rules for both GeoStory hero images and EditorJS image uploads. Validation must be server-side and enforce a shared policy.

**Changes**:

- [ ] Implement shared validation utility for image files and metadata
- [ ] Enforce MIME allowlist: `image/jpeg`, `image/png`, `image/webp`
- [ ] Enforce max file size: `8 MB`
- [ ] Enforce dimensions: min `800x450`, max `6000x6000`
- [ ] Enforce required alt text (hero image and EditorJS image block)
- [ ] Return clear validation errors for each failure type

**Acceptance Criteria**:

- [ ] Unsupported MIME types are rejected
- [ ] Files above 8 MB are rejected
- [ ] Images below min or above max dimensions are rejected
- [ ] Missing/blank alt text is rejected
- [ ] Validation messages identify the exact violated rule

**Tests**:

- [ ] Unit: valid JPEG/PNG/WebP images pass
- [ ] Unit: GIF/SVG and other unsupported formats fail
- [ ] Unit: oversize file fails at >8 MB
- [ ] Unit: `799x449` fails; `800x450` passes
- [ ] Unit: `6001x6001` fails; `6000x6000` passes
- [ ] Unit: empty alt fails for hero and EditorJS image paths

**Commit Message**:

```text
feat(media): enforce strict server-side image validation policy

Add shared validation for mime, size, dimensions, and alt text across
GeoStory hero images and EditorJS image ingestion.
```

---

### Task 9.3: Hero Image API and Admin Surface

**Branch**: `feat/geostory-hero-image-surfaces`

**Description**: Expose hero image fields consistently across API and Django admin so editors can upload/manage hero images and clients can read the image URL + alt text.

**Changes**:

- [ ] Add hero image fields to GeoStory write serializer
- [ ] Add hero image URL + alt to GeoStory list/detail serializers
- [ ] Ensure serializer validation invokes model/validation policy for hero image
- [ ] Update GeoStory admin form/display to include hero image + alt and preview
- [ ] Update any API examples where GeoStory payloads are shown

**Acceptance Criteria**:

- [ ] GeoStory create/update accepts hero image and alt
- [ ] GeoStory list/detail includes hero image URL and alt fields
- [ ] Invalid hero image payload returns clear 400 response
- [ ] Admin supports upload/edit for hero image and alt

**Tests**:

- [ ] API create test: valid multipart hero image accepted
- [ ] API patch test: replace/remove hero image behaves correctly
- [ ] API response test: list/detail includes expected hero image fields
- [ ] Admin form test: hero image widgets and validation errors render correctly

**Commit Message**:

```text
feat(geostories): expose hero image in API and admin

Wire hero image upload/read fields through serializers and admin with
strict validation and response contract coverage.
```

---

### Task 9.4: Extend EditorJS Canonical Contract With `image` Block

**Branch**: `feat/editorjs-image-block-validation`

**Description**: Extend `tosca_api/apps/core/editorjs.py` to accept, validate, and normalize an EditorJS `image` block while preserving deterministic canonical storage behavior.

**Changes**:

- [ ] Add `image` to allowed block types
- [ ] Define canonical block shape for storage:
  - [ ] `type: "image"`
  - [ ] `data.file.url` (required)
  - [ ] `data.caption` (sanitized inline HTML)
  - [ ] `data.alt` (required non-empty)
  - [ ] `data.mime`, `data.width`, `data.height`
  - [ ] Optional EditorJS booleans: `withBorder`, `withBackground`, `stretched`
- [ ] Strip unknown keys during normalization for deterministic output
- [ ] Enforce URL and metadata constraints aligned with Task 9.2 policy

**Acceptance Criteria**:

- [ ] Valid `image` blocks normalize to canonical shape
- [ ] Missing URL / alt / mime / dimensions fail validation
- [ ] Unsupported URL schemes fail validation
- [ ] Unknown keys are stripped and do not persist
- [ ] Existing non-image block behavior remains unchanged

**Tests**:

- [ ] Unit: valid image block passes and normalizes deterministically
- [ ] Unit: invalid scheme (`javascript:` etc.) fails
- [ ] Unit: missing/blank alt fails
- [ ] Unit: invalid mime or out-of-range dimensions fail
- [ ] Unit: unknown keys removed from normalized output
- [ ] Regression: prior paragraph/list/header/quote/code tests still pass

**Commit Message**:

```text
feat(core): add canonical EditorJS image block validation

Extend editorjs validator/normalizer to support strict image blocks with
required URL, alt, mime, and dimension constraints.
```

---

### Task 9.5: EditorJS Image Upload Endpoint and Storage Contract

**Branch**: `feat/editorjs-image-upload-endpoint`

**Description**: Add authenticated backend upload endpoint(s) for EditorJS image tool. Endpoint returns EditorJS-compatible response payload and stores files via Django media storage.

**Changes**:

- [ ] Add authenticated API endpoint for image upload used by EditorJS admin tool
- [ ] Validate upload with strict policy from Task 9.2
- [ ] Store uploads in namespaced path (e.g. `geocontext/editorjs/%Y/%m/...`)
- [ ] Return EditorJS-compatible payload containing URL and required metadata
- [ ] Document endpoint contract and errors

**Acceptance Criteria**:

- [ ] Authorized users can upload valid image files
- [ ] Unauthorized users receive 401/403
- [ ] Invalid uploads receive structured 400 errors
- [ ] Response contract is compatible with EditorJS image tool expectations
- [ ] Stored file URL is resolvable through configured media backend

**Tests**:

- [ ] API auth test: unauthenticated upload rejected
- [ ] API success test: valid upload returns expected payload shape
- [ ] API validation test: bad mime/size/dimensions/alt rejected
- [ ] API contract test: response keys match tool integration expectations

**Commit Message**:

```text
feat(geocontext): add authenticated EditorJS image upload endpoint

Introduce backend image upload endpoint with strict validation and
EditorJS-compatible response contract for canonical media storage.
```

---

### Task 9.6: Admin EditorJS Image Tool Integration

**Branch**: `feat/editorjs-admin-image-tool`

**Description**: Integrate EditorJS image tool into Django admin GeoContext authoring. Use vendored assets and existing progressive-enhancement widget flow.

**Changes**:

- [ ] Vendor EditorJS image tool asset(s) under `geocontext/editorjs/vendor/`
- [ ] Include upstream license file(s) alongside vendored assets
- [ ] Update `EditorJsWidget.Media` to include image tool script(s)
- [ ] Update `init.js` tool configuration to register image tool
- [ ] Wire image tool uploader config to Task 9.5 endpoint
- [ ] Keep no-JS textarea fallback unchanged

**Acceptance Criteria**:

- [ ] GeoContext admin loads image tool assets without CDN
- [ ] EditorJS image uploads work via backend endpoint
- [ ] Saved EditorJS JSON contains canonical `image` blocks
- [ ] No-JS fallback remains operational

**Tests**:

- [ ] Admin test: widget media includes vendored image tool scripts
- [ ] Admin test: no CDN references introduced
- [ ] Admin integration test: saved JSON contains normalized `image` block
- [ ] Regression: existing admin editor behavior remains intact

**Commit Message**:

```text
feat(admin): integrate EditorJS image tool with backend uploads

Vendor and wire EditorJS image tool into GeoContext admin using the
existing progressive-enhancement flow and canonical JSON persistence.
```

---

### Task 9.7: Regression and Security Test Coverage

**Branch**: `test/image-support-regressions`

**Description**: Add end-to-end regression coverage for hero image + EditorJS image support across model, serializer, API, validator, upload, and admin surfaces.

**Changes**:

- [ ] Add GeoStory model/serializer/API tests for hero image flows
- [ ] Add EditorJS validator tests for `image` block strictness
- [ ] Add upload endpoint auth + validation tests
- [ ] Add admin integration tests for image tool and canonical persistence
- [ ] Add negative tests for unsafe URLs and malformed payloads

**Acceptance Criteria**:

- [ ] All new image-related paths are covered by automated tests
- [ ] Security regressions (unsafe schemes, malformed image data) are explicitly tested
- [ ] Existing non-image behavior remains green

**Tests**:

- [ ] Model tests for hero image acceptance/rejection by strict rules
- [ ] Serializer/API tests for hero image fields and validation errors
- [ ] EditorJS validator tests for valid/invalid `image` blocks
- [ ] Upload endpoint tests for auth, malformed files, and contract
- [ ] Admin tests for image tool asset loading and canonical persistence

**Commit Message**:

```text
test(media): add regression and security coverage for image support

Cover GeoStory hero image and EditorJS image block behavior across
model/API/validator/upload/admin with strict negative-path tests.
```

---

### Task 9.8: Schema, Docs, and Supersession Notes

**Branch**: `docs/phase9-image-contract`

**Description**: Document all new payload contracts and operational notes for image support, including OpenAPI updates and non-destructive supersession notes for any stale image placeholders in older docs.

**Changes**:

- [ ] Update OpenAPI schema examples for GeoStory hero image fields
- [ ] Document EditorJS image block contract in API/docs references
- [ ] Document upload endpoint request/response examples
- [ ] Add migration/rollout notes for environments using local media storage
- [ ] Add non-destructive supersession notes where stale image-related placeholders exist in older tasks/issues docs
- [ ] Do not auto-close unrelated open tasks (especially Phase 8)

**Acceptance Criteria**:

- [ ] API docs show hero image request/response usage
- [ ] EditorJS image block schema is documented with strict rules
- [ ] Upload endpoint contract and error examples are documented
- [ ] Any stale image placeholders are marked "Superseded by Phase 9" (if touched)
- [ ] Existing open tasks remain unchanged unless concretely completed

**Tests**:

- [ ] Docs/schema check: OpenAPI includes new hero image fields
- [ ] Docs/schema check: upload endpoint is present and typed
- [ ] Docs-only review: supersession notes are non-destructive and scoped

**Commit Message**:

```text
docs(phase9): document hero image and EditorJS image contracts

Add OpenAPI/documentation updates for hero image and EditorJS image
upload contracts, plus scoped supersession notes for stale placeholders.
```
