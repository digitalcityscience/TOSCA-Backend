# Decision Log

This document tracks key architectural and implementation decisions made during the development of the TOSCA-Backend features.

## Phase 0: Infrastructure

### [0.1] GDAL Installation Strategy

- **Decision**: Install system libraries (`gdal-bin`, `libgdal-dev`) directly in the Docker image via `apt`.
- **Rationale**: GeoDjango requires binary bindings to GDAL/GEOS. Using the system package manager is the most reliable way to ensure compatibility with the underlying OS (Debian Slim).
- **Alternatives Considered**: Using a pre-built GeoDjango image (less control), compiling from source (too slow/complex).

### [0.2] GeoDjango Configuration Strategy

- **Decision**: Add `django.contrib.gis` to `INSTALLED_APPS` and use `django.contrib.gis.db.backends.postgis` as the database engine.
- **Rationale**: This is the standard Django approach for enabling GeoDjango features. No custom backends or third-party packages needed.
- **Favorites**: Using a separate GeoDjango project (rejected: unnecessary complexity).

### [0.X] Test Location Strategy

- **Decision**: Co-locate tests within each app directory (`apps/<app_name>/tests/`), using a package structure.
- **Rationale**:
  - **Modularity**: Allows apps to be portable.
  - **Organization**: Breaking tests into `test_models.py`, `test_views.py` is cleaner than one giant file.
  - **Django Default**: Standard convention expected by `manage.py test`.

### [0.3] PostGIS Infrastructure Tests Strategy

- **Decision**: Run PostGIS verification tests against the live dev database (`--ds=tosca_api.settings.base`), not a test database.
- **Rationale**:
  - **Speed**: Avoids creating/tearing down a test database just to verify a SQL query.
  - **Infrastructure Focus**: These tests are verifying the _system_ (PostGIS extension is installed), not application logic.
  - **Permission Issues**: The API user (`tosca_api`) lacks CREATEDB privileges, which is correct for security. Superuser tests would require a separate test database setup.
- **Pattern**: Use a custom fixture (`db_access_without_rollback`) that unblocks the database connection without triggering test database creation.
- **Run Command**: `pytest tosca_api/apps/core/tests/test_postgis.py -v --ds=tosca_api.settings.base`

### [0.4] Campaign Model Design

- **Decision**: Use UUID as primary key, TextChoices for status/visibility enums, and inherit from `TimeStampedModel`.
- **Rationale**:
  - **UUID PK**: Provides globally unique identifiers, avoids sequential ID enumeration (security), and is required for eventual multi-tenant sync.
  - **TextChoices**: Human-readable values stored in DB (e.g., `"draft"`), easier to debug and query vs integer enums.
  - **TimeStampedModel**: Reuses existing abstract base class from `core.models`, ensuring consistent timestamp fields across all models.
  - **PROTECT on delete**: Prevents accidental deletion of users who own campaigns.

### [0.5] GeoContext Model Design

- **Decision**: Create a separate `geocontext` app with a standalone `GeoContext` model, linked 1:1 to parent features.
- **Rationale**:
  - **Separation of Concerns**: Content management is distinct from feature metadata (status, visibility, etc.).
  - **Reusability**: The same content structure can be used by GeoStory, CalendarEvent, and GeoFeedback.
  - **Flexibility**: Rich vs simple content types allow for progressive enhancement without schema changes.
- **Linkage Pattern**: Parent features (GeoStory, etc.) will use `OneToOneField(GeoContext, null=True)`.
- **Superseded Note**: This original `content` + `content_type` design is preserved as historical context, but is superseded for future implementation by Phase 7 Editor.js canonical JSON storage.

### [SECURITY] Zero Trust Content Sanitization

- **Policy**: All user-generated text content MUST be sanitized at the model level before persistence.
- **Rationale**: Prevent XSS/HTML injection even if frontend or serializer validation is bypassed.
- **Implementation**:
  - **Simple Fields** (Title, Summary, etc.): Apply `sanitize_simple` (strip ALL tags).
  - **Rich Fields** (GeoContext content type=rich): Apply `sanitize_rich` (allowlist only).
  - **Location**: Override `save()` method on all content models (`Campaign`, `GeoContext`, `GeoStory`, etc.).
- **Consistency**: Frontend should mirrored this policy, but Backend is the source of truth enforcement.
- **Phase 7 Addendum**: For GeoContext Editor.js migration, sanitization applies to inline HTML fragments within Editor.js block text rather than to raw stored HTML documents.

---

## Phase 2: CalendarEvent Feature

### [2.1] CalendarEvent API Dual-View Pattern

