# GitHub Issues for Implementation Tasks

Use this file to create GitHub issues for each task. Copy the content below into GitHub.

---

## Phase Tracking (Epics)

Use these "Epic" issues to track overall progress for a Phase. They won't be closed until all sub-tasks are done.

### Issue: Phase 0 - Infrastructure Setup

**Labels**: `epic`, `infrastructure`

**Goal**: Prepare the backend for advanced geospatial capabilities.

**Context**:
Currently, the TOSCA backend uses a standard PostgreSQL configuration. To support features like GeoStories and flexible feedback forms, we need to upgrade our stack to support **PostGIS** (spatial database) and **GeoDjango** (ORM extensions). This phase lays the groundwork for all future spatial features.

**Key Outcomes**:

- Docker image includes GDAL/GEOS libraries.
- Database engine switched to PostGIS.
- Core app skeletons (`campaigns`, `geocontext`) created for future development.

#### Task List

- [ ] Task 0.1: Add GDAL to Docker
- [ ] Task 0.2: Configure GeoDjango Settings
- [ ] Task 0.3: Verify PostGIS Extension
- [ ] Task 0.4: Campaigns App Skeleton
- [ ] Task 0.5: GeoContext App
- [x] Task 0.6: LayerRef App

---

### Issue: Phase 1 - GeoStory Feature

**Labels**: `epic`, `feature`

**Goal**: Enable administrators to create rich, map-based narratives (GeoStories).

**Context**:
GeoStories are the core content unit of the platform. They combine rich text (via GeoContext) with specific map layers (from GeoServer) to tell a spatial story. This phase implements the full backend lifecycle for these stories, from creation to API delivery.

**Key Outcomes**:

- **Campaign Management**: Grouping stories into broader initiatives.
- **GeoStory CRUD**: Full API to create, edit, and publish stories.
- **Layer Integration**: Syncing and linking WMS/WFS layers from GeoServer.
- **Linking**: Ability to link stories to other content via `FeatureLink`.

#### Task List

- [x] Task 1.1: GeoStory Model
- [x] Task 1.2: Campaign API
- [x] Task 1.2a: API Documentation
- [ ] Task 1.2b: Authentication API Docs Fix
- [x] Task 1.3a: GeoStory Basic API
- [ ] Task 1.3b: GeoStory Nested Writes API
- [ ] Task 1.4a: LayerRef Client
- [ ] Task 1.4b: LayerRef Endpoint
- [x] Task 1.5: FeatureLink App
- [x] Task 1.6: Enhanced GeoStory API

---

### Issue: Phase 2 - CalendarEvent Feature

**Labels**: `epic`, `feature`

**Goal**: Integrate time-bound events into the spatial platform.

**Context**:
Events are temporal points of interest (meetups, town halls, deadlines) associated with a campaign. Unlike static stories, they have a specific start/end time and location. This phase adds the ability to manage and query these events spatially and temporally.

**Key Outcomes**:

- **CalendarEvent Model**: Storing events with `Point` geometry and time ranges.
- **GeoJSON API**: Delivering event data in standard spatial formats for frontend mapping.
- **Filtering**: Querying events by date range and campaign.

#### Task List

- [x] Task 2.1: CalendarEvent Model
- [x] Task 2.2: CalendarEvent API
- [x] Task 2.3: FeatureLink for Events

---

### Issue: Phase 2B - Event Model V2

**Labels**: `epic`, `feature`, `architecture`

**Goal**: Refactor the event domain from the current `CalendarEvent` model into a more flexible event platform supporting dynamic taxonomy, profile binding, separate spatial and online map results, and recurring/batch event series.

**Context**:
The current event implementation is sufficient for basic calendar and map scenarios, but it does not model:

- dynamic classification dimensions
- event-type-to-profile binding
- recurring/batch event generation
- shared-filter map and list outputs
- separate map-screen handling for online events without fake geometry

The v2 model keeps event content, provider data, and location data on the event itself while introducing lightweight structural models (`EventType`, `TaxonomyDimension`, `TaxonomyTerm`, `EventTerm`, `EventSeries`, `EventSeriesDate`) to support more complex product requirements without overloading the core event table.

**Key Outcomes**:

- **Event Core Refactor**: Replace `CalendarEvent` with a generic `Event` core after schema freeze, with `location_mode`, provider fields, series metadata, and optional context enrichment.
- **Dynamic Taxonomy**: Support owner-defined dimensions and hierarchical terms.
- **Profile Binding**: Drive event-type-specific extension models from an `EventType` registry.
- **Dual Output API**: Map and list endpoints share filters but return different response structures.
- **Recurring / Batch Series**: Persist recurring rules and manual series dates while keeping actual event content on occurrence rows.

Pre-production note: because the system is not yet in production, rollout may use a destructive DB reset instead of row-level backfill.

Implementation note after 2B.4:

- The codebase already contains minimal placeholder `EventType` and `EventSeries` models because `2B.2` referenced them before their later schema tasks.
- `Event.context` is already a nullable override `ForeignKey`, not a `OneToOneField`.
- Effective context resolution is already implemented as `event.context -> series.default_context -> none`.
- The required GiST index on `Event.location` is currently satisfied by GeoDjango's spatial index.
- `EventSeries.campaign` and `EventSeries.event_type` are currently nullable at the DB layer for destructive-reset development, but series-linked event validation already requires them.
- Local tests use a persistent `test_tosca` database with `--reuse-db`. After destructive event-schema changes, reset or repair the reusable test DB before trusting failures that mention old event tables or content types.

#### Task List

- [x] Task 2B.1: Replace `CalendarEvent` With `Event`
- [x] Task 2B.2: Add Event Core Fields and Mode Validation
- [x] Task 2B.3: Add Series Default Context and Event Overrides
- [x] Task 2B.4: Add Core Constraints and Indexes
- [x] Task 2B.5: Add Taxonomy Schema and Admin
- [x] Task 2B.6: Add Event-Taxonomy Assignment Rules
- [x] Task 2B.7: Add Event Type Registry and Seeds
- [x] Task 2B.8: Add Profile Extension Models and Compatibility Validation
- [x] Task 2B.9: Add Shared Event Filter Layer
- [x] Task 2B.10: Add Event List API V2
- [x] Task 2B.11: Add Event Map API V2
- [x] Task 2B.12: Add EventSeries and EventSeriesDate Schema
- [x] Task 2B.13: Add Recurrence Generation, Exceptions, and DST Handling
- [x] Task 2B.14: Review Cleanup for Event Filters and Validation

---

### Issue: Phase 3 - GeoFeedback Feature

**Labels**: `epic`, `feature`

**Goal**: Enable citizen participation through spatial feedback forms.

**Context**:
Participation is the final loop of the platform. Users need to provide feedback (ratings, comments, drawings) on specific locations or plans. This phase implements a flexible form system integrating `django-basic-form-builder` alongside native ratings, and a submission system (`FeedbackSubmission`) that handles user inputs along with spatial sketching.

**Key Outcomes**:

- **Configurable Forms**: Admins can toggle ratings, drawings, or link dynamic custom forms via the form builder.
- **Spatial Submissions**: Users can submit points, lines, or polygons along with their feedback.
- **Anonymity**: Support for both authenticated and anonymous participation.

#### Task List

- [x] Task 3.1: Feedback Models
- [x] Task 3.2: Submission Model
- [x] Task 3.3: Feedback API
- [x] Task 3.4: FeatureLink for Feedback

---

### Issue: Phase 5 - Security Hardening

**Labels**: `epic`, `security`

**Goal**: Address security vulnerabilities and harden the application before production deployment.

**Context**:
As the application handles user-generated content (rich HTML in GeoContext) and public APIs, we need to implement security measures to prevent common attack vectors like XSS, CSRF, and API abuse.

**Key Outcomes**:

- **HTML Sanitization**: Prevent XSS attacks via rich content fields.
- **Rate Limiting**: Protect public endpoints from abuse.
- **Security Headers**: Configure CSP, CORS, and other protective headers.

#### Task List

- [ ] Task 5.1: Input Sanitization for All Content
- [ ] Task 5.2: Rate Limiting for Public Endpoints
- [ ] Task 5.3: Security Headers and CORS Hardening

---

## Output of Individual Tasks

### Issue: Task 0.1 - Add GDAL to Docker Image

**Labels**: `infrastructure`, `docker`
**Branch**: `feat/gdal-docker`

**Description**:
Install system dependencies required for GeoDjango. This ensures we can use PostGIS features in the backend.

**Technical Details**:

- **Libraries needed**: `gdal-bin`, `libgdal-dev`, `libgeos-dev`, `libproj-dev`.
- **Target File**: `docker/django/Dockerfile` (install via `apt-get`).

#### Acceptance Criteria

- [ ] `docker compose build django` succeeds
- [ ] Inside container: `gdalinfo --version` returns version info
- [ ] GeoDjango can import GDAL bindings

---

### Issue: Task 0.2 - Configure GeoDjango in Settings

**Labels**: `infrastructure`, `django`
**Branch**: `feat/geodjango-config`

**Description**:
Update Django settings to use the PostGIS database backend and enable GIS apps.

**Technical Details**:

- **Settings**:
  - `INSTALLED_APPS`: Add `django.contrib.gis`.
  - `DATABASES['default']['ENGINE']`: Change to `django.contrib.gis.db.backends.postgis`.
  - Ensure `PSQL_VERSION` in Docker matches.

#### Acceptance Criteria

- [ ] `make up` starts without errors
- [ ] `python manage.py check` passes
- [ ] `SELECT PostGIS_Version();` returns version

---

### Issue: Task 0.3 - Verify PostGIS Extension

**Labels**: `infrastructure`, `testing`
**Branch**: `feat/postgis-verify`

**Description**:
Add a test case to explicitly verify that the PostGIS extension is installed and responding in the database.

**Technical Details**:

- **Test File**: `tosca_api/apps/core/tests/test_postgis.py`.
- **Logic**: Run raw SQL `SELECT PostGIS_Version();` via Django cursor.

#### Acceptance Criteria

- [ ] Test passes: `pytest tosca_api/apps/core/tests/test_postgis.py -v`
- [ ] PostGIS version query works from Django

---

### Issue: Task 0.4 - Create `campaigns` App Skeleton

**Labels**: `feature`, `models`
**Branch**: `feat/campaigns-app`

**Description**:
Initialize the `campaigns` app and implement the core `Campaign` model. This model serves as the parent container for all other features.

**Technical Details**:

- **App**: `tosca_api/apps/campaigns`.
- **Model: `Campaign`**:
  - `id`: UUID (Primary Key).
  - `title`: CharField(255).
  - `summary`: TextField (nullable).
  - `status`: CharField (Draft/Active/Archived).
  - `visibility`: CharField (Public/Private).
  - `created_by`: FK to User.
  - `timestamps`: created_at, updated_at.

