# Serializer Standardization Plan

## Goal Description

The current application serializers possess varying styles, naming conventions, and validation practices. We are going to standardize the DRF serializes across the `campaigns`, `events`, `feedback`, and `geostories` apps.

## The "Superior" Style: `events/serializers.py`

We will adopt the structural and behavioral patterns found in `events/serializers.py` as our baseline, and further establish explicit validation standards.

The unified style guidelines are:

1. **Separation of Concerns:**
   Every core model should have three distinct serializers:
   - `<Model>ListSerializer`: Slim, optimized for list views (`GET /`).
   - `<Model>DetailSerializer`: Full, with nested fields and relations (`GET /{id}/`).
   - `<Model>WriteSerializer`: Exclusively for creation and updating (`POST /`, `PATCH /{id}/`).

2. **Strict Read-Only Protection (`Meta.read_only_fields`):**
   - For `ListSerializer` and `DetailSerializer`, explicitly set `read_only_fields = fields`. This prevents accidental writes and clarifies the serializer's intent purely as a read conduit.
   - For `WriteSerializer`, explicitly list immutable fields (e.g., `id`, `created_at`, `updated_at`, `created_by`).

3. **Robust Validation:**
   - Write serializers should implement an explicit `validate(self, attrs)` method when cross-field or model-backed validation is required.
   - If a model has a `clean()` method, serializer validation must evaluate the full candidate object state, not `attrs` in isolation.
   - For `create`, instantiate a transient model from the incoming values and call `.clean()` before save.
   - For `update` and `partial_update`, merge `attrs` onto `self.instance` first, then call `.clean()` on that merged state.
   - Validation must preserve DRF `400` responses at the API boundary instead of allowing Django model validation errors to surface during `save()`.
   - For partial updates, validation must run against `instance + attrs`, not `attrs` alone.
   - If the implementation uses `full_clean(exclude=...)` instead of `clean()`, the exclude behavior for omitted PATCH fields must be explicitly defined.

4. **File Structure and Documentation:**
   - Use standardized header blocks (`# =================...`) to group nested serializers and model serializers.
   - Use clear docstrings describing the purpose of each serializer.

## Important Public API / Interface Notes

- No endpoint URLs change.
- No response-shape changes are intended for existing campaign, event, feedback, or geostory endpoints beyond internal Python serializer class renames and view wiring.
- Internal serializer class names will change as follows:
  - `CalendarEventCreateSerializer` -> `CalendarEventWriteSerializer`
  - `GeoFeedbackCreateUpdateSerializer` -> `GeoFeedbackWriteSerializer`
  - `GeoStorySerializer` -> `GeoStoryWriteSerializer`
  - `CampaignSerializer` -> `CampaignListSerializer`, `CampaignDetailSerializer`, `CampaignWriteSerializer`

## Proposed Changes

### `tosca_api/apps/campaigns/serializers.py`

- Split `CampaignSerializer` into `CampaignListSerializer`, `CampaignDetailSerializer`, and `CampaignWriteSerializer` for naming consistency with the other apps.
- There is no nested campaign detail payload in the current scope; this split is structural and low-risk.
- `CampaignListSerializer`
  - Fields: `id`, `title`, `summary`, `status`, `visibility`, `created_by`, `created_at`, `updated_at`
  - `read_only_fields = fields`
- `CampaignDetailSerializer`
  - Same fields as `CampaignListSerializer` for now
  - `read_only_fields = fields`
- `CampaignWriteSerializer`
  - Same fields as the current `CampaignSerializer`
  - `read_only_fields = ["id", "created_by", "created_at", "updated_at"]`

### `tosca_api/apps/events/serializers.py`

- _Already our baseline._
- Rename `CalendarEventCreateSerializer` to `CalendarEventWriteSerializer` for naming consistency.
- Add an explicit `validate()` method to `CalendarEventWriteSerializer`.
- The serializer must mirror the existing model rule that `end_datetime >= start_datetime`; it should not introduce a new rule.
- Validation must evaluate the full candidate state:
  - On create: validate using the incoming attrs.
  - On patch/update: merge incoming attrs onto `self.instance` before calling model-backed validation.

### `tosca_api/apps/feedback/serializers.py`

- Rename `GeoFeedbackCreateUpdateSerializer` to `GeoFeedbackWriteSerializer`.
- Add `read_only_fields = fields` to `GeoFeedbackListSerializer` (currently missing).
- `GeoFeedbackWriteSerializer.validate()` must use the same merged-state validation pattern as events so PATCH requests validate against the persisted object plus incoming attrs.
- `FeedbackSubmissionSerializer.validate()` may validate payload-local concerns only:
  - field typing/parsing
  - rating bounds if needed
  - geometry payload shape/parsing