- **Decision**: Implement two distinct response formats for the events API:
  - **Calendar View** (default): Paginated JSON, includes all events (with or without location).
  - **Map View** (spatial filter): GeoJSON FeatureCollection, only events WITH location.
- **Rationale**:
  - Calendar UI needs all events regardless of spatial data.
  - Map UI needs GeoJSON format and only events that can be rendered on a map.
  - Avoids null geometry issues in GeoJSON output.
- **Trigger**: Map view is activated by `bbox` query param or `POST /within/` endpoint.

### [2.2] Spatial Filtering Strategy

- **Decision**: Use POST for polygon filters, GET for bbox filters.
- **Rationale**:
  - **BBox (GET)**: Simple, fits in URL query string: `?bbox=min_lon,min_lat,max_lon,max_lat`
  - **Polygon (POST)**: Complex GeoJSON geometries don't fit well in URLs; POST body is cleaner.
  - **Mutual Exclusivity**: `bbox` and `geometry` cannot be combined (no meaningful use case).
- **Endpoints**:
  - `GET /api/v1/events/?bbox=...` - Bounding box filter
  - `POST /api/v1/events/within/` - Polygon/MultiPolygon filter

### [2.3] Default Temporal Filter: Future Events Only

- **Decision**: By default, the events API returns only future events (`start_datetime >= now`).
- **Rationale**:
  - Most frontend use cases display upcoming events.
  - Past events are historical and require explicit opt-in via `include_past=true`.
  - Reduces response payload size for typical queries.
- **Override**: Use `?include_past=true` to include past events.

### [2.4] GeoJSON Serialization via djangorestframework-gis

- **Decision**: Use `djangorestframework-gis` for GeoJSON output instead of custom serializers.
- **Rationale**:
  - Standard library, well-maintained, integrates with DRF.
  - `GeoFeatureModelSerializer` produces valid GeoJSON FeatureCollection.
  - Handles SRID transformation and geometry serialization.
- **Dependency**: Added `djangorestframework-gis>=1.0` to project.

---

## Phase 2B: Event Model V2

### [2B.1] Event Core as the Source of Truth

- **Decision**: Keep location, provider, schedule, and core event content directly on the event model rather than splitting them into separate `Location` or `Provider` models.
- **Rationale**:
  - Event authoring stays simple.
  - The main use case is still event-centric, not location-centric or provider-centric.
  - Avoids premature normalization and extra joins for the common create/edit/read path.
- **Trade-off**: Reusable provider or venue entities can still be introduced later if product requirements justify them.

### [2B.2] GeoContext Reuse for Event Series

- **Decision**: Change event-to-`GeoContext` linkage from `OneToOneField` to `ForeignKey`.
- **Rationale**:
  - Multiple generated occurrences in the same series may initially share one context block.
  - This avoids duplicating rich content during series generation.
  - Users can still override context per event later if needed.
- **Trade-off**: The event-context relationship becomes less strictly isolated than the current v1 event design, but the reuse benefit is higher for recurring workflows.

### [2B.3] Dynamic Taxonomy Dimensions

- **Decision**: Use `TaxonomyDimension`, `TaxonomyTerm`, and `EventTerm` instead of a hardcoded event taxonomy enum structure.
- **Rationale**:
  - The project is open source and should support different owner-defined classification schemes.
  - New dimensions should not require schema changes.
  - Hierarchical terms map naturally to workbook concepts such as `topic` / `topicFocus`.
- **Trade-off**: Some validation moves from simple enum constraints to richer application logic and admin governance.

### [2B.4] EventType Registry with Profile Binding

- **Decision**: Introduce `EventType` as a registry with `profile_mode` and `profile_key`, and enforce profile compatibility through application validation.
- **Rationale**:
  - Some event types are fully represented by the event core.
  - Other event types need extension profiles such as `PublicHealthEventProfile`.
  - The registry makes event-type behavior explicit and data-driven.
- **Implementation Rule**:
  - `profile_mode=core` means no profile row is expected.
  - `profile_mode=extension` means only the matching profile table is valid.

### [2B.5] Shared Filters with Dual Output APIs

- **Decision**: Keep one filtering contract for event retrieval, but provide separate map and list endpoints with different response shapes.
- **Rationale**:
  - Frontend logic stays simpler when filters are shared.
  - Map consumers need GeoJSON.
  - List and card consumers need paginated JSON.
- **Map Output Rule**:
  - Online events are included in GeoJSON with `geometry: null`.
  - Spatial filters constrain spatial events, but online events remain in the result set for now.

### [2B.6] Lightweight EventSeries Model