#### Acceptance Criteria

- [ ] `python manage.py makemigrations campaigns` succeeds
- [ ] `python manage.py migrate` succeeds
- [ ] Campaign model visible in Django Admin
- [ ] Tests pass (in `apps/campaigns/tests/`)

---

### Issue: Task 0.5 - Create `geocontext` App

**Labels**: `feature`, `models`
**Branch**: `feat/geocontext-app`

**Description**:
Initialize the `geocontext` app. This app holds shared content blocks (text/media) that are linked 1:1 to other features like Stories or Events.

**Technical Details**:

- **App**: `tosca_api/apps/geocontext`.
- **Model: `GeoContext`**:
  - `id`: UUID.
  - `content`: TextField.
  - `content_type`: CharField (choices: simple, rich).
  - `created_by`: FK to User.

#### Acceptance Criteria

- [ ] Migrations apply successfully
- [ ] Model visible in Admin
- [ ] Tests pass (in `apps/geocontext/tests/`)

---

### Issue: Task 0.6 - Create `layerrefs` App

**Labels**: `feature`, `models`
**Branch**: `feat/layerrefs-app`

**Description**:
Initialize the `layerrefs` app. This model acts as a pointer to GeoServer layers, allowing us to link map layers to Stories/Events.

**Technical Details**:

- **App**: `tosca_api/apps/layerrefs`.
- **Model: `LayerRef`**:
  - `id`: UUID.
  - `layer_name`: CharField(255), **unique**.
  - `created_at`: Datetime.

#### Acceptance Criteria

- [ ] Migrations apply
- [ ] LayerRef enforces unique `layer_name`
- [ ] Tests pass (in `apps/layerrefs/tests/`)

---

## Phase 1: GeoStory Feature

### Issue: Task 1.1 - Create `geostories` App with GeoStory Model

**Labels**: `feature`, `models`
**Branch**: `feat/geostories-model`

**Description**:
Create the `geostories` app. Implement the `GeoStory` model which links to `Campaign` (parent) and `GeoContext` (content). Also implement `GeoStoryLayer` for M2M linking to maps.

**Technical Details**:

- **App**: `tosca_api/apps/geostories`.
- **Model: `GeoStory`**:
  - `id`: UUID.
  - `campaign`: FK to Campaign.
  - `title`: CharField.
  - `status`: CharField (Draft/Published/Archived).
  - `author`: FK to User.
  - `context`: OneToOneField to `GeoContext` (nullable).
- **Model: `GeoStoryLayer`** (Through table):
  - `geostory`: FK.
  - `layer`: FK to LayerRef.
  - `display_order`: Integer.

#### Acceptance Criteria

- [ ] GeoStory links to Campaign and GeoContext
- [ ] GeoStoryLayer junction table supports ordering
- [ ] Tests pass (in `apps/geostories/tests/`)

---

### Issue: Task 1.2 - Campaign API Endpoints

**Labels**: `feature`, `api`
**Branch**: `feat/campaigns-api`

**Description**:
Implement REST API endpoints for managing Campaigns. Use DRF ModelViewSet.

**Technical Details**:

- **URL**: `/api/v1/campaigns/`
- **Permissions**: IsAuthenticated (ReadOnly for public if visible).
- **Pagination**: usage of `CursorPagination`.
- **Actions**: List, Retrieve, Create, Update.

#### Acceptance Criteria

- [ ] API returns 401 for unauthenticated requests
- [ ] Authenticated user can create and list campaigns
- [ ] Tests pass (in `apps/campaigns/tests/`)

---

### Issue: Task 1.3a - GeoStory Basic CRUD API

**Labels**: `feature`, `api`
**Branch**: `feat/geostories-api-basic`

**Description**:
Implement basic REST API for GeoStories (Metadata only).

**Technical Details**:

- **URL**: `/api/v1/stories/`.
- **Filters**: Filter by `campaign_id`.
- **Validation**: Ensure `campaign` exists.

#### Acceptance Criteria

- [ ] Can create/list/update GeoStory objects
- [ ] Validation: Title required, Campaign valid
- [ ] Tests pass (in `apps/geostories/tests/`)

---

### Issue: Task 1.3b - GeoStory Nested Writes (Context & Layers)

**Labels**: `feature`, `api`
**Branch**: `feat/geostories-api-nested`

**Description**:
Enhance `GeoStory` API to handle nested creation of `GeoContext` and `Layers` in a single POST/PATCH request.

**Technical Details**:

- **Serializer**: Override `create()`/`update()` in `GeoStorySerializer`.
- **Logic**:
  - If `context` dict present: create/update `GeoContext` model and link it.
  - If `layers` list present: sync `GeoStoryLayer` M2M using `layer_name`.

#### Acceptance Criteria

- [ ] POST includes `context: {"content": "..."}` -> Creates GeoContext
- [ ] POST includes `layers: ["workspace:roads"]` -> Creates GeoStoryLayer links
- [ ] Tests pass (in `apps/geostories/tests/`) for nested creation (atomic transaction)

---

### Issue: Task 1.4a - LayerRef Sync Client

**Labels**: `feature`, `integration`
**Branch**: `feat/layerrefs-client`

**Description**:
Implement a service client to fetch the list of available layers from GeoServer's REST API.

**Technical Details**:

- **File**: `apps/layerrefs/client.py`.
- **Lib**: Use `requests`.
- **Config**: Read `GEOSERVER_URL` and credentials from settings/env.
- **Output**: List of strings `["workspace:layer_name", ...]`.

#### Acceptance Criteria

- [ ] Function returns list of layer names
- [ ] Handles connection errors gracefully
- [ ] Unit tests with mocked responses (no real network calls)

---

### Issue: Task 1.4b - LayerRef Sync Endpoint

**Labels**: `feature`, `api`
**Branch**: `feat/layerrefs-endpoint`

**Description**:
Add an Admin-only endpoint to trigger the Layer Sync process manually.

**Technical Details**:

- **URL**: `POST /api/v1/layers/sync/`.
- **Logic**: Call client -> Get Layers -> Create missing `LayerRef` -> Delete stale `LayerRef`.
- **Response**: `{"added": 5, "removed": 2}`.

#### Acceptance Criteria

- [ ] Only Admin/Editor can call
- [ ] Returns added/removed counts
- [ ] Tests pass (in `apps/layerrefs/tests/`)

---

### Issue: Task 1.5 - Create `featurelinks` App

**Labels**: `feature`, `models`
**Branch**: `feat/featurelinks-app`

**Description**:
Create `featurelinks` app to handle explicit relationships (e.g., Story A links to Story B). This uses Generic relationships (polymorphism).

**Technical Details**:

- **Model: `FeatureLink`**:
  - `source`: GenericForeignKey.
  - `target`: GenericForeignKey.
  - `campaign`: FK (Boundary enforcement).
  - `link_type`: Enum (Direct/ReadMore/Action).
- **Validation**:
  - Source and Target must be in the same `campaign`.
  - Source != Target.

#### Acceptance Criteria

- [ ] Can link GeoStory → GeoStory
- [ ] Validation rejects cross-campaign links
- [ ] Validation rejects self-links
- [ ] Tests pass (in `apps/featurelinks/tests/`)

---

### Issue: Task 1.6 - Enhanced GeoStory API

**Labels**: `feature`, `api`
**Branch**: `feat/geostory-read-api`

**Description**:
Enhance `GeoStory` API to support public consumption with list/detail separation and nested data (Context, Layers, Links). Current API is too flat for frontend use.

**Technical Details**:

- **Serializers**:
  - `GeoStoryListSerializer`: Slim payload (title, summary, cover).
  - `GeoStoryDetailSerializer`: Full payload with nested Context, Layers, Links.
  - `GeoContextSerializer`: Expose `content`.
  - `LayerRefSerializer`: Expose `layer_name`.
  - `FeatureLinkSerializer`: Expose outgoing links.
- **Views**:
  - Update `GeoStoryViewSet` to use `get_serializer_class`.
  - Enforce `status='published'` for List view (filtering).
  - Optimize queries with `select_related`/`prefetch_related`.

#### Acceptance Criteria

- [x] List endpoint returns only Published stories
- [x] List endpoint returns optimized/slim fields
- [x] Detail endpoint returns fully nested Context and Layers
- [x] Detail endpoint returns outgoing FeatureLinks
- [x] Tests verify payload structure

---

## Phase 2: CalendarEvent Feature

### Issue: Task 2.1 - Create `events` App with CalendarEvent Model

**Labels**: `feature`, `models`, `geodjango`
**Branch**: `feat/events-model`

**Description**:
Create `events` app. Implement `CalendarEvent` model with spatial support (`PointField`).

**Technical Details**:

- **App**: `tosca_api/apps/events`.
- **Model: `CalendarEvent`**:
  - `location`: `PointField(srid=4326)`.
  - `start_datetime`, `end_datetime`.
  - `context`: OneToOne to `GeoContext`.
- **Constraints**: `end_datetime >= start_datetime`.

#### Acceptance Criteria

- [ ] PointField migration applies successfully
- [ ] Can save model with SRID 4326 point
- [ ] IntegrityError if end < start
- [ ] Tests pass (in `apps/events/tests/`)

---

### Issue: Task 2.2 - CalendarEvent API Endpoints

**Labels**: `feature`, `api`, `geodjango`
**Branch**: `feat/events-api`

**Description**:
Implement REST API for CalendarEvents. Output must be valid **GeoJSON**.

**Technical Details**:

- **Serializer**: Use `GeoFeatureModelSerializer` (DRF GIS).
- **Filters**:
  - Spatial: Bounding Box (optional).
  - Temporal: `start_after`, `end_before`.

#### Acceptance Criteria

- [ ] GET /api/v1/events/ returns GeoJSON FeatureCollection
- [ ] Can filter: ?start_after=2024-01-01
- [ ] CursorPagination enabled
- [ ] Tests pass (in `apps/events/tests/`)

---

### Issue: Task 2.3 - Extend FeatureLink for Events

**Labels**: `feature`, `testing`
**Branch**: `feat/featurelinks-events`

**Description**:
Ensure `FeatureLink` model works correctly with `CalendarEvent` as both source and target.

**Technical Details**:

- Add validation to `FeatureLink` to allow `CalendarEvent` content type.

#### Acceptance Criteria

- [ ] Tests pass (in `apps/featurelinks/tests/`) for linking events to other features
- [ ] Admin UI allows selecting events in generic relation

---

## Phase 2B: Event Model V2

