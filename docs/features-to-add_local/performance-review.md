# Architecture & Performance Review

Comparing our implementation plan against `postgis-checklist.md`.

---

## 🛑 Required Changes (We must fix these)

### 1. Pagination Strategy

**Checklist**: "No OFFSET pagination on spatial endpoints"
**Current Plan**: `StandardResultsSetPagination` (PageNumberPagination uses OFFSET/LIMIT)
**Why Change**: `OFFSET` becomes extremely slow on large datasets because the DB must verify visibility for all skipped rows. For spatial data, this performance hit is magnified.
**Action**:

- Switch all list endpoints (`/stories/`, `/events/`, `/feedback/`) to `CursorPagination`.
- **Note**: This prevents "Jump to page 5" UI, but enables infinite scroll which is standard for feeds.

### 2. Infrastructure (Production Phase)

**Checklist**: "PgBouncer in front of Postgres"
**Current Plan**: Direct Docker Compose connection.
**Why Change**: Django opens a new connection per request. PostGIS connections are heavy memory consumers.
**Action**: Add `pgbouncer` service to `docker-compose.prod.yml` (not critical for dev/MVP, but required before Load Testing).

### 3. Spatial Indexes

**Checklist**: "Every geometry column has a GiST index"
**Current Plan**: Django creates these automatically, but we must verify.
**Action**: Explicitly check migrations ensure `spatial_index=True` (default).

---

## 🛡️ Defending Our Approach (Deviations from Checklist)

### 1. Primary Keys: UUID vs. BigInt

**Checklist**: "Tables use BIGINT / BIGSERIAL as internal primary keys... UUIDs are external identifiers"
**Our Approach**: Use `UUID` as the Primary Key.
**Defense**:

- **Complexity**: Dual-ID systems (internal Int ID + external UUID) add significant complexity to the application layer, serializers, and frontend logic.
- **Distributed Identity**: Events/Stories might be synched from external systems or pushed to mobile offline caches. UUIDs guarantee uniqueness without central coordination.
- **Performance**: For datasets under 50-100M rows, the performance penalty of UUID joins vs BigInt joins is negligible for our use case.
- **Security**: Prevents ID enumeration attacks (scanning `/api/campaigns/1/`, `/api/campaigns/2/`).

### 2. JSONB Usage

**Checklist**: "No EAV tables... No JSONB filtering in hot paths"
**Our Approach**: `GeoFeedback` uses `form_schema` (JSONB) and `FeedbackSubmission` uses `form_data` (JSONB), effectively an EAV pattern.
**Defense**:

- **Requirement Flexibility**: Administrators need to build custom forms (text, checkboxes) dynamically. Rigid columns cannot support "Survey A has Question 1" vs "Survey B has Question 2".
- **Mitigation**: We will **NOT** filter on `form_data` values in hot paths. List API will filter by standard columns (`campaign_id`, `created_at`, `user_id`). If analytics on JSON answers are needed, we will do it via the async Analytics/Celery pipeline (read-only), not the OLTP API.

### 3. Geometry SRID

**Checklist**: "No runtime ST_Transform required for common queries"
**Our Approach**: Store `SRID=4326` (Lat/Lon).
**Defense**:

- **Standard**: JSON/REST APIs standard is WGS84 (4326).
- **Client-Side**: Modern frontend clients (MapLibre/Leaflet) handle 4326 natively. Vector tiles (MVT) do require 3857, but PostGIS `ST_AsMVT` functions handle this transformation efficiently with indexes.

---

## Use of "Triggers" (Checklist Point 6)

**Checklist**: "No business logic implemented via triggers"
**Our Plan**: We considered a Trigger for `FeatureLink` cross-campaign validation.
**Defense**: Validating constraints (Data Integrity) is NOT business logic; it is schema logic. A trigger enforcing "Source and Target must share Campaign ID" is a strong consistency guarantee. However, following your earlier guidance, we will stick to **Django `clean()`** for Phase 1/2 to keep logic in Python, and only move to Triggers if we have raw SQL importers.

---

## Action Plan Updates

1. **Update Task 0.2**: Ensure `CursorPagination` is configured as default or per-view.
2. **Update Task 3.1**: Add note about performant JSONB usage (no deep filtering).
3. **Infrastructure**: Add Phased task for PgBouncer setup (Phase 4 / Prod).