- **Decision**: Model recurring and manual-batch relationships through a lightweight `EventSeries` model plus `EventSeriesDate` for explicit batch dates.
- **Rationale**:
  - Campaign is too broad to represent recurrence.
  - Event rows must remain the canonical occurrence records.
  - The system still needs persistent relation and recurrence-definition metadata.
- **Scope**:
  - `EventSeries` stores grouping and generation metadata.
  - `Event` stores actual occurrence data.
  - `EventSeriesDate` stores explicit dates for manual batches.

### [2B.7] Exception Tracking for Individual Occurrence Edits

- **Decision**: Track manually changed series occurrences via `is_exception` and `original_start_datetime`.
- **Rationale**:
  - Users need to adjust single occurrences without corrupting the whole series.
  - Future series edits must not silently overwrite manually changed events.
- **Default Behavior**:
  - Future bulk updates should target only future non-exception events unless the user explicitly resets an exception.

### [2B.8] Synthesized Admin Form for Event Series

- **Decision**: Extend `EventSeriesAdminForm` with non-model (synthetic) template and profile fields rather than adding an `event_template` JSON column to the `EventSeries` model.
- **Rationale**:
  - Keeps the database schema clean and normalized.
  - The series model serves strictly as recurrence metadata, while occurrences remain the canonical source of truth for event details.
  - Allows the UI to automatically load the base template from the first non-exception occurrence when editing.
- **Trade-off**: Requires more manual field management in the admin form, but avoids duplicating state in the database.

### [2B.9] Shared Orchestration Layer

- **Decision**: Extract series occurrence generation and synchronization logic into a context-independent service layer (`services.py`) shared by both DRF serializers and Django admin.
- **Rationale**:
  - Ensures exactly the same behavior and side-effects (profile row creation, taxonomy term assignment) regardless of whether a series is authored via API or Admin UI.
  - Keeps forms and serializers strictly focused on input validation.
  - Facilitates comprehensive unit testing of the complex recurrence logic.

### [2B.10] Grouped Taxonomy Assignments for Event Writes

- **Decision**: Replace raw `taxonomy_term_ids` write payloads with grouped `taxonomy_assignments` keyed by dimension for both event and event-series authoring.
- **Rationale**:
  - Taxonomy is conceptually an optional set of event attributes, not a direct join-table editing surface.
  - Grouping terms under dimensions makes validation clearer for single-select vs multi-select dimensions.
  - The feature is not yet published, so the contract can be corrected now without carrying compatibility debt.
- **Rules**:
  - Taxonomy remains optional for all event statuses.
  - Only active dimensions and active leaf terms may be newly assigned.
  - `EventTerm` remains the only persistence model for event taxonomy.
  - Series authoring uses the same grouped assignment contract as direct event writes.

### [2B.11] Grouped Taxonomy Hydration for Reads and Admin

- **Decision**: Expose grouped taxonomy assignments on event detail and event-series retrieve responses, and render taxonomy in Django admin as one dynamic field per dimension.
- **Rationale**:
  - The authoring surface should match the conceptual model of taxonomy as optional event attributes.
  - Event-series remains lightweight, so series taxonomy hydration should derive from the base non-exception occurrence rather than adding duplicate series-level taxonomy storage.
  - Admin edit flows need to preserve visibility of already-assigned inactive terms without reopening the write contract for newly selecting inactive terms.
- **Rules**:
  - Event detail and series retrieve use the same grouped taxonomy shape as write payloads, with extra dimension/term metadata for hydration.
  - EventSeries retrieve fails clearly when no usable base occurrence/template exists.
  - Event and event-series admin forms generate taxonomy fields dynamically from active dimensions plus any inactive assigned dimensions already present on the source event.

---

## Phase 3: GeoFeedback Feature

### [3.1] Dynamic Form Engine Strategy

- **Decision**: Use `django-basic-form-builder` to manage customizable feedback forms instead of custom `FeedbackType` and `FeedbackOption` models.
- **Rationale**:
  - Offloads the complex business logic of generating schema JSON and managing individual form fields to a dedicated, tested package.
  - Enables non-technical admins to build complex forms (dropdowns, conditional logic, checkboxes) using a polished inline admin interface without requiring backend changes per campaign.
  - Native integration with Django 5.x and DRF schema output.
- **Integration Profile**:
  - `GeoFeedback` links to `formbuilder.CustomForm` via `custom_form_id`.
  - The API exposes the Form Builder JSON schema via nested endpoints.
  - `FeedbackSubmission` stores dynamic answers in a `JSONB` column (`form_data`), while preserving strictly-typed relationships for Native Ratings (`rating`) and Geospatial input (`geometry`).

---

## Phase 7: GeoContext Editor.js Migration

### [7.1] Canonical GeoContext Storage Contract