### Issue: Task 2B.1 - Replace `CalendarEvent` With `Event`

**Labels**: `feature`, `models`, `migration`, `geodjango`
**Branch**: `feat/event-v2-rename`

**Description**:
Replace `CalendarEvent` with `Event` after schema freeze and update all direct dependencies in one sweep.

**Technical Details**:

- Replace the Django model name and ORM references
- Update `FeatureLink` allowed content types and generic relation names
- Update serializers, viewsets, admin, tests, and docs that still reference `CalendarEvent`
- Pre-production destructive schema reset is acceptable; no row-level backfill is required.

#### Acceptance Criteria

- [x] No `CalendarEvent` references remain in the event app or `FeatureLink`
- [x] Event CRUD still works under the new `Event` model name
- [x] Tests covering event creation and linking pass

---

### Issue: Task 2B.2 - Add Event Core Fields and Mode Validation

**Labels**: `feature`, `models`, `validation`
**Branch**: `feat/event-v2-core-fields`

**Description**:
Add the new core fields required for online, hybrid, and provider-aware events.

**Technical Details**:

- Add `event_type`, `location_mode`, online fields, provider fields, `series`, `occurrence_index`, `is_exception`, and `original_start_datetime`
- Enforce `physical`, `hybrid`, and `online` validation rules
- Keep event context optional
- Keep concrete event datetimes on `TIMESTAMPTZ`
- `EventType` and `EventSeries` were introduced as minimal placeholders here because the original task order referenced them before `2B.7` and `2B.12`
- Online access validation currently accepts either `online_url` or `online_platform`

#### Acceptance Criteria

- [x] Online events allow `location=NULL` and require online access data
- [x] Hybrid events require both geometry and online access data
- [x] Standalone events can exist without context
- [x] Mode validation tests pass

---

### Issue: Task 2B.3 - Add Series Default Context and Event Overrides

**Labels**: `feature`, `models`
**Branch**: `feat/event-v2-context`

**Description**:
Implement the final shared-content model using series defaults plus per-occurrence overrides.

**Technical Details**:

- Add `EventSeries.default_context`
- Keep `Event.context` as an optional override
- Implement effective context resolution as `event.context -> series.default_context -> none`
- Events without effective context remain valid
- `Event.context` is now a nullable `ForeignKey` override rather than a `OneToOneField`
- The detail serializer returns the resolved effective context

#### Acceptance Criteria

- [x] Series events can resolve shared context from `EventSeries.default_context`
- [x] Individual occurrences can override shared context through `Event.context`
- [x] Published events remain valid without any effective context
- [x] Context resolution tests pass

---

### Issue: Task 2B.4 - Add Core Constraints and Indexes

**Labels**: `feature`, `models`, `geodjango`
**Branch**: `feat/event-v2-core-constraints`

**Description**:
Add the minimum production-ready constraints and indexes for the event core.

**Technical Details**:

- Add `end_datetime >= start_datetime`
- Add series-linked invariants for `campaign` and `event_type`
- Add GiST index on `location`
- Add b-tree indexes on `(campaign_id, status, start_datetime)`, `(event_type_id, start_datetime)`, `(location_mode, start_datetime)`, `(series_id, start_datetime)`
- Add unique partial index on `(series_id, occurrence_index)` where `series_id IS NOT NULL`
- The current implementation satisfies the GiST requirement through GeoDjango's spatial index on `Event.location`
- `EventSeries` now carries `campaign` and `event_type`; both remain nullable at the DB layer for destructive-reset development, but attached events are validated against populated values

#### Acceptance Criteria

- [x] Series-linked events reject `campaign` or `event_type` changes while attached
- [x] Duplicate `(series_id, occurrence_index)` values are rejected
- [x] Schema contains the agreed minimum index set
- [x] Constraint tests pass

---

### Issue: Task 2B.5 - Add Taxonomy Schema and Admin

**Labels**: `feature`, `models`, `taxonomy`
**Branch**: `feat/event-v2-taxonomy-schema`

**Description**:
Add dynamic taxonomy models and admin configuration.

**Technical Details**:

- Add `TaxonomyDimension`, `TaxonomyTerm`, and `EventTerm`
- Support `single` vs `multiple` selection modes
- Support parent-child terms inside a dimension
- Add admin configuration for dimensions and terms
- Add unique `TaxonomyTerm(dimension_id, code)` and unique `EventTerm(event_id, term_id)`
- Reuse the existing `EventType` and `EventSeries` models already present in the app; do not introduce replacement models while adding taxonomy support
- `TaxonomyDimension`, `TaxonomyTerm`, and `EventTerm` now exist in the live schema, and `EventTerm` should be reused in `2B.6` rather than replaced by another relation model
- Single-select assignment enforcement remains intentionally deferred to `2B.6`

#### Acceptance Criteria

- [x] Admin can create dimensions and terms
- [x] Parent term validation rejects cross-dimension hierarchies
- [x] Duplicate term assignment is rejected
- [x] Schema and admin tests pass

---

### Issue: Task 2B.6 - Add Event-Taxonomy Assignment Rules

**Labels**: `feature`, `api`, `taxonomy`
**Branch**: `feat/event-v2-taxonomy-assignment`

**Description**:
Implement assignment-time validation and query support for event taxonomy.

**Technical Details**:

- Enforce single-select dimension rules on `EventTerm`
- Add index `EventTerm(term_id, event_id)` for filtering
- Add serializer/admin validation for event-term assignment
- Add API/query support for filtering by term and dimension
- Build on the existing `EventTerm` table and admin exposure introduced in `2B.5`
- The current event API now uses `term_id` and `dimension_id` for taxonomy filtering, and write operations accept `taxonomy_term_ids`

#### Acceptance Criteria

- [x] Single-select dimensions reject conflicting assignments
- [x] Event-term assignment works from admin and API
- [x] Event filtering by taxonomy terms works
- [x] Assignment tests pass

---

### Issue: Task 2B.7 - Add Event Type Registry and Seeds

**Labels**: `feature`, `models`, `validation`
**Branch**: `feat/event-v2-types`

**Description**:
Introduce the `EventType` registry as the source of truth for event-type behavior.

**Technical Details**:

- Add `EventType` with `code`, `label`, `profile_mode`, `profile_key`, and `is_active`
- Seed `general`, `public_health`, `sports`, and `culture`
- Add admin support for event type management
- Extend the existing placeholder `EventType` model in place rather than replacing it
- Because the event rollout is destructive-reset-based, reshape the placeholder schema directly instead of planning compatibility backfills
- `EventType` now enforces the registry invariant: `core` rows must not carry a `profile_key`, while `extension` rows must define one
- The seed migration now creates the canonical bindings: `general=core`, `public_health=extension/public_health`, `sports=extension/sports`, `culture=extension/culture`

#### Acceptance Criteria

- [x] Event types can be created and managed in admin
- [x] Seeded event types exist and match the agreed profile bindings
- [x] Tests for registry creation and seed data pass

---

### Issue: Task 2B.8 - Add Profile Extension Models and Compatibility Validation

**Labels**: `feature`, `models`, `validation`
**Branch**: `feat/event-v2-profiles`

**Description**:
Add extension profile tables and validate them against `EventType`.

**Technical Details**:

- Add `PublicHealthEventProfile`, `SportsEventProfile`, and `CultureEventProfile`
- Validate `profile_mode=core` vs `profile_mode=extension`
- Reject mismatched event-type/profile combinations
- Reuse the `EventType` registry contract established in `2B.7` instead of redefining profile bindings in code
- Profile compatibility is now enforced from the event type registry itself: `profile_mode=core` rejects extension rows, and `profile_mode=extension` accepts only the matching `profile_key`

#### Acceptance Criteria

- [x] Core event types work without extension rows
- [x] Extension types reject mismatched profile rows
- [x] Profile compatibility tests pass

---

### Issue: Task 2B.9 - Add Shared Event Filter Layer

**Labels**: `feature`, `api`, `geodjango`
**Branch**: `feat/event-v2-filters`

**Description**:
Centralize event filtering so list and map endpoints share one contract.

**Technical Details**:

- Add shared parsing/validation for campaign, date range, status, visibility, taxonomy, `include_past`, and spatial filters
- Make spatial predicates apply only to `physical` and `hybrid`
- Keep `online` events subject to all non-spatial filters
- Preserve the `term_id` and `dimension_id` taxonomy filter semantics already introduced on the current event endpoints
- The shared filter helper is now in place and reused by both list and polygon/bbox query paths
- Until `2B.11` lands, eligible online events remain visible in the current GeoJSON responses as features with `null` geometry

#### Acceptance Criteria

- [x] List and map endpoints can reuse the same filter layer
- [x] Spatial filters exclude out-of-area `physical`/`hybrid` events
- [x] Eligible `online` events remain included after non-spatial filtering
- [x] Filter-layer tests pass

---

### Issue: Task 2B.10 - Add Event List API V2

**Labels**: `feature`, `api`
**Branch**: `feat/event-v2-list-api`

**Description**:
Build the list endpoint as one chronological mixed stream using `location_mode`.

**Technical Details**:

- Add `GET /api/v1/events/list/`
- Return one paginated stream ordered by `start_datetime`
- Include `location_mode` in each item
- Reuse the shared event filter layer
- The dedicated list endpoint now stays in JSON form even when spatial filters are supplied
- The legacy `/api/v1/events/` route still exists and still flips to GeoJSON on bbox requests; the new list contract lives on `/api/v1/events/list/`

#### Acceptance Criteria

- [x] List endpoint returns `physical`, `hybrid`, and `online` events in one chronological stream
- [x] Filtering semantics match the shared contract
- [x] Pagination works as expected
- [x] List API tests pass

---

### Issue: Task 2B.11 - Add Event Map API V2

**Labels**: `feature`, `api`, `geodjango`
**Branch**: `feat/event-v2-map-api`

**Description**:
Build the map endpoint with separate spatial and online result buckets.

**Technical Details**:

- Add `GET /api/v1/events/map/`
- Return `spatial_events` as GeoJSON for `physical` and `hybrid`
- Return `online_events` as a separate JSON array
- Reuse the shared event filter layer
- The dedicated map endpoint now owns the v2 bucketed map response contract while the legacy bbox route remains in place for backward compatibility
- `spatial_events` now contains only mapped `physical` and `hybrid` events, and `online_events` carries the separately returned `online` matches

#### Acceptance Criteria

- [x] Map endpoint returns valid GeoJSON in `spatial_events`
- [x] Map endpoint returns `online_events` separately from spatial GeoJSON
- [x] Spatial filtering behavior matches the agreed semantics
- [x] Map API tests pass

---

### Issue: Task 2B.12 - Add EventSeries and EventSeriesDate Schema