- Campaign-specific submission rules must be validated using the parent `GeoFeedback` resolved from the URL, not inferred from serializer input alone.
- Default implementation approach:
  - Pass `feedback` into serializer context from the `submit` action.
  - `FeedbackSubmissionSerializer.validate()` reads `self.context["feedback"]` and enforces `rating_enabled`, `form_enabled`, and `allow_drawings`.
  - The `submit` action remains responsible for injecting the feedback object and saving `submitted_by`.
- If context injection is not implemented, these campaign-specific submission checks must remain in the view and must not be moved incompletely.

### `tosca_api/apps/geostories/serializers.py`

- Rename `GeoStorySerializer` to `GeoStoryWriteSerializer`.
- Update `FeatureLinkSerializer`, `GeoContextSerializer`, and `LayerRefSerializer` to use `read_only_fields = fields` (currently they duplicate the field list manually).
- Update `GeoStoryListSerializer` and `GeoStoryDetailSerializer` `read_only_fields` syntax to use `read_only_fields = fields` consistently.
- Ensure `GeoStoryWriteSerializer` correctly handles any cross-field validation rules (if any).
- This standardization pass does not add nested write behavior for `context` or `layers`; those remain out of scope here and are already tracked in the existing geostory nested-write tasks.

### View-side Effects (`tosca_api/apps/*/views.py`)

- `CampaignViewSet`
  - Replace `serializer_class = CampaignSerializer` with `get_serializer_class()`.
  - `list` -> `CampaignListSerializer`
  - `retrieve` -> `CampaignDetailSerializer`
  - write actions -> `CampaignWriteSerializer`
- `CalendarEventViewSet`
  - Rename imports and references to `CalendarEventWriteSerializer`.
  - Keep `CalendarEventGeoSerializer` for spatial list/within endpoints.
  - Keep the current list vs retrieve branching.
- `GeoFeedbackViewSet`
  - Rename imports and references to `GeoFeedbackWriteSerializer`.
  - Keep the `submit` action serializer path explicit.
  - Inject `feedback` into serializer context for `submit` if serializer-level campaign validation is adopted.
- `GeoStoryViewSet`
  - Rename imports and references to `GeoStoryWriteSerializer`.
  - Keep the current list vs retrieve branching.

## Validation / Test Expectations

- `CalendarEvent` create rejects `end_datetime < start_datetime` with HTTP 400.
- `CalendarEvent` patch of only `end_datetime` rejects invalid merged state with HTTP 400.
- `GeoFeedback` create rejects:
  - `rating_enabled=False` and `form_enabled=False`
  - `form_enabled=True` without `custom_form`
- `GeoFeedback` patch rejects invalid merged state when toggling flags on an existing valid record.
- `FeedbackSubmission` submit rejects:
  - missing rating when `feedback.rating_enabled=True`
  - missing form data when `feedback.form_enabled=True`
  - geometry when `feedback.allow_drawings=False`
- Existing list/detail serializers remain read-only and preserve current response fields.
- Where the app already has API test coverage, add endpoint-level regression tests in addition to serializer-level validation tests.

## Assumptions and Defaults

- Keep the document's overall structure and intent; this is a revision, not a rewrite from scratch.
- Keep the campaign serializer split in the plan, but make it explicit and low-risk.
- Treat the two review findings around PATCH validation and feedback submission validation as mandatory corrections.
- Do not add new product scope such as geostory nested writes, new endpoints, or response payload redesign.
- Use "candidate state validation" as the canonical term for create/update validation against the full object state.

## Open Questions / Requests for Confirmation

- Should `FeedbackSubmission` campaign-rule validation move into the serializer via context injection, or intentionally remain in the view?
  - Default assumption for this plan: use serializer context injection with `feedback` supplied by the `submit` action.
- Should this standardization pass include serializer tests only, or also endpoint-level regression tests for create/PATCH validation behavior?
  - Default assumption for this plan: add both serializer-level and API-level regression tests where existing coverage already exists.
- Should `CampaignListSerializer` and `CampaignDetailSerializer` remain identical for now, or be collapsed later if the split proves unnecessary?
  - Default assumption for this plan: keep the split for consistency in this pass and revisit later only if it creates maintenance churn.