- **Decision**: Store GeoContext as canonical Editor.js JSON only.
- **Rationale**:
  - Structured content is a better long-term contract than HTML strings.
  - A single JSON source of truth avoids drift between raw text, HTML, and derived representations.
  - The feature is not yet published, so the contract can be corrected without compatibility debt.
- **Rules**:
  - `GeoContext.content` is canonical Editor.js JSON.
  - `content_type` is removed from the future contract.
  - Empty content is stored as `{ "blocks": [] }`.
  - Stored data is intentionally narrower than the full Editor.js save envelope.

### [7.2] Admin Authoring Surface

- **Decision**: Introduce Editor.js in Django admin only, using progressive enhancement over a JSON textarea.
- **Rationale**:
  - The repository has no frontend bundler and does not need one for this authoring surface.
  - Django admin is the immediate editing environment that benefits from WYSIWYG authoring.
  - A textarea fallback preserves operability if JavaScript fails or is disabled.
- **Rules**:
  - No CDN dependency for Editor.js assets.
  - No custom AJAX submission path; standard Django form POST remains the write path.
  - The raw textarea remains the no-JS fallback.

### [7.2b] Validation Errors Raise Django ValidationError

- **Decision** (implementation, Task 7.2): The Editor.js validator raises `django.core.exceptions.ValidationError` directly from `GeoContext.save()` rather than introducing a DRF-specific exception layer.
- **Rationale**:
  - Validation must run for all write paths (admin, shell, fixtures, future serializers) — coupling it to DRF would bypass admin.
  - Django's admin and model-form stack already translate `ValidationError` into user-visible form errors; DRF translates model `ValidationError` into HTTP 400 at the view layer.
- **Rules**:
  - The validator is a pure function (`validate_and_normalize`) with no framework dependency beyond `django.core.exceptions.ValidationError`.
  - Input envelope fields (`time`, `version`, block `id`, `tunes`) are silently stripped; only schema violations raise.

### [7.3] Deterministic Normalization and Validation

- **Decision**: Centralize Editor.js validation and normalization in a dedicated module.
- **Rationale**:
  - JSON document validation is a separate concern from legacy HTML sanitization.
  - Deterministic normalization prevents round-trip drift and makes migration output predictable.
  - Strict schema rules reduce ambiguity in future authoring and migration behavior.
- **Rules**:
  - Strip `time`, `version`, and block `id` from stored content.
  - Normalize `<b>` to `<strong>` and `<i>` to `<em>`.
  - Reject `quote.alignment`.
  - Constrain `list.meta` to `{}` for MVP.
  - Allow only the defined MVP block and inline toolset.

### [7.3b] Vendored Editor.js Versions

- **Decision** (implementation, Task 7.3): Vendor `@editorjs/editorjs@2.30.7`, `@editorjs/header@2.8.8`, `@editorjs/list@1.10.0`, `@editorjs/quote@2.7.3`, `@editorjs/delimiter@1.4.2`, `@editorjs/code@2.9.3` under `tosca_api/apps/geocontext/static/geocontext/editorjs/vendor/` with each upstream `LICENSE` file preserved alongside.
- **Rationale**:
  - `@editorjs/list@2.x` ships only an ESM build (`list.mjs`); the 1.10.0 UMD drop is the newest version that loads cleanly via Django's static-file `<script>` tags without a bundler.
  - Pinned exact versions keep the admin build reproducible and auditable; LICENSE files satisfy MIT attribution requirements for redistribution.
- **Rules**:
  - Upgrades require re-vendoring both the UMD bundle and the upstream `LICENSE`.
  - No CDN fallback is permitted; a CDN-URL presence test runs in `test_admin.py`.

### [7.4] Legacy HTML Migration Rules

- **Decision**: Sanitize legacy HTML first, then parse it into canonical Editor.js blocks, aborting on media markup.
- **Rationale**:
  - Sanitizing first keeps legacy conversion inside the existing security posture.
  - Parsing into a fixed block schema preserves supported narrative content while avoiding silent data loss.
  - Explicit abort behavior is safer than partially migrating unsupported media-bearing rows.
- **Rules**:
  - Preserve paragraphs, headers, nested lists, blockquotes, code blocks, inline code, and `<br>`.
  - Fold top-level text nodes into paragraph blocks.
  - Abort on `img`, `figure`, and `figcaption`.
  - Preflight and real migration share the same detection logic.

### [7.4b] Stdlib HTMLParser for Legacy Conversion

- **Decision** (implementation, Task 7.4): Use Python's stdlib
  `html.parser.HTMLParser` (not `beautifulsoup4` or `lxml`) as the parsing
  engine inside `tosca_api/apps/core/legacy_html.py`.