**Labels**: `feature`, `models`, `recurrence`
**Branch**: `feat/event-v2-series-schema`

**Description**:
Add the structural models required for batch and recurring event generation.

**Technical Details**:

- Create `EventSeries`
- Create `EventSeriesDate`
- Add unique `EventSeriesDate(series_id, occurrence_date)` constraint
- Add recurrence field validation for daily, weekly, and monthly rules
- Extend the existing `EventSeries` table in place rather than introducing a new grouping model
- After the destructive reset for the new event system, revisit whether `EventSeries.campaign` and `EventSeries.event_type` should be tightened from nullable to non-nullable at the schema level
- The live `EventSeries` table now carries recurrence, schedule, timezone, and creator fields, and `EventSeriesDate` now persists explicit manual-batch dates
- The "manual batch must already have at least one date" rule remains deferred to the creation/generation flow rather than raw model save

#### Acceptance Criteria

- [x] Manual batch series can persist explicit dates
- [x] Weekly/monthly validation rules are enforced
- [x] Duplicate manual batch dates are rejected
- [x] Series schema tests pass

---

### Issue: Task 2B.13 - Add Recurrence Generation, Exceptions, and DST Handling

**Labels**: `feature`, `api`, `recurrence`
**Branch**: `feat/event-v2-series-generation`

**Description**:
Implement generation, exception behavior, and local-time recurrence semantics.

**Technical Details**:

- Add preview and creation flow for manual and recurring series
- Persist generated events with `series_id`, `occurrence_index`, and `original_start_datetime`
- Mark diverging occurrences as `is_exception`
- Update only future non-exception occurrences by default
- Generate occurrences in local wall time using `EventSeries.timezone`
- Build on the `EventSeries` and `EventSeriesDate` schema already introduced in `2B.12`

#### Acceptance Criteria

- [x] Recurring weekly series generate correct occurrences
- [x] Weekly recurring series preserve the same local wall time across DST boundaries
- [x] Exception occurrences are skipped by default during future bulk updates
- [x] Generation and exception-handling tests pass

**Implementation Notes**:

- Added the dedicated recurrence API surface at `POST /api/v1/event-series/preview/`, `POST /api/v1/event-series/`, and `PATCH /api/v1/event-series/{id}/`.
- Generation runs through a shared recurrence service that produces local-time occurrences in the `EventSeries.timezone`, then persists or synchronizes `Event` rows with `series_id`, `occurrence_index`, and `original_start_datetime`.
- Direct edits to series-generated events through `/api/v1/events/{id}/` now mark the occurrence as `is_exception=True` when schedule/content/location or taxonomy data diverges from the generated row.
- Future bulk updates skip exception rows by default and only update/delete future non-exception occurrences.
- Recurring generation is currently same-day only. The existing `EventSeries.end_date` field is being used as the recurrence termination boundary, so multi-day recurring occurrence duration would need separate schema support later.
- Series `campaign` and `event_type` changes are rejected once occurrences exist so attached exception rows cannot drift from the series identity.

---

### Issue: Task 2B.14 - Review Cleanup for Event Filters and Validation

**Labels**: `cleanup`, `api`, `validation`
**Branch**: `feat/event-v2-review-cleanups`

**Description**:
Apply the concrete Phase 2B follow-up improvements identified during implementation review without expanding scope into broader authorization work.

**Technical Details**:

- Confirm that the canonical `EventType` seed rows from `2B.7` are already backed by a real `RunPython` migration
- Replace `EventLayer.unique_together` with `UniqueConstraint`
- Move `VALID_WEEKDAYS` ahead of `EventSeries` for clarity
- Add serializer-level GeoJSON `Point` validation for event-series template `location`
- Auto-assign `EventSeriesDate.display_order` when omitted
- Add `event_type_id` to the shared event filter contract used by list/map/within endpoints
- Avoid unnecessary FK object comparison in series-occurrence exception detection

#### Acceptance Criteria

- [x] Event-series preview/create returns clear 400 errors for malformed or non-point location GeoJSON
- [x] Shared event filtering supports `event_type_id`
- [x] `EventLayer` uniqueness is expressed with `UniqueConstraint`
- [x] `EventSeriesDate` can be created without explicitly setting `display_order`
- [x] Cleanup tests pass

**Implementation Notes**:

- The `2B.7` event type seeds are confirmed in `events/migrations/0007_eventtype_is_active_eventtype_profile_key_and_more.py` through `RunPython(seed_event_types, unseed_event_types)`.
- The `event_type_id` filter now flows through the shared filter serializers and `apply_event_filters()`, so legacy list, list v2, map v2, and geometry-based endpoints all share the same contract.
- Queryset deletion in recurrence sync remains as-is because `Event` still does not implement custom delete-side cleanup.
- Role-based permissions remain intentionally out of scope for this cleanup and should be handled as a separate authorization task.

---

### Issue: Task 2B.15 - Add Admin Event-Series Authoring and Occurrence Generation

**Labels**: `feature`, `admin`, `recurrence`, `testing`
**Branch**: `feat/event-series-admin-generation`

**Description**:
Extend Django admin so admins can create and update event series from the admin UI and have occurrence events generated or synchronized with the same semantics already used by the recurrence API.

**Technical Details**:

- Treat this as admin parity for the existing recurrence feature, not a second independent recurrence implementation.
- Extend the `EventSeries` admin authoring surface to collect the required event-template fields currently only provided through `POST /api/v1/event-series/` and `PATCH /api/v1/event-series/{id}/`.
- Required admin template coverage should include at least:
  - `title`
  - `description`
  - `location_mode`
  - `location`
  - `online_url`
  - `online_platform`
  - `access_notes`
  - `provider_name`
  - `provider_url`
  - `provider_contact`
  - `status`
  - `visibility`
  - `context`
  - taxonomy term selection for generated occurrences
- Reuse the existing recurrence generation and sync logic instead of duplicating recurrence math or exception-handling rules inside admin code.
- Extract or introduce a shared orchestration layer if needed so admin and API can both call the same validation/generation flow without fabricating a fake API request.
- Ensure admin create runs generation only after inline `EventSeriesDate` rows have been saved, because manual-batch series depend on inline explicit dates.
- Ensure admin update synchronizes future non-exception occurrences by default, matching the existing API semantics.
- Preserve the current rule that `campaign` and `event_type` cannot change once occurrences already exist.
- Prevent duplicate occurrence creation when an existing generated series is re-saved from admin; synchronization must remain keyed by `occurrence_index`.
- Surface validation failures in the admin form flow rather than partially saving a series without its generated events.
- Wrap series save, inline date persistence, and occurrence generation/synchronization in one transaction so admin does not leave half-written data behind on failure.
- Support editing existing series whose template must be derived from an existing non-exception occurrence because `EventSeries` itself does not persist the full event template.
- Define the fallback behavior for legacy series rows that have no occurrences yet:
  - either require admins to supply the missing template fields before generation
  - or block sync with a clear admin validation error
- Keep raw model saves side-effect free outside the explicit admin/API authoring flows; do not silently generate events on every `EventSeries.save()` call.

**Edge Cases / Risks**:

- Manual-batch admin create with no inline dates.
- Manual-batch admin update that adds, removes, or reorders explicit dates.
- Weekly recurrence missing `by_weekday`.
- Monthly recurrence with incomplete rule inputs.
- Online, physical, and hybrid template validation mismatches.
- Invalid or non-point GeoJSON for admin-entered location data.
- Existing future exception occurrences must remain untouched during admin bulk edits.
- Existing past occurrences must not be recreated or overwritten during admin sync.
- Timezone or start-time edits across DST boundaries must preserve local wall-time recurrence behavior.
- Existing API-created series re-saved in admin must not create duplicate events.
- Existing series with no remaining events must still produce predictable validation behavior.
- Taxonomy term selections must still enforce single-select dimension rules.

#### Acceptance Criteria

- [x] Admin can create a recurring or manual-batch series and immediately get generated `Event` rows
- [x] Admin can edit a generated series and synchronize future non-exception occurrences without duplicating rows
- [x] Manual-batch admin authoring supports inline explicit dates
- [x] Admin validation enforces the same event-template and recurrence rules as the API flow
- [x] Exception occurrences remain preserved by default during admin bulk edits
- [x] Legacy or incomplete series states fail with clear admin errors instead of silent partial saves
- [x] Admin generation and sync behavior is covered by automated tests

**Implementation Notes**:

- The current recurrence API is the only path that creates/synchronizes occurrence rows today; admin currently saves only the `EventSeries` model and its inline dates.
- `EventSeries` is intentionally lightweight and does not store all event-template fields, so admin update behavior must derive defaults from an existing occurrence or require explicit replacement input.
- The admin hook should likely run after `save_related()` rather than only in `save_model()` so inline explicit dates are available for manual-batch generation.
- If sharing the current serializer logic directly proves awkward, extract the orchestration currently embedded in `EventSeriesWriteSerializer` into a service that both the API and admin can call.

---

### Issue: Task 2B.16 - Replace Term-Centric Event Taxonomy Writes with Dimension Assignments

**Labels**: `feature`, `api`, `taxonomy`, `testing`
**Branch**: `feat/event-taxonomy-dimension-assignments`

**Description**:
Refactor event and event-series taxonomy writes from raw `taxonomy_term_ids` lists to a grouped `taxonomy_assignments` contract so taxonomy behaves like optional event attributes rather than a low-level join-table payload.

**Technical Details**:

- This task should build on the shared authoring flow extracted or stabilized in `2B.15`.
- Replace `taxonomy_term_ids` on event and event-series writes with a grouped shape keyed by dimension, for example:
  - `taxonomy_assignments: [{dimension_id, term_ids[]}]`
- No backward-compatibility shim is required because the taxonomy authoring contract is not yet published.
- Introduce a shared taxonomy parser/validator that can be reused by API serializers, admin forms, and series orchestration.
- Validation rules should include:
  - taxonomy is optional for all event statuses
  - each dimension appears at most once in the payload
  - each term appears at most once within its dimension
  - terms must belong to the stated dimension
  - only active dimensions and active terms may be assigned in new writes
  - only leaf terms may be assigned
  - `single` dimensions may have at most one assigned term
- Keep `EventTerm` as the only persistence model for event taxonomy; this task should not denormalize taxonomy onto `Event` or `EventSeries`.

#### Acceptance Criteria

- [x] Event writes accept grouped taxonomy assignments instead of `taxonomy_term_ids`
- [x] Event-series writes accept the same grouped taxonomy assignment shape
- [x] Taxonomy remains optional for draft and published events
- [x] Leaf-only and selection-mode validation rules are enforced consistently
- [x] `EventTerm` remains the storage layer for event taxonomy
- [x] Automated tests cover grouped taxonomy validation for both event and event-series writes

**Implementation Notes**:

- The grouped assignment validator should be implemented once and reused everywhere rather than duplicating validation in multiple serializers/forms.
- Validation errors should be phrased around dimensions and assignments, not around raw join-table operations.
- Existing taxonomy filter semantics (`term_id`, `dimension_id`) can remain unchanged for reads unless changed by a later task.
- The live write contract now accepts `taxonomy_assignments` on both event and event-series writes, and the old `taxonomy_term_ids` write field has been removed rather than retained behind a compatibility layer.

---

### Issue: Task 2B.17 - Add Dimension-Based Taxonomy Hydration for Event and Series Authoring

**Labels**: `feature`, `admin`, `api`, `taxonomy`, `testing`
**Branch**: `feat/event-taxonomy-authoring-hydration`

**Description**:
Expose grouped taxonomy assignments on event and event-series reads, and surface taxonomy in admin authoring flows as dimension-based optional attributes instead of direct `EventTerm` maintenance.

**Technical Details**:

- This task depends on `2B.15` for shared series-authoring orchestration and on `2B.16` for the grouped taxonomy-assignment contract.
- Add read helpers/serializers so event detail responses return grouped taxonomy assignments for edit hydration.
- Add the same grouped taxonomy representation to event-series retrieve responses, deriving values from the base non-exception occurrence/template rather than adding new taxonomy columns to `EventSeries`.
- Extend `EventAdminForm` to render taxonomy grouped by active dimension and write through the shared grouped-assignment validator.
- Extend `EventSeriesAdminForm` to expose the same taxonomy authoring section for generated occurrences.
- Keep `EventTermAdmin` as a low-level maintenance/debug entry point only; it should no longer represent the intended authoring flow.
- Define clear failure behavior for legacy series with no usable base occurrence/template from which taxonomy defaults can be derived.
- Preserve the `2B.15` recurrence sync semantics:
  - future non-exception occurrences are updated
  - future exceptions are preserved by default
  - past occurrences remain untouched

#### Acceptance Criteria

- [x] Event detail responses expose grouped taxonomy assignments suitable for edit hydration
- [x] Event-series retrieve responses expose grouped taxonomy assignments derived from the series template/base occurrence
- [x] Admin event create/edit manages taxonomy by dimension rather than through direct `EventTerm` editing
- [x] Admin event-series create/edit manages taxonomy by dimension for generated occurrences
- [x] Legacy series states without a usable base occurrence/template fail with actionable errors
- [x] Automated tests cover grouped taxonomy hydration and admin authoring behavior

**Implementation Notes**:

- `EventSeries` should remain lightweight; do not introduce a second persistent taxonomy representation on the series row unless a later task explicitly changes that decision.
- Responses should use the same grouped taxonomy shape as writes so admin/frontend hydration is straightforward.
- When an event or series references inactive already-assigned terms, edit flows should display those assignments predictably even if new writes reject newly selecting inactive terms.
- Admin now renders taxonomy as dynamic per-dimension fields on event and event-series forms while leaving `EventTermAdmin` as a low-level maintenance surface.

---

## Phase 3: GeoFeedback Feature

### Issue: Task 3.1 - Create `feedback` App with Core Models

**Labels**: `feature`, `models`
**Branch**: `feat/feedback-models`

**Description**:
Create `feedback` app. Implement the `GeoFeedback` container linking to dynamic forms and setting configuration flags for ratings and drawings.

**Technical Details**:

- **App**: `tosca_api/apps/feedback`.
- **Dependencies**: Install `django-basic-form-builder` and add to INSTALLED_APPS.
- **Model: `GeoFeedback`**:
  - `rating_enabled` (bool).
  - `form_enabled` (bool).
  - `allow_drawings` (bool).
  - `custom_form`: FK to `formbuilder.CustomForm` (nullable).
  - `campaign`: FK to `Campaign`.
- **Constraints**: At least one mode (rating or form) must be enabled. If form is enabled, `custom_form` must not be null.

#### Acceptance Criteria

- [ ] Admin can create CustomForms via form builder and link them to GeoFeedback
- [ ] GeoFeedback model supports configuration flags and proper validation
- [ ] Tests pass (in `apps/feedback/tests/`)

---

### Issue: Task 3.2 - Add FeedbackSubmission Model

**Labels**: `feature`, `models`, `geodjango`
**Branch**: `feat/feedback-submission`

**Description**:
Implement `FeedbackSubmission` model to store user responses. This stores dynamic data, optional geometry, and strict native ratings.

**Technical Details**:

- **Model: `FeedbackSubmission`**:
  - `feedback`: FK to `GeoFeedback`.
  - `rating`: Integer (1-5).
  - `form_data`: JSONB (for dynamic answers matching CustomForm).
  - `geometry`: GeometryField (Point/Line/Poly) - user sketch.
  - `is_anonymized`: Boolean.

#### Acceptance Criteria

- [ ] Can store submissions with or without User
- [ ] Can store geometry (Point/Line/Poly)
- [ ] JSONB field accepts arbitrary dict for formbuilder schema answers
- [ ] Tests pass (in `apps/feedback/tests/`)

---

### Issue: Task 3.3 - GeoFeedback API Endpoints

**Labels**: `feature`, `api`
**Branch**: `feat/feedback-api`

**Description**:
Implement API for managing Feedback definitions and accepting submissions.

**Technical Details**:

- **Endpoints**:
  - `GET /api/v1/feedback/` (List active feedback forms). Include linked `CustomForm` API endpoint URL.
  - `POST /api/v1/feedback/{id}/submit/` (Custom Action).
- **Submission Logic**:
  - Validate `rating` if `rating_enabled`.
  - Validate `form_data` matching form schema if `form_enabled`.
  - Validate `geometry` if `allow_drawings`.

#### Acceptance Criteria

- [ ] Admin can create/update Feedback forms
- [ ] Anonymous user can submit if allowed
- [ ] Submission validates against configuration (strictly enforcing the config flags)
- [ ] CursorPagination for Feedback list
- [ ] Tests pass (in `apps/feedback/tests/`)

---

### Issue: Task 3.4 - Extend FeatureLink for Feedback

**Labels**: `feature`, `testing`
**Branch**: `feat/featurelinks-feedback`

**Description**:
Ensure `FeatureLink` works with `GeoFeedback`. This allows connecting a User Story to a Feedback form (e.g., "Rate this proposal").

**Technical Details**:

- Add validation to `FeatureLink` to allow `GeoFeedback` content type.

#### Acceptance Criteria

- [ ] Can link Story -> Feedback
- [ ] Tests pass (in `apps/featurelinks/tests/`)
- [ ] Admin UI generic relation picker includes GeoFeedback

---

## Phase 4: Production Readiness

### Issue: Task 4.1a - Configure PgBouncer

**Labels**: `infrastructure`, `performance`
**Branch**: `chore/pgbouncer-infra`

**Description**:
Add PgBouncer to the production Docker stack to pool database connections. PostGIS connections are heavy; pooling is essential for scale.

**Technical Details**:

- **Docker**: Add `pgbouncer` container.
- **Config**: Mount `pgbouncer.ini`.
- **Django**: Update `DATABASE_URL` to point to pgbouncer port (6432) in `settings/prod.py`.

#### Acceptance Criteria

- [ ] `docker compose -f docker-compose.prod.yml config` is valid
- [ ] Application connects via pooler

---

### Issue: Task 4.1b - Verify Spatial Indexes

**Labels**: `infrastructure`, `performance`
**Branch**: `chore/verify-indexes`

**Description**:
Create a management command to audit the database and ensure all geometry columns have a GIST index. Missing indexes ruin spatial query performance.

**Technical Details**:

- **Command**: `manage.py check_spatial_indexes`.
- **Logic**: Inspect `pg_indexes` system view for columns implementing `models.GeometryField`.

#### Acceptance Criteria

- [ ] Command reports OK for current schema
- [ ] Fails if we drop an index manually

---

### Issue: Phase 7 - GeoContext Editor.js Migration

**Labels**: `epic`, `content`, `admin`, `migration`

**Goal**: Replace the legacy GeoContext text-plus-`content_type` authoring model with canonical Editor.js JSON, deterministic validation and normalization, Django admin authoring, and a staged migration path.

**Context**:
The original GeoContext design stored either simple text or rich HTML in a single text field with a `content_type` discriminator. Future implementation will supersede that approach with canonical Editor.js JSON so content structure is explicit, normalization is deterministic, admin authoring is improved, and staged migration can proceed without ambiguity.

**Key Outcomes**:

- **Canonical JSON Contract**: GeoContext content is structured Editor.js JSON instead of text-plus-`content_type`.
- **Deterministic Validation**: Future writes pass through strict block-schema validation and normalization.
- **Admin Authoring**: Django admin gains Editor.js progressive enhancement with a raw JSON fallback.
- **Safe Migration**: Preflight scanning and staged releases handle legacy content safely, aborting on media-bearing rows.

#### Task List

- [x] Task 7.1: Canonical GeoContext Editor.js Contract
- [x] Task 7.2: Editor.js Validation and Normalization Layer
- [x] Task 7.3: Django Admin Editor.js Authoring
- [x] Task 7.4: GeoContext Preflight and HTML-to-Blocks Backfill
- [x] Task 7.5: Release 2 Switch to `content_json`
- [x] Task 7.6: Release 3 Drop Legacy GeoContext Fields

---

### Issue: Task 7.1 - Canonical GeoContext Editor.js Contract

**Labels**: `feature`, `content`, `api`
**Branch**: `feat/geocontext-editorjs-contract`

**Description**:
Replace the legacy `GeoContext` string-plus-`content_type` contract with canonical Editor.js JSON. `GeoContext.content` becomes JSON-backed content, `content_type` disappears from the future contract, and all nested read surfaces expose `context.content` as JSON.

**Technical Details**:

- **Model Surface**: `GeoContext.content` becomes the canonical Editor.js JSON field for future implementation.
- **Removed Field**: `content_type` is removed from the future model, serializer, admin, and schema contract.
- **Read Surfaces**: Update nested GeoContext serializer contracts in `geostories`, `events`, and `feedback`.
- **Empty State**: Empty documents must be represented as `{ "blocks": [] }`, not `null`, blank strings, or omitted fields.
- **Supersession Note**: This issue supersedes the original GeoContext `content` + `content_type` design for future implementation.

#### Acceptance Criteria