- **Rationale**:
  - bs4/lxml are not currently project dependencies; adding them for a
    one-shot legacy converter is disproportionate.
  - `HTMLParser` is event-driven, which matches the deterministic
    block-builder shape (open/close/data hooks → push/pop/append) and
    keeps the converter dependency-free.
  - Sanitization already runs through `nh3` before parsing, so the
    parser only ever sees an allowlisted tag set.
- **Consequences**:
  - Media detection must run **before** sanitization so that rows with
    `<img>`/`<figure>`/`<figcaption>` still abort even though
    `sanitize_rich` would happily preserve them.
  - Inline tag handling is suppressed inside `<pre>` so that
    `<pre><code>…</code></pre>` collapses to a single canonical `code`
    block with only the inner text.

### [7.4c] Preflight Is Read-Only, Output Is Sorted UUID

- **Decision** (implementation, Task 7.4): The `geocontext_preflight`
  management command never writes to the database and emits failing
  GeoContext rows sorted by UUID (optionally alongside dry-run failures
  from a `--legacy-input-json` file).
- **Rationale**:
  - Sorted output means two consecutive runs on unchanged data produce
    byte-equal output, which is diffable across environments and in CI.
  - Read-only behavior lets operators run the preflight against a
    production snapshot without any migration-time coupling.
  - Dry-running a separate legacy-HTML JSON file decouples "check which
    legacy strings would convert" from any future real backfill
    migration that might mutate rows.
- **Consequences**:
  - Because Release 1 (Task 7.1) destructively reset GeoContext, the
    command currently scans a (typically empty) canonical table; the
    real value is that future releases or data-imports can reuse the
    same validation + converter entry points.

### [7.5] Multi-Release Migration Strategy

- **Decision** (planning): Execute the GeoContext migration over three releases.
- **Rationale**:
  - A staged rollout preserves rollback safety while switching application behavior.
  - Separating backfill, read/write switch, and cleanup reduces the risk of destructive migration mistakes.
  - Final schema cleanup should occur only after the JSON-backed contract is already active.
- **Rules (planned)**:
  - Release 1: add `content_json` and backfill it.
  - Release 2: switch reads and writes to `content_json`, while keeping old columns.
  - Release 3: drop legacy fields and rename `content_json` to `content`.

### [7.5b] Phased Migration Collapsed by Destructive Task 7.1

- **Decision** (implementation, Tasks 7.1 / 7.5 / 7.6): Collapse the
  three-release phased migration into a single destructive Release 1
  that drops `content_type` and the legacy text `content` column and
  promotes the canonical Editor.js JSON directly onto
  `GeoContext.content`.
- **Rationale**:
  - Pre-Release 1 there were zero production rows to preserve, so the
    rollback-safety case that motivated the three-release strategy
    evaporated.
  - Carrying a parallel `content_json` field plus a read/write switch
    through two more releases would add migration + code paths that
    only exist to protect data that does not exist.
  - Dropping the legacy columns up-front keeps the single-source-of-
    truth narrative clean (`GeoContext.content` is always Editor.js
    JSON) and avoids a temporary nullable-JSON shape that downstream
    serializers would have to special-case.