- [x] `GeoContext.content` is defined as JSON-backed content rather than freeform text
- [x] `content_type` is removed from the future model, serializers, admin, and schema
- [x] All nested `GeoContext` read surfaces in events, geostories, and feedback describe `context.content` as JSON
- [x] Empty content is represented as `{ "blocks": [] }`, not `null`, `""`, or omitted
- [x] Historical `content_type` references in future-oriented docs for this feature are removed or marked superseded

---

### Issue: Task 7.2 - Editor.js Validation and Normalization Layer

**Labels**: `feature`, `content`, `security`
**Branch**: `feat/editorjs-validation`

**Description**:
Introduce a dedicated `core/editorjs.py` validation and normalization layer for GeoContext Editor.js documents. This layer is responsible for block schema enforcement, semantic tag normalization, inline HTML sanitization, and deterministic canonical storage.

**Technical Details**:

- **Module**: `tosca_api/apps/core/editorjs.py`.
- **Allowed Blocks**: `paragraph`, `header`, `list`, `quote`, `delimiter`, `code`.
- **Allowed Inline Formatting**: `a`, `strong`, `em`, `code`, `br`.
- **Normalization Rules**:
  - Strip `time`, `version`, and block `id` from stored content.
  - Normalize `<b>` to `<strong>` and `<i>` to `<em>`.
  - Reject `quote.alignment`.
  - Allow `list.meta` only as `{}` for MVP.
- **Validation Behavior**: Unsafe URLs and malformed block shapes fail validation explicitly.

#### Acceptance Criteria

- [x] Validation logic lives in a dedicated Editor.js module, not in the legacy HTML sanitizer router
- [x] Only the MVP block set is accepted: `paragraph`, `header`, `list`, `quote`, `delimiter`, `code`
- [x] Only allowed inline formatting survives in text-bearing fields: `a`, `strong`, `em`, `code`, `br`
- [x] `<b>` normalizes to `<strong>` and `<i>` normalizes to `<em>`
- [x] Full Editor.js save payloads may be accepted as input, but stored documents strip `time`, `version`, and block `id`
- [x] `quote.alignment` is rejected
- [x] `list.meta` is allowed only as `{}` for MVP
- [x] Invalid block shapes or unsafe URLs fail validation

---

### Issue: Task 7.3 - Django Admin Editor.js Authoring

**Labels**: `feature`, `admin`, `content`
**Branch**: `feat/geocontext-editorjs-admin`

**Description**:
Add Editor.js authoring to Django admin for GeoContext as progressive enhancement. The admin retains a JSON textarea fallback, while vendored JavaScript upgrades it into an Editor.js editing surface and mirrors content back before submit.

**Technical Details**:

- **Admin Integration**: Use Django admin `Media` to load vendored Editor.js assets.
- **Fallback**: Keep the raw textarea as the no-JS submission path.
- **Submission Path**: Mirror editor state into the textarea; no custom AJAX write flow.
- **Static Assets**: Vendor Editor.js assets under Django static files and include required upstream licenses.
- **Preview Behavior**: Extract plain text from block JSON for admin changelist preview, truncate long values, and show `(empty)` for empty documents.

#### Acceptance Criteria

- [x] GeoContext admin uses an Editor.js-powered editing surface when JS loads successfully
- [x] The non-JS fallback remains a plain textarea containing canonical JSON
- [x] Form submission relies on standard Django POST behavior; no custom AJAX write path is introduced
- [x] Editor.js assets are vendored into static files, not loaded from a CDN
- [x] Required upstream license files are included alongside vendored assets
- [x] Changelist preview shows extracted plain text, truncated, and displays `(empty)` for empty documents

---

### Issue: Task 7.4 - GeoContext Preflight and HTML-to-Blocks Backfill

**Labels**: `feature`, `migration`, `management-command`
**Branch**: `feat/geocontext-editorjs-preflight`

**Description**:
Add a read-only preflight command and Release 1 backfill logic for converting existing GeoContext rows from legacy text or HTML content into canonical Editor.js blocks. The process must be deterministic and must abort explicitly on rows that contain media-bearing markup.

**Technical Details**:

- **Command**: Add a preflight management command that reports exact sorted blocking IDs.
- **Conversion Logic**: Share detection and conversion logic between preflight and migration code.
- **Supported Mapping**:
  - blank simple content -> `{ "blocks": [] }`
  - simple text -> paragraph block
  - rich HTML headers/paragraphs/nested lists/blockquotes/code/inline code/`<br>` -> supported blocks
- **Abort Rule**: Rows containing `img`, `figure`, or `figcaption` abort with explicit IDs.
- **Behavior Guarantees**: Preflight must be read-only and idempotent.

#### Acceptance Criteria

- [x] A preflight command scans existing GeoContext rows and reports the exact sorted IDs that cannot be migrated
- [x] The preflight command is read-only and idempotent
- [x] Legacy simple content maps to paragraph blocks or `{ "blocks": [] }` when blank
- [x] Legacy rich content is sanitized first, then parsed into blocks
- [x] Supported HTML mappings are fixed and documented: headers, paragraphs, nested lists, blockquotes, code blocks, inline code, `<br>`
- [x] Rows containing `img`, `figure`, or `figcaption` abort migration with explicit IDs
- [x] The data migration uses the same detection logic as the preflight command

**Implementation**:

- Converter lives in `tosca_api/apps/core/legacy_html.py`. Exports
  `convert_legacy_html(html)` and `LegacyHtmlMediaError`. Uses stdlib
  `html.parser.HTMLParser` (no bs4/lxml dependency).
- Preflight command lives at
  `tosca_api/apps/geocontext/management/commands/geocontext_preflight.py`.
  Read-only: scans existing rows through
  `tosca_api.apps.core.editorjs.validate_and_normalize` and reports
  failures by UUID in sorted order for deterministic diffs across runs.
- `--legacy-input-json PATH` dry-runs the converter against a
  `{id: html}` JSON map so operators can preview which legacy rows
  would be blocked by media before any migration is attempted.
- Media detection runs before sanitization so `<img>`, `<figure>`, and
  `<figcaption>` abort with explicit IDs even though `sanitize_rich`
  would otherwise preserve them.
- Since Release 1 (Task 7.1) was destructive, the preflight + converter
  land as reusable migration tooling; future releases that ingest
  legacy HTML can reuse the same detection logic, satisfying the
  "migration and preflight agree" acceptance bullet.
- Tests: 25 in `tosca_api/apps/core/tests/test_legacy_html.py`
  (including nested list shape, `<pre><code>` → code block, `<br>`
  preservation, orphan-text folding, media rejection, idempotency),
  plus 8 in `tosca_api/apps/geocontext/tests/test_preflight.py` (clean
  dataset, invalid-row reporting, run-to-run stability, no-mutation
  guarantee, legacy input dry-run with sorted media IDs, missing/
  malformed legacy file).

---

### Issue: Task 7.5 - Release 2 Switch to `content_json`

**Labels**: `feature`, `migration`, `api`
**Branch**: `feat/geocontext-editorjs-switch`

**Description**:
Switch application reads and writes to the new JSON-backed GeoContext field while keeping the legacy columns in place for rollback safety during Release 2.

**Technical Details**:

- **Source of Truth**: Switch serializer, admin, and model codepaths to `content_json`.
- **Read Surfaces**: Geostories, events, and feedback nested serializers emit JSON-backed context content.
- **Write Surfaces**: GeoContext validation and admin writes use `content_json`.
- **Rollback Strategy**: Legacy columns remain present but inactive during this release.
- **Contract Cleanup**: Active read/write paths no longer depend on `content_type`.

#### Acceptance Criteria

- [x] All application reads use `content_json` as the source of truth *(`GeoContext.content` is the canonical JSON column post-7.1)*
- [x] All application writes populate and validate `content_json` *(`GeoContext.save()` routes through `validate_and_normalize`)*
- [~] Legacy columns remain present but are no longer the active read/write path *(N/A — 7.1 destructive reset dropped them)*
- [x] Events, geostories, and feedback nested serializers all emit the JSON-backed content contract
- [~] Rollback remains possible because legacy columns are still available during this release *(N/A — 7.1 destructive reset)*

**Implementation**:

- Task 7.1 collapsed the phased plan by dropping `content_type` and the
  legacy text column outright in migration
  `tosca_api/apps/geocontext/migrations/0002_editorjs_canonical_contract.py`.
  There is no parallel `content_json` field — `GeoContext.content` is
  already the canonical Editor.js JSON column. See decisions.md §[7.5].
- Nested serializers expose the JSON contract:
  `geostories.serializers.GeoContextSerializer`,
  `events.serializers.EventGeoContextSerializer` (with
  `effective_context` series-default resolution) and
  `feedback.serializers.FeedbackGeoContextSerializer`, each emitting
  `{id, content}` with `content` as a dict.
- Removed the dead `tosca_api.apps.core.sanitization.sanitize_content`
  helper and its lone unit test — the helper dispatched on the retired
  `content_type` string discriminator and had no remaining production
  callers.
- Final regression guard lives at
  `tosca_api/apps/geocontext/tests/test_json_contract.py` (14 cases)
  covering geostory/event/feedback detail contract, empty-context →
  `{"blocks": []}`, admin save round-trip, and absence of
  `content_type` on model + `sanitize_content` in code.

---

### Issue: Task 7.6 - Release 3 Drop Legacy GeoContext Fields

**Labels**: `feature`, `migration`, `cleanup`
**Branch**: `feat/geocontext-editorjs-cleanup`

**Description**:
Complete the GeoContext migration by dropping the legacy text-based fields, renaming `content_json` back to `content`, and finalizing the codebase on the canonical Editor.js contract.

**Technical Details**:

- **Schema Cleanup**: Drop legacy text `content` and `content_type`.
- **Rename**: Rename `content_json` back to `content`.
- **Code Cleanup**: Remove obsolete references in models, serializers, admin, migration helpers, and updated docs.
- **Regression Scope**: Verify model, admin, and nested serializer behavior after the final rename.

#### Acceptance Criteria

- [x] Legacy `content` text column and `content_type` are dropped *(Task 7.1 migration `0002_editorjs_canonical_contract`)*
- [~] `content_json` is renamed to `content` *(N/A — `content_json` was never introduced; see decision §[7.5b])*
- [x] No model, serializer, admin, migration helper, or doc for the new path refers to the old fields
- [x] The final schema and codebase align exactly with the canonical Editor.js contract
- [x] Regression coverage confirms behavior is unchanged after the rename

**Implementation**:

- Task 7.6 is almost entirely superseded by the destructive Task 7.1
  reset and Task 7.5 regression coverage. No schema changes ship in
  this task.
- Final regression locks in
  `tosca_api/apps/geocontext/tests/test_models.py`:
  - `test_geocontext_final_schema_snapshot` pins
    `{id, title, content, created_by, created_at, updated_at}` as the
    authoritative field set and explicitly rejects re-introduction of
    `content_type` or `content_json`.
  - `test_geocontext_crud_round_trip` exercises
    create / read / update / delete against the canonical JSON
    pipeline.