- **Consequences**:
  - Tasks 7.5 ("switch to `content_json`") and 7.6 ("drop legacy
    fields / rename back to `content`") have no code changes to make;
    both are marked folded into 7.1 in tasks.md + issues.md.
  - Task 7.5 still owns the regression coverage proving every nested
    serializer (geostories / events / feedback) emits the canonical
    `{"blocks": [...]}` contract and that no code path depends on the
    retired `content_type` discriminator — this lives in
    `tosca_api/apps/geocontext/tests/test_json_contract.py`.
  - Task 7.5 also owns removal of the now-dead
    `tosca_api.apps.core.sanitization.sanitize_content` helper, which
    dispatched on the retired `content_type` discriminator string.
  - Phased column-level rollback is no longer available for any future
    GeoContext storage change. Future migrations that would benefit
    from a parallel-field rollout will need to reintroduce the pattern
    deliberately rather than relying on the historical Release 1 → 2 → 3
    shape documented in §[7.5].

### [7.5c] GeoContext Title for Related-Object Dropdowns

- **Decision** (UX, post-Task 7.5): Add an optional
  `GeoContext.title = CharField(max_length=200, blank=True, default="")`
  and make `__str__` dropdown-friendly so admin pickers for GeoStory,
  Event, and GeoFeedback expose meaningful labels instead of
  `"GeoContext: 4 block(s)"`.
- **Rationale**:
  - Block-count-only labels made it impossible to pick the right
    context from the Django admin related-object dropdown when
    authoring GeoStory / Event / GeoFeedback rows.
  - An explicit title is clearer than any derived excerpt and lets
    editors name contexts intentionally (e.g. "Smart City Logistics
    Overview") regardless of the current block contents.
  - Keeping the field optional (`blank=True`, default `""`) avoids a
    disruptive data migration and preserves backwards compatibility
    with existing fixtures and callers.
- **Fallback chain for `__str__`**:
  1. explicit `title` → `"Title (N block(s))"`
  2. first `header` / `paragraph` / `quote` excerpt (up to 60 chars)
  3. `"GeoContext {short-uuid} (N block(s))"`
- **Consequences**:
  - `GeoContext` nested serializers for GeoStory, Event, and GeoFeedback
    now expose `{id, title, content}`. Frontends can show the title
    next to the JSON blocks.
  - Admin list view shows `title_or_excerpt` as the primary column;
    `search_fields` covers `title` in addition to `id`.
  - Migration `geocontext.0003_geocontext_title` ships the column with
    default `""`, so previously stored rows (there are none post-7.1
    reset) would remain readable without operator action.

---

## Phase 8: GeoData Provider Integration & LayerRef Refactor

### [8.1] Direct FK Replaces the LayerRef Indirection

- **Decision**: Replace the `layerrefs.LayerRef` indirection with a direct `ForeignKey` from `GeoStoryLayer`, `EventLayer`, and `FeedbackLayer` to `geodata_providers.Layer`. Delete the `layerrefs` app.
- **Rationale**:
  - `geodata_providers.Layer` is already the canonical layer registry and reconciles with GeoServer / Martin / pg_tileserv via `GeoServerSyncService`.
  - `LayerRef` only stored a free-form `"workspace:layername"` string with no link to canonical metadata, so consumers could not verify existence, public flag, publishing state, geometry type, SRID, or published URL without parsing the string.
  - The indirection earned its keep only in two cases — referencing layers that do not yet exist in `geodata_providers`, or keeping consumer apps decoupled for plugin-style extraction. Neither applies here.
- **Trade-off**: Consuming apps (`geostories`, `events`, `feedback`) now import from `geodata_providers`. That coupling reflects reality — these features genuinely depend on canonical layer metadata.
- **Pre-production note**: The system is not yet in production, so this lands as a destructive schema reset for the affected through tables; no row preservation is required.

### [8.2] Cascade Deletion With Pre-delete Usage Warning

- **Decision**: Use `on_delete=CASCADE` on the FK from each through model to `Layer`, but warn admins and API callers about usage counts before the delete proceeds.
- **Rationale**:
  - CASCADE keeps the data model simple: deleting a `Layer` simply removes its through-rows from any GeoStory / Event / GeoFeedback that referenced it.
  - The alternative (`SET_NULL`) preserves a tombstone row but creates orphan-handling complexity in serializers and admin UIs.
  - The real risk is silent removal of layer assignments without operator awareness. Surfacing a usage summary at delete time addresses that risk without requiring tombstones.
- **Rules**:
  - `Layer.usage_summary()` returns counts via `geostory_uses`, `event_uses`, `feedback_uses` related managers.
  - Admin delete confirmation displays the usage counts and requires explicit confirmation.
  - `LayerViewSet.destroy` returns `409 Conflict` with the usage breakdown unless the caller passes `?confirm=true`.

### [8.3] Public + Published Required for Layer Assignment

- **Decision**: Through-model `clean()` rejects assigning any layer that is not both `is_public=True` and `publishing_state="PUBLISHED"`.
- **Rationale**:
  - GeoStory, Event, and GeoFeedback are user-facing surfaces. Linking a draft, failed, or non-public layer would either render nothing or expose internal data.
  - Keeping the rule at the through-model layer means it applies equally to admin saves, DRF writes, and any future fixture-based or shell-based code path.
- **Rules**:
  - The check runs in each through model's `clean()` so it surfaces in admin forms and DRF serializers as a `ValidationError`.
  - `feedback` follows the same rule even though feedback layers are pure visualization backdrops.

### [8.4] `LayerSummarySerializer` as the Shared Read Contract

- **Decision**: Add a slim, reusable `LayerSummarySerializer` exposing `id`, `name`, `workspace`, `geometry_type`, `srid`, `published_url`, `is_public`, `publishing_state` and use it on every consumer detail response.
- **Rationale**:
  - Consumers need enough metadata to render the layer on a map (`published_url`, `geometry_type`, `srid`) and to display human-friendly labels (`name`, `workspace`) without a second API round-trip.
  - A single shared serializer keeps the layer payload shape consistent across GeoStory, Event, and GeoFeedback.
  - Slim by design: heavier metadata (style assignments, store details, internal publishing errors) is not exposed to public consumers.
- **Rules**:
  - The serializer lives in `geodata_providers` and is imported by consumer apps.
  - Each linked-layer entry in a detail response carries `display_order` from the through model.
  - Detail-endpoint querysets use `select_related("workspace")` and `prefetch_related` on the through tables to avoid N+1 queries.

### [8.5] UUID-based Nested Layer Writes

- **Decision**: Nested layer writes on GeoStory / Event / GeoFeedback accept a list of `Layer.id` UUIDs (optionally with `display_order`) instead of the legacy `layer_name` strings.
- **Rationale**:
  - UUIDs let the serializer resolve a structured `Layer` object directly and run through-model validation without parsing strings.
  - The legacy `layer_name` contract had no validation that the layer existed, was public, or was published.
  - The system is not yet in production, so the contract can be corrected now without a compatibility shim.
- **Consequence**: This supersedes the layer sub-bullet of Task 1.3b. Nested writes for `context` (the other half of 1.3b) are unaffected.

### [8.6] `GeoServerSyncService` Supersedes Standalone LayerRef Sync

- **Decision**: Tasks 1.4a (LayerRef Sync Client) and 1.4b (LayerRef Sync Endpoint) are superseded by the `GeoServerSyncService` already shipped in `geodata_providers`.
- **Rationale**:
  - `geodata_providers` already implements multi-engine sync (GeoServer / Martin / pg_tileserv) via `EngineClientFactory` and reconciles workspaces, stores, layers, and styles in `GeoServerSyncService`.
  - Maintaining a separate sync surface in `layerrefs` would duplicate the responsibility and diverge over time.
- **Rule**: All future layer-sync work lives in `geodata_providers`. The legacy task entries remain in the docs as "Superseded by Phase 8" pointers for historical traceability.

---

## Phase 9: GeoStory & GeoContext Image Support

### [9.1] Storage Strategy

- **Decision**: For v1, store GeoStory hero images and EditorJS uploaded images through Django's storage abstraction using `ImageField`/file storage paths. Keep DB fields nullable and enforce alt requirements in `clean()` / serializer validation (not DB constraints).
- **Rationale**:
  - Fastest delivery with minimal schema complexity.
  - Keeps validation intent explicit and field-keyed instead of over-constraining DB schema.
  - Django storage remains backend-agnostic (local now; S3-compatible later via settings).
- **Rules**:
  - Hero images store under a GeoStory namespace (e.g. `geostories/<uuid>/hero/...`).
  - EditorJS uploads store under a GeoContext namespace (e.g. `geocontext/editorjs/<uuid>/...`).
  - `hero_image_alt` is required only when `hero_image` is present.
  - No S3-specific flow (presigned direct upload, bucket-coupled contracts) in Phase 9.

### [9.2] Strict Image Policy

- **Decision**: Enforce a two-tier server-side validation policy with shared MIME/size/decode checks and tier-specific dimension bounds.
- **Rationale**:
  - Hero images and inline images have different UX needs.
  - Header-based validation prevents client header spoofing.
  - Keeping validation read-only avoids hidden content mutation at ingest.
- **Rules**:
  - Allowed MIME types (both tiers): `image/jpeg`, `image/png`, `image/webp`.
  - Max file size (both tiers): `8 MB`.
  - Hero dimensions: min `800x450`, max `6000x6000`.
  - Inline dimensions: min `200x200`, max `6000x6000`.
  - MIME is determined from image header bytes (Pillow), not request `Content-Type`.
  - Validation rejects/accepts only; it does not rewrite uploaded bytes.

### [9.2b] Original-Byte Preservation and Metadata Posture

- **Decision**: Preserve original upload bytes exactly at ingest. Do not strip EXIF on upload; metadata cleanup occurs when derivatives are re-encoded.
- **Rationale**:
  - Preserves forensic fidelity and avoids implicit data mutation.
  - Keeps ingest path simple and deterministic.
  - Metadata-clean public delivery can still be achieved via derivative-only consumption.
- **Rules**:
  - Stored originals are byte-for-byte identical to upload/downloaded body.
  - EXIF orientation and other metadata may remain on originals.
  - Derivative outputs are metadata-clean by re-encode (see [9.4]).
  - Hardening raw-original public access is explicitly deferred.

### [9.5] EditorJS Image Canonical Contract

- **Decision**: Extend canonical EditorJS storage to accept a strict `image` block shape with deterministic normalization.
- **Rationale**:
  - Image support must preserve the same deterministic storage guarantees introduced in Phase 7.
  - A narrow canonical shape keeps client behavior predictable and prevents arbitrary JSON growth.
  - Block-level validation keeps content integrity independent of authoring client quirks.
- **Rules**:
  - `image` block is added to allowed block types in `core/editorjs.py`.
  - Canonical stored data includes required URL + metadata fields, sanitized caption, and tool flags.
  - `data.alt` is a project extension, not native to `@editorjs/image`.
  - Missing `data.alt` falls back to the plain-text caption so the upstream caption input remains the authoring surface.
  - `data.file.{mime,width,height}` are server-derived on every save.
  - Inline image block count is not capped at the GeoContext level.
  - Unknown keys are stripped during normalization.
  - Off-origin or unsafe URLs are rejected.

### [9.4] Derivative Strategy and Orientation Handling

- **Decision**: Generate optimized derivatives on demand (`webp`, `avif` when available), cache them, and keep originals immutable.
- **Rationale**:
  - Avoids up-front processing cost while improving delivery performance.
  - Solves camera-orientation rendering by baking EXIF orientation into pixels.
  - Produces metadata-clean public assets without mutating originals.
- **Rules**:
  - Apply `ImageOps.exif_transpose()` before derivative encoding.
  - Re-encoded derivatives must not include EXIF.
  - Original bytes must remain unchanged.
  - AVIF unavailability returns a clear non-success response path (documented by API behavior).

### [9.6] Upload-by-URL Rehost and Safety Rules

- **Decision**: Support both `byFile` and `byUrl` upload flows required by `@editorjs/image`, with rehost-and-validate behavior for URL uploads and deferred throttling tuning.
- **Rationale**:
  - Matches official EditorJS image-tool integration shape.
  - Rehosting centralizes validation and origin control.
  - Defers premature ops tuning while keeping a throttle hook in place.
- **Rules**:
  - `byUrl` accepts HTTP(S) only, with strict size/timeout/redirect limits.
  - Same-origin URLs are rejected to avoid self-rehost loops.
  - Upload responses follow `{"success": 1|0, ...}` contract expected by `@editorjs/image`.
  - Throttle class lands as a placeholder; real rates/scopes are deferred.

### [9.7] Vendored EditorJS Image and List Version Pins

- **Decision**: Vendor pinned `@editorjs/image@2.10.3` and `@editorjs/list@2.0.9` builds and record exact versions/licenses in-repo.
- **Rationale**:
  - Ensures deterministic admin behavior and avoids CDN/runtime drift.
  - Maintains license traceability alongside vendored assets.
  - Uses the list build that preserves nested canonical list data during admin round-trips.
- **Rules**:
  - No CDN fallback.
  - Exact versions and license files must be recorded with the vendored bundle.
  - Image alt text is supplied through the canonical caption fallback instead of a custom tune.
  - Future upgrades require explicit re-vendor + doc update.

### Phase 9 Deferred Media Work

- **Orphan cleanup**: Replaced or deleted hero and inline images may leave files in storage. Cleanup is deferred to a future media-management pass.
- **Throttle tuning**: Upload and media-listing views use a standard user throttle hook. Route-specific scopes and rates are deferred until operational limits are known.

## Report Fixes & Hardening (Epic 11)

### [11.1] API Versioning Strategy

- **Decision**: Keep `/api/v1/` as a plain URL-prefix convention. No DRF versioning mechanism (`URLPathVersioning`, `NamespaceVersioning`, etc.) is wired up.
- **Rationale**:
  - No `v2` need exists today, and no external consumers depend on this API yet — there's nothing to version against.
  - The alternative (DRF `URLPathVersioning`) would require consolidating the ~6 scattered `path('api/v1/', include(...))` registrations across `tosca_api/urls.py` into a single `path('api/<version>/', include(...))` wrapper, plus `REST_FRAMEWORK` settings (`DEFAULT_VERSIONING_CLASS`, `ALLOWED_VERSIONS`, `DEFAULT_VERSION`). Visible URLs would stay identical to today, so the only real gain right now is `request.version` becoming available in views — not worth a routing-wide refactor with no consumer to serve.
- **Alternatives Considered**: DRF `URLPathVersioning` (deferred, not rejected — see trigger below) and `NamespaceVersioning` (would require app-level URL namespacing, more invasive for the same zero current benefit).
- **Revisit when**: a real breaking API change is needed, or an external (non-frontend-owned) consumer starts depending on this API. At that point, implement `URLPathVersioning` as scoped above rather than inventing a new scheme.
- **Raw originals**: Direct `MEDIA_URL` access can expose original uploads, including retained EXIF metadata. Production hardening should lock raw originals behind the intended storage/auth boundary and route public consumption through metadata-clean derivatives.