- Admin and nested-serializer regressions live in Task 7.3
  (`test_admin.py`) and Task 7.5 (`test_json_contract.py`) and continue
  to pass.
- Full-repo sweep confirms no residual references to `content_json` in
  production code, and remaining `content_type` matches elsewhere in
  the codebase (`featurelinks`, `events`, `geostories`) all refer to
  Django's unrelated `contenttypes.ContentType` generic-relations —
  not to the retired GeoContext discriminator.

---

### Issue: Phase 8 - GeoData Provider Integration & LayerRef Refactor

**Labels**: `epic`, `architecture`, `integration`

**Goal**: Replace the standalone `layerrefs` indirection with direct foreign keys to `geodata_providers.Layer` so GeoStory, Event, and GeoFeedback consume canonical layer metadata from one source of truth, and stop maintaining a parallel layer registry.

**Context**:
The `geodata_providers` app is the canonical layer registry: it owns `GeodataEngine → Workspace → Store → Layer → Style`, exposes CRUD and publish/unpublish actions, and reconciles state with GeoServer / Martin / pg_tileserv via `GeoServerSyncService` and `EngineClientFactory`. The legacy `layerrefs.LayerRef` model holds only a free-form `"workspace:layername"` string and is referenced by `GeoStory.layers`, `Event.layers`, and `GeoFeedback.layers` through per-app through models with `display_order`. There is no FK or resolver connecting the two: consumers cannot verify a layer exists, is public, is published, or read its geometry/SRID/published URL without parsing the string.

The system is not yet in production. Backward-compatible shims are not required. A destructive schema reset for the affected through tables is acceptable.

**Key Outcomes**:

- **Direct FK to `Layer`**: `GeoStoryLayer`, `EventLayer`, `FeedbackLayer` reference `geodata_providers.Layer` directly; `display_order` semantics preserved per parent.
- **`layerrefs` Removal**: The `layerrefs` app, `LayerRefSerializer`, and string-based layer references are deleted.
- **Public + Published Validation**: Through-model `clean()` rejects non-public or non-published layers across all three consuming apps.
- **Layer Metadata in Detail Responses**: A shared `LayerSummarySerializer` exposes `geometry_type`, `srid`, `published_url`, `is_public`, `publishing_state` on GeoStory / Event / GeoFeedback detail responses.
- **UUID-based Writes**: Nested layer writes accept `Layer.id` UUIDs instead of `layer_name` strings; this supersedes the layer sub-bullet of Task 1.3b.
- **Pre-delete Usage Warning**: Admin and API surface usage counts before deleting a `Layer` referenced by GeoStory / Event / GeoFeedback.
- **Supersede Legacy Sync Tasks**: Task 1.4a (LayerRef Sync Client) and Task 1.4b (LayerRef Sync Endpoint) are superseded by `GeoServerSyncService` already shipped in `geodata_providers`.

#### Task List

- [ ] Task 8.1: Replace `LayerRef` FK with `geodata_providers.Layer` FK
- [ ] Task 8.2: Remove `layerrefs` App
- [ ] Task 8.3: Public + Published Validation on Layer Assignment
- [ ] Task 8.4: `LayerSummarySerializer` and Detail Response Hydration
- [ ] Task 8.5: Replace String Layer Writes With UUID-Based Writes
- [ ] Task 8.6: Pre-delete Usage Warning for `Layer`
- [ ] Task 8.7: Supersede Task 1.4a and Task 1.4b

---

### Issue: Task 8.1 - Replace `LayerRef` FK with `geodata_providers.Layer` FK

**Labels**: `feature`, `models`, `migration`, `geodata`
**Branch**: `feat/layerref-direct-fk`

**Description**:
Swap the FK target on `GeoStoryLayer.layer`, `EventLayer.layer`, and `FeedbackLayer.layer` from `layerrefs.LayerRef` to `geodata_providers.Layer`. Re-declare the M2M `layers` field on `GeoStory`, `Event`, and `GeoFeedback` to point at `geodata_providers.Layer` through the existing through models. Preserve `display_order`, the auto-increment `save()` behavior, and the `(parent, layer)` uniqueness constraint on each through model.

**Technical Details**:

- **Models touched**: `GeoStoryLayer`, `EventLayer`, `FeedbackLayer`, `GeoStory.layers`, `Event.layers`, `GeoFeedback.layers`.
- **FK target**: `geodata_providers.Layer`, `on_delete=CASCADE`.
- **Related names**: `geostory_uses`, `event_uses`, `feedback_uses`.
- **Migration**: Destructive — pre-production, no row preservation required.
- **Preserved invariants**:
  - `display_order` ordering and `Meta.ordering`.
  - Auto-increment `display_order` on save when not specified.
  - `(parent, layer)` uniqueness via `unique_together` / `UniqueConstraint`.
  - The same `Layer` may be assigned to multiple parents at different `display_order` values.

#### Acceptance Criteria

- [ ] All three through models reference `geodata_providers.Layer` directly
- [ ] `display_order` ordering still works the same way per parent
- [ ] The same `Layer` can be assigned to multiple parents at different `display_order` values
- [ ] Migrations apply cleanly on a destructive reset
- [ ] No `LayerRef` import remains in `geostories`, `events`, or `feedback`

---

### Issue: Task 8.2 - Remove `layerrefs` App

**Labels**: `cleanup`, `migration`
**Branch**: `feat/layerrefs-app-removal`

**Description**:
With no consumer referencing `LayerRef`, delete the app entirely. Drop the `layerrefs_layerref` table, remove the directory, deregister from `INSTALLED_APPS`, and delete the `LayerRefSerializer` from `geostories`.

**Technical Details**:

- **Schema cleanup**: Drop `layerrefs_layerref` table via migration.
- **App removal**: Remove `tosca_api.apps.layerrefs` from `INSTALLED_APPS`; delete `tosca_api/apps/layerrefs/` directory.
- **Code cleanup**: Remove `LayerRefSerializer` from `geostories/serializers.py`; remove residual imports.
- **Doc updates**: Annotate the original Task 0.6 ("Create `layerrefs` App") with a "removed in Task 8.2" note.

#### Acceptance Criteria

- [ ] `INSTALLED_APPS` no longer lists `layerrefs`
- [ ] `tosca_api/apps/layerrefs/` no longer exists
- [ ] `LayerRefSerializer` no longer exists in `geostories`
- [ ] `grep -R LayerRef tosca_api/` returns zero matches in production code
- [ ] Migrations apply cleanly

---

### Issue: Task 8.3 - Public + Published Validation on Layer Assignment

**Labels**: `feature`, `validation`
**Branch**: `feat/layer-assignment-validation`

**Description**:
Add `clean()` validation on `GeoStoryLayer`, `EventLayer`, and `FeedbackLayer` so only layers with `is_public=True` and `publishing_state="PUBLISHED"` can be assigned. Errors must surface in admin forms and DRF serializers.

**Technical Details**:

- **Where**: Through-model `clean()`.
- **Rule**: Reject layers where `is_public=False` or `publishing_state != "PUBLISHED"`.
- **Surfaces**: Admin inline saves and DRF write serializers must surface the error as a 400 with a clear message.
- **Docs**: Update each through model's docstring with the rule.

#### Acceptance Criteria

- [ ] Assigning a non-public layer is rejected from API and admin
- [ ] Assigning a non-published layer is rejected from API and admin
- [ ] Assigning a public + published layer succeeds
- [ ] Existing valid through-rows continue to round-trip without revalidation errors

---

### Issue: Task 8.4 - `LayerSummarySerializer` and Detail Response Hydration

**Labels**: `feature`, `api`
**Branch**: `feat/layer-summary-serializer`

**Description**:
Add a slim, reusable `LayerSummarySerializer` exposing only the fields consumers need, then wire it into GeoStory, Event, and GeoFeedback detail responses so the frontend gets full layer metadata in one call.

**Technical Details**:

- **Fields**: `id`, `name`, `workspace` (nested `{id, name}`), `geometry_type`, `srid`, `published_url`, `is_public`, `publishing_state`.
- **Where**: Lives in `geodata_providers` (or a shared serializers module) and is imported by the three consuming apps.
- **Detail responses**: Replace nested layer output in GeoStory detail; add equivalent layer output to Event and GeoFeedback detail.
- **Per-link metadata**: Each linked-layer entry must include `display_order`.
- **Performance**: `select_related("workspace")` and `prefetch_related` on the through tables to avoid N+1 queries.

#### Acceptance Criteria

- [ ] GeoStory / Event / GeoFeedback detail responses include `LayerSummary` per linked layer with `display_order`
- [ ] No N+1 queries on detail endpoints (verified via `assertNumQueries`)
- [ ] OpenAPI schema reflects the new layer summary contract

---

### Issue: Task 8.5 - Replace String Layer Writes With UUID-Based Writes

**Labels**: `feature`, `api`, `breaking-change`
**Branch**: `feat/layer-uuid-writes`

**Description**:
Update the write contract for GeoStory, Event, and GeoFeedback so the `layers` payload accepts a list of `Layer.id` UUIDs (with optional `display_order`) instead of `layer_name` strings. Validation flows through the through-model `clean()` introduced in Task 8.3. This supersedes the layer sub-bullet of Task 1.3b.

**Technical Details**:

- **Contract shape**: `layers: [{id: <uuid>, display_order: <int>}]` or a plain UUID list with auto-incremented order.
- **Removed**: `layer_name` string parsing path.
- **Resolution**: UUIDs resolved to `Layer` instances and validated via through-model `clean()`.
- **Atomicity**: Nested writes wrapped in a transaction.
- **Schema**: Update OpenAPI for the three consumer apps.

#### Acceptance Criteria

- [ ] POST/PATCH bodies accept layers by UUID and persist them through the existing through models
- [ ] Unknown / non-public / non-published UUIDs return clear 400 errors
- [ ] String-based `layer_name` payloads are no longer accepted
- [ ] Nested writes remain atomic
- [ ] OpenAPI schema reflects the UUID contract

---

### Issue: Task 8.6 - Pre-delete Usage Warning for `Layer`

**Labels**: `feature`, `admin`, `api`, `safety`
**Branch**: `feat/layer-delete-usage-warning`

**Description**:
Before a `Layer` is deleted (admin or API), surface a count of GeoStory / Event / GeoFeedback rows that reference it via the new `geostory_uses`, `event_uses`, `feedback_uses` related managers. Admins must confirm the cascade. API deletes return the usage count in the response.

**Technical Details**:

- **Helper**: `Layer.usage_summary() -> dict[str, int]` using the new related managers.
- **Admin**: Override the `Layer` delete confirmation template (or `delete_view` / `delete_selected`) to show usage counts and a warning banner.
- **API**: `LayerViewSet.destroy` returns `409 Conflict` with the usage breakdown if usage is non-zero and `?confirm=true` is not supplied. With `?confirm=true`, cascade deletion proceeds.
- **Schema**: Document the confirmation behavior in OpenAPI.

#### Acceptance Criteria

- [ ] Admin delete confirmation shows usage counts for each consuming app
- [ ] API delete without `?confirm=true` against a referenced layer returns 409 with usage counts
- [ ] API delete with `?confirm=true` against a referenced layer cascades and removes through-rows
- [ ] API delete against an unused layer succeeds without requiring confirmation

---

### Issue: Task 8.7 - Supersede Task 1.4a and Task 1.4b

**Labels**: `docs`, `cleanup`
**Branch**: `chore/supersede-layerref-sync`

**Description**:
Mark the legacy LayerRef sync tasks as superseded by `geodata_providers`. The `geodata_providers` app already exposes `GeoServerSyncService`, `EngineClientFactory`, admin sync actions, and an `engines/{id}/sync` API path. There is no remaining work for a separate LayerRef sync client or endpoint.

**Technical Details**:

- **Docs only**: No code changes.
- **Touch**: Annotate Task 1.4a and Task 1.4b entries in `tasks.md` and `issues.md` with a "Superseded by Phase 8" pointer.
- **Phase 1 epic**: Update the `1.4a` / `1.4b` checkboxes in the Phase 1 epic to reflect supersession rather than open status.

#### Acceptance Criteria

- [ ] Task 1.4a and Task 1.4b are clearly marked superseded with a pointer to `GeoServerSyncService`
- [ ] Phase 1 epic reflects the change

---

### Issue: Phase 9 - GeoStory Hero Image + EditorJS Image Support

**Labels**: `epic`, `content`, `media`, `admin`, `api`

**Goal**: Add strict image support across GeoStory and GeoContext Editor.js with authenticated backend uploads, canonical validation, and non-destructive rollout documentation.

**Context**:
GeoStories need a dedicated hero image surface for cards/details, while GeoContext Editor.js content needs first-class image block support. This phase introduces strict image policy enforcement (mime, size, dimensions, alt text), adds upload infrastructure for EditorJS images, and documents contract changes without forcing unrelated task closure.

**Key Outcomes**:

- **Hero Image Contract**: `GeoStory` gains Django `ImageField`-backed hero image support with required alt text.
- **Strict Validation Policy**: Unified server-side validation across hero and EditorJS image ingestion.
- **EditorJS Image Block Support**: Canonical `image` block validation + normalization in `core/editorjs.py`.
- **Authenticated Upload Flow**: Backend endpoint provides EditorJS-compatible upload response.
- **Admin Integration**: Django admin EditorJS includes image tool with vendored assets.
- **Non-destructive Task Hygiene**: Existing open tasks are not auto-closed by this phase.

#### Task List

- [ ] Task 9.1: GeoStory Hero Image Storage Contract
- [ ] Task 9.2: Strict Image Validation Policy
- [ ] Task 9.3: Hero Image API and Admin Surface
- [ ] Task 9.4: Extend EditorJS Contract With `image` Block
- [ ] Task 9.5: EditorJS Image Upload Endpoint and Storage Contract
- [ ] Task 9.6: Admin EditorJS Image Tool Integration
- [ ] Task 9.7: Regression and Security Test Coverage
- [ ] Task 9.8: Schema, Docs, and Supersession Notes

#### Open Task Closure Notes

- [x] No currently-open legacy tasks are auto-closed by Phase 9 by default.
- [ ] If Task 9.8 updates stale image-related placeholders in older docs, mark those entries as **"Superseded by Phase 9"** (docs-only, non-destructive).
- [x] Existing open Phase 8 tasks remain untouched unless concretely completed by separate implementation.

---

### Issue: Task 9.1 - GeoStory Hero Image Storage Contract

**Labels**: `feature`, `media`, `model`, `migration`
**Branch**: `feat/geostory-hero-image-model`

**Description**:
Add first-class hero image support to `GeoStory` using Django `ImageField` and required alt text. Storage must remain backend-agnostic (local or S3 via Django storage settings).

**Technical Details**:

- **Model fields**: `hero_image` + `hero_image_alt`.
- **Path strategy**: namespaced upload path under `geostories/hero/...`.
- **Storage backend**: Django storage abstraction only; no S3-specific code required in this task.
- **Migration**: add fields to `GeoStory`.

#### Acceptance Criteria

- [ ] GeoStory persists hero image files via Django storage
- [ ] Hero image alt text is required when image is set
- [ ] Upload paths are deterministic and GeoStory-scoped
- [ ] Migration applies cleanly

---

### Issue: Task 9.2 - Strict Image Validation Policy

**Labels**: `feature`, `validation`, `security`, `media`
**Branch**: `feat/image-validation-policy`

**Description**:
Implement strict server-side image validation shared by hero image and EditorJS image upload flows.

**Technical Details**:

- **Allowed mime**: `image/jpeg`, `image/png`, `image/webp`
- **Max size**: `8 MB`
- **Dimensions**: min `800x450`, max `6000x6000`
- **Alt text**: required for hero image and EditorJS image blocks
- **Errors**: clear per-rule failure messages

#### Acceptance Criteria

- [ ] Invalid mime/size/dimensions/alt fail validation with clear errors
- [ ] Shared policy is reused by all image ingestion paths
- [ ] Boundary values are covered by tests

---

### Issue: Task 9.3 - Hero Image API and Admin Surface

**Labels**: `feature`, `api`, `admin`, `media`
**Branch**: `feat/geostory-hero-image-surfaces`

**Description**:
Expose hero image fields in GeoStory serializers and admin so editors can manage hero images and API consumers can render them.

**Technical Details**:

- **Write surface**: serializer accepts hero image + alt
- **Read surface**: list/detail include hero image URL + alt
- **Admin**: hero image upload/edit + preview
- **Validation**: policy from Task 9.2 enforced on write

#### Acceptance Criteria

- [ ] Create/update supports hero image and alt
- [ ] List/detail include hero image fields
- [ ] Invalid payloads return 400 with actionable errors
- [ ] Admin form supports edit/preview paths

---

### Issue: Task 9.4 - Extend EditorJS Contract With `image` Block

**Labels**: `feature`, `content`, `validation`, `security`
**Branch**: `feat/editorjs-image-block-validation`

**Description**:
Extend canonical EditorJS validation to support strict `image` blocks with deterministic normalization.

**Technical Details**:

- **Module**: `tosca_api/apps/core/editorjs.py`
- **Canonical image shape**: includes `file.url`, `alt`, `mime`, `width`, `height`, optional caption and tool booleans
- **Validation**: URL scheme checks + strict metadata checks + key stripping
- **Compatibility**: non-image blocks must remain unaffected

#### Acceptance Criteria

- [ ] Valid image blocks normalize deterministically
- [ ] Invalid URL/mime/dimensions/alt fail validation
- [ ] Unknown keys are stripped
- [ ] Existing block test suite remains green

---

### Issue: Task 9.5 - EditorJS Image Upload Endpoint and Storage Contract

**Labels**: `feature`, `api`, `media`, `security`
**Branch**: `feat/editorjs-image-upload-endpoint`

**Description**:
Add authenticated upload endpoint for EditorJS image tool with strict validation and EditorJS-compatible response payload.

**Technical Details**:

- **Auth**: endpoint restricted to authorized users
- **Storage path**: `geocontext/editorjs/...`
- **Validation**: strict policy from Task 9.2
- **Response**: EditorJS tool-compatible JSON contract with URL + metadata

#### Acceptance Criteria

- [ ] Authorized uploads succeed for valid files
- [ ] Unauthorized requests fail with 401/403
- [ ] Invalid uploads fail with structured 400 responses
- [ ] Response shape is integration-compatible with EditorJS image tool

---

### Issue: Task 9.6 - Admin EditorJS Image Tool Integration

**Labels**: `feature`, `admin`, `content`, `media`
**Branch**: `feat/editorjs-admin-image-tool`

**Description**:
Integrate image tool into Django admin EditorJS workflow using vendored static assets and backend uploader wiring.

**Technical Details**:

- **Assets**: vendor image tool scripts under static vendor path
- **Licensing**: include required upstream LICENSE files
- **Widget media**: include image tool assets in `EditorJsWidget.Media`
- **Init wiring**: configure tool + uploader endpoint in `init.js`
- **Fallback**: keep no-JS textarea path intact

#### Acceptance Criteria

- [ ] Admin loads image tool without CDN references
- [ ] Upload flow works through backend endpoint
- [ ] Saved output contains canonical image blocks
- [ ] Fallback mode remains usable

---

### Issue: Task 9.7 - Regression and Security Test Coverage

**Labels**: `test`, `security`, `media`, `api`, `admin`
**Branch**: `test/image-support-regressions`

**Description**:
Add broad regression coverage for new image features, including strict negative-path tests and non-image behavior safeguards.

**Technical Details**:

- **Model/API tests**: hero image positive + negative paths
- **Validator tests**: valid/invalid EditorJS image blocks
- **Upload tests**: auth + contract + malformed inputs
- **Admin tests**: asset inclusion + canonical persistence
- **Security tests**: unsupported schemes and malformed metadata

#### Acceptance Criteria

- [ ] All new image flows are covered by automated tests
- [ ] Security-relevant negative paths are explicitly tested
- [ ] Existing non-image functionality remains green

---

### Issue: Task 9.8 - Schema, Docs, and Supersession Notes

**Labels**: `docs`, `schema`, `api`, `cleanup`
**Branch**: `docs/phase9-image-contract`

**Description**:
Update OpenAPI/docs for hero image and EditorJS image contracts, and add scoped supersession notes for stale image placeholders in older docs without closing unrelated open tasks.

**Technical Details**:

- **OpenAPI**: include hero image fields and upload endpoint contract
- **Examples**: add request/response payloads for hero image and EditorJS image block
- **Migration notes**: local media storage rollout notes with Django storage backend compatibility
- **Task hygiene**: mark stale image placeholders as "Superseded by Phase 9" only when touched
- **Non-destructive**: do not auto-close unrelated open tasks (especially Phase 8)

#### Acceptance Criteria

- [ ] OpenAPI includes hero image and image-upload contract updates
- [ ] Docs include canonical EditorJS image block examples
- [ ] Supersession notes are scoped and non-destructive
- [ ] Unrelated open tasks remain unchanged
