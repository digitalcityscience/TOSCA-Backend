> Development note: Run Django, uv, pytest, and migration checks inside the
> `tosca-django-api` container, not on the host.

# Epic 11: Organizations, Object-Scoped Authorization, and S3-Compatible Storage

Status: in progress. Phase 0 and Phase 2's geodata_providers refactor block
(29, 31, 35, 40-45) are complete. Phase 1 hygiene issues 17, 19-25, 27, 30,
32, 34, 36 are also complete (22/51 issues done — see per-issue "✅ Done"
markers below and the Issue Index table). Not started: storage (10-16, 48),
Phase 3 Organizations & Authorization (1-9, 51), Phase 4 (18, 26, 28, 37), and
remaining Phase 1 hygiene (33 — deferred by choice, not urgent until the
feedback feature ships to the frontend; 38, 39 — deferred priority; 46, 47,
49, 50 — added 2026-07-13 from a follow-up code-review pass, along with
the inline "(2026-07-13)" notes on issues 5, 10, 12, 18, and 28).
Source material: `docs/application-review-report.md` ("Permissions and object-level
access", "Admin hardening"), `docs/development/s3-production-media-roadmap.md`,
`docs/development/epic-10/154-integrate-garage-s3-compatible-storage.md`.

This document is ordered by **execution phase (0 → 4)**, not by feature track.
Issue numbers are stable identifiers (used in "depends on" references
throughout) — they do not indicate reading order. Read top to bottom to get
the actual build order; use the Issue Index below to jump to a specific
issue by number.

## Epic Goal

Three tracks, scoped in the same planning pass, now interleaved into one
execution order:

1. **Organizations + object-scoped authorization** (Phase 3). Today,
   `is_staff` is global: any staff user can see and edit every Campaign and
   every geodata Workspace. This epic adds an `Organization` concept that
   owns both Campaigns and geodata Workspaces, and makes Django — not
   Keycloak — the place where "can this user do this to this resource" is
   decided.
2. **S3-compatible object storage (Garage)** (Phase 1). Media currently
   lives on a single Docker volume, which blocks horizontal scaling and has
   no durability story. This track adopts the already-detailed
   `s3-production-media-roadmap.md` as this epic's storage issues, converted
   into the same issue format as the rest of the epic.
3. **Every actionable finding from `application-review-report.md`** that
   isn't already covered by (1) or (2) (Phases 0, 1, 2, 4). Per explicit
   instruction, these are prerequisite work — several of them touch the
   exact files (1) is about to change (`admin.py`, `api/views.py`,
   `settings/base.py`, `models.py` across multiple apps), so they're
   sequenced to land first.

Track (2) has no file overlap with (1) or (3) and can run fully in parallel
with the whole sequence below — see the file-overlap notes in
"Parallelization Guidance."

## Decisions Already Made (do not re-litigate without a reason)

- **Organization scope: both Campaigns and geodata providers.** A single
  `Organization` owns `Campaign` (and therefore `Event`, `GeoStory`,
  `GeoFeedback`, which already FK to `Campaign`) and `Workspace` (and
  therefore `Store`, `Layer`, `Style`, which already FK to `Workspace`). No
  new FK is needed on the child models — scoping resolves through the
  existing `campaign` / `workspace` foreign keys.
- **`GeodataEngine` stays unscoped.** It represents a GeoServer/engine
  connection, which is shared infrastructure, not tenant data. `Workspace`
  is the tenancy boundary on the geodata side — this matches GeoServer's own
  concept of a workspace as a namespace.
- **Membership source of truth: hybrid.** Keycloak is authoritative for
  *identity* — who a user is, what groups/roles they hold in Keycloak. Django
  is authoritative for *authorization* — what a given user/group/role is
  allowed to do. Keycloak groups/roles get synced into a Django `Membership`
  table; every permission check in Django reads `Membership`, never Keycloak,
  at request time.
- **Superusers bypass org scoping entirely** (see and edit everything), same
  as today. Org scoping applies to non-superuser staff.
- **No denormalized `organization` FK on child models.** `Event.campaign.organization`
  and `Layer.workspace.organization` are resolved via joins/`select_related`,
  not copied onto every table. Add a denormalized FK later only if a measured
  query-performance problem shows up — consistent with this repo's existing
  YAGNI stance on schema (see `application-review-report.md`).
- **Since the project is pre-production, `organization` FKs on `Campaign` and
  `Workspace` can be added as required (`NOT NULL`)** with a single seed/default
  organization for existing dev fixtures. No backfill migration complexity
  needed.
- **Garage cluster provisioning is out of scope.** Standing up the Garage
  service itself (cluster layout, disks, replication) is an ops concern
  outside this Django repo. These issues make Django a correctly-configured
  S3 client; they do not deploy Garage.
- **This epic supersedes GitHub issue `#154`** ("Integrate Garage S3-Compatible
  Storage", epic-10) — `#154`'s scope (upload-backed provider source files
  only) is narrower than the adopted roadmap here (all media surfaces).
  Recommend closing or redirecting `#154` to point at issues 10-16 below to
  avoid duplicate tracking.
- **Audit logging and service-account/M2M auth are explicitly deferred.** They
  are included as issues (8, 9) so the scope is visible, but they are not
  required for this epic to be considered done. Pick them up later if/when
  needed.
- **GeoServer direct-write is intentionally allowed for org Writers, bypassing
  Django validation.** GeoServer is accessed directly by clients (WMS/WFS/
  WFS-T), not proxied through Django (see
  `docs/development/IAM_access_control.md` and
  `docs/development/epic-11-User_ACL_decision.md`). A user with the
  `ROLE_ORG_<org>_WRITER` Keycloak role can write straight to GeoServer via
  WFS-Transaction, which bypasses Django entirely — this contradicts the
  "Django is the control plane, GeoServer only publishes" principle in
  `docs/development/CLAUDE.md`. Accepted trade-off, deliberately: Issue 32's
  geometry validity check, Issue 8's audit logging, and `MediaAsset` tracking
  (issue 48) do not see direct WFS-T writes. Chosen because Django's upload
  flow doesn't yet cover every data type/scenario this early, and blocking
  direct write risks stalling real work when it doesn't. Revisit and tighten
  (lock `<workspace>.*.w` to a single Django service-account role, remove
  write from `ROLE_ORG_*_WRITER`) once the Django upload flow is mature
  enough to cover all needed cases. `ROLE_GEOSERVER_ADMIN` (global, manually
  assigned to a small set of trusted users, separate from org Writer roles)
  is the escape valve for GeoServer console access outside this org-scoped
  model.
- **Django `Membership` is the canonical authorization source; Keycloak org-roles
  are a synced projection of it for GeoServer.** GeoServer can't call Django at
  request time, so a user's org `access_level` in Django is *projected* into a
  Keycloak role (`ROLE_ORG_<org>_<level>`) that lands in the JWT; GeoServer reads
  that role. Two enforcement planes, one brain: the API/admin plane reads
  `Membership` directly; the GeoServer plane reads the JWT roles that mirror it.
  Direction: **membership role → Keycloak role is authoritative** — editing a
  `Membership` in Django admin without re-projecting to Keycloak would let the
  same user have different effective rights on the two planes, so the projection
  must run on membership change. Identity/authentication stays Keycloak → Django;
  only the org-role authz is Django-driven. See the role-mapping table in
  `epic-11-User_ACL_decision.md` §5.0/5.1. (This extends the earlier "hybrid
  membership source of truth" bullet with an explicit projection direction and
  supersedes any read of Issue 2 as a purely Keycloak → Django one-way sync.)
- **User revocation is bounded by access-token TTL, not by the GeoServer ACL
  sync.** The synchronous ACL push (new Issue 51 / ACL doc §8) only reconciles
  *ACL rule* changes; removing a user from an org is a *token claim* change — their
  existing JWT still carries `ROLE_ORG_<org>_READER` until it expires. Policy:
  short access-token TTL (≤ ~5 min) so a revoked membership can stay open on
  GeoServer for at most one TTL; add Keycloak back-channel logout / session
  revocation if/when a workspace needs instant revocation. See ACL doc §6c.
- **Keycloak organization modeling: single decision — native Organizations
  primary, Groups+Roles fallback.** Prefer Keycloak native Organizations (26.7
  delegated admin + FGAP); if the verification spike shows the deployed version
  doesn't behave as documented, fall back to plain `Group` + realm roles
  (`/org-<slug>/{readers,writers,admins}` mapped to `ROLE_ORG_<org>_<level>`).
  Either way Django and the role projection are identical — GeoServer/Django only
  ever see `ROLE_ORG_*` roles, not how Keycloak produced them. This closes
  Issue 2's open "(A) roles vs (B) groups" choice. See ACL doc §1.
- **POC sharing is via a separate shared workspace, not a `shared_with` field.**
  A prior ACL-doc revision proposed workspace `visibility` incl. `SHARED` + a
  `shared_with` M2M; the second grilling round reversed this for the POC.
  `Workspace.visibility` is **PRIVATE | PUBLIC only** for the POC; cross-org
  sharing is done by creating a dedicated shared `Workspace` (owned by the
  sharing org) that re-publishes the same PostGIS layers under a second GeoServer
  namespace (no data copy) with an ACL granting the partner org reader; write
  stays with the owner org. `shared_with` M2M and layer-level sharing are
  deferred to v2. See ACL doc §5a/§9 — and this changes Issue 3's scope (see
  that issue's note).
- **PUBLIC visibility maps to anonymous GeoServer read + owner-org write.**
  `visibility=PUBLIC` → `<ws>.*.r = *` (anonymous/`ROLE_ANONYMOUS` read) while
  `<ws>.*.w` stays locked to `ROLE_ORG_<org>_WRITER`. PUBLIC widens read only,
  never write. See ACL doc §6d.

## Architecture Overview: Organizations & Authorization

```
Keycloak (identity + groups/roles)
        │  on login / token refresh
        ▼
role_sync.py  (existing: syncs realm roles → is_staff/is_superuser)
        +
NEW: org_sync.py-style hook (issue 2): syncs Keycloak groups → Django
     Membership(user, organization, role)
        │
        ▼
Django Membership table  ◄── authoritative for authorization decisions
        │
        ▼
Permission classes (issue 4) read Membership, not Keycloak, at request time
        │
        ▼
Campaign.organization, Workspace.organization  (issue 3)
        │
        ▼
Event/GeoStory/GeoFeedback (via campaign), Store/Layer/Style (via workspace)
  inherit scoping through existing FKs — no new columns needed
```

The existing `authentication/permissions.py` already has `IsSuperAdmin`,
`IsAdmin`, `IsEditor`, `IsViewer` built around global `is_staff`/Django
groups. Issue 4 extends this naming pattern to be org-scoped rather than
replacing it with new vocabulary.

## Phase Overview

| Phase | Contents | Why this order |
|-------|----------|-----------------|
| 0 | Issue 17 | Trivial one-line bug fix — no reason to delay it even a day. |
| 1 | Issues 19-25, 27, 30, 32-34, 36, 46, 47, 49, 50 (hygiene) · 10-16, 48 (storage) · 38-39 (deferred) | Fully independent of Phase 3 and of each other — parallelize freely, any order. |
| 2 | Issues 29, 31, 35, 40-45 | Touch the same files Phase 3 is about to touch (models, admin, geodata_providers views) — land first to avoid conflicts. |
| 3 | Issues 1-9 | Organizations & Authorization — the epic's main feature, built on a now-settled codebase. |
| 4 | Issues 18, 26, 28, 37 | Deferred to the end on purpose: CI (18) and the two production-hardening issues (26 admin IP allowlist, 28 rate limiting) aren't needed while the project is still in active development with no real traffic. Migration squash (37) stays structurally last regardless. |

Storage (part of Phase 1) has no file overlap with Phase 2 or 3 and can
finish before, during, or after the rest of this sequence with no
coordination needed.

## Issue Index (by phase, then by number)

| Phase | # | Issue | Track | Depends on | Priority |
|-------|---|-------|-------|------------|----------|
| 0 | 17 | Fix `LayerViewSet.preview()` missing import | Report fix | — | Required, first — ✅ |
| 1 | 10 | Storage guardrails before S3 | Storage | — | Required |
| 1 | 11 | Configurable S3 storage backend | Storage | 10 (soft) | Required |
| 1 | 12 | Route upload surfaces through S3 | Storage | 11, 48 (soft) | Required |
| 1 | 13 | Private originals + public derivatives | Storage | 12 | Required |
| 1 | 14 | Media migration to S3 | Storage | 12 | Required |
| 1 | 15 | Static files S3/CDN decision | Storage | 11 | Deferred |
| 1 | 16 | Storage ops, backup, lifecycle | Storage | 11 | Deferred |
| 1 | 19 | Settings hardening + system checks | Report fix | — | Required — ✅ |
| 1 | 20 | Fix URL routing duplication | Report fix | — | Required — ✅ |
| 1 | 21 | Health & readiness endpoints | Report fix | — | Required — ✅ |
| 1 | 22 | Deduplicate template tag modules | Report fix | — | Required — ✅ |
| 1 | 23 | Shared visibility queryset helpers | Report fix | — | Required — ✅ |
| 1 | 24 | Move vendored `geoserver-rest` out of app boundary | Report fix | — | Required — ✅ |
| 1 | 25 | DB connection pooling + statement timeout | Report fix | — | Required — ✅ |
| 1 | 27 | Remove committed `.env.dev` credentials | Report fix | — | Required — ✅ |
| 1 | 30 | Audit `geocontext` for N+1 queries | Report fix | — | Required — ✅ |
| 1 | 32 | Geometry validity checks on submissions | Report fix | — | Required — ✅ |
| 1 | 33 | Data retention/cleanup job for feedback | Report fix | — | Required |
| 1 | 34 | Decide real API versioning approach | Report fix | — | Required — ✅ |
| 1 | 36 | Schema hygiene cleanup (cascade/constraint nits) | Report fix | — | Optional/opportunistic — ✅ |
| 1 | 38 | i18n scope decision | Report fix | — | Deferred |
| 1 | 39 | OpenTelemetry observability, phases 1-3 | Report fix | — | Deferred |
| 1 | 46 | Configure DRF throttle rates (throttling currently inert) | Report fix | — | Required |
| 1 | 47 | Restrict `GeodataEngine` write API to admins (interim) | Report fix | — | Required, trivial |
| 1 | 48 | DB-tracked media assets (`MediaAsset` model) | Storage | 11 (soft) | Required, before 12 |
| 1 | 49 | Replace custom CORS middleware with `django-cors-headers` | Report fix | — | Required |
| 1 | 50 | Small review follow-ups (serializer/pyproject nits) | Report fix | — | Optional/opportunistic |
| 2 | 29 | Indexes on existing FK/geometry fields | Report fix | — | Required, before 3 — ✅ |
| 2 | 31 | Destructive-action safety in admin | Report fix | — | Required, before 6 — ✅ |
| 2 | 35 | Switch existing models to UUIDv7 | Report fix | — | Required, before 3 — ✅ |
| 2 | 40 | Collapse duplicated CRUD in `geoserver/client.py` | Report fix | — | Required, before 5 — ✅ |
| 2 | 41 | Split `sync_service.py` by resource | Report fix | — | Required, before 5 — ✅ |
| 2 | 42 | Enforce service layer as the only boundary | Report fix | 40, 41 | Required, before 5 — ✅ |
| 2 | 43 | Typed results + real exception hierarchy | Report fix | — | Required, before 5 — ✅ |
| 2 | 44 | Delete Martin/PG_Tileserv placeholder abstraction | Report fix | — | Required, before 5 — ✅ |
| 2 | 45 | Thin `LayerViewSet.update` and similar fat views | Report fix | 42, 43 | Required, before 5 — ✅ |
| 3 | 1 | Organization & Membership models | Org/Auth | — | Required |
| 3 | 2 | Keycloak group/role → Membership sync | Org/Auth | 1 | Required |
| 3 | 3 | `organization` FK on Campaign & Workspace | Org/Auth | 1, 29, 35 (soft) | Required |
| 3 | 4 | Org-scoped DRF permission classes | Org/Auth | 1, 3 | Required |
| 3 | 5 | Apply permissions to API viewsets | Org/Auth | 4, 40-45 (soft) | Required |
| 3 | 6 | Org-scoped filtering in Django admin | Org/Auth | 1, 3, 31 (soft) | Required |
| 3 | 7 | Organization/Membership management surface | Org/Auth | 1 | Required |
| 3 | 8 | Audit logging | Org/Auth | 1 | Deferred |
| 3 | 9 | Service-account / M2M auth | Org/Auth | 1, 4 | Deferred |
| 3 | 51 | Keycloak role projection + GeoServer ACL sync | Org/Auth | 1, 3 | Required (GeoServer plane) |
| 4 | 18 | CI/CD + dependency scanning + mypy baseline | Report fix | — | Required, deferred to end |
| 4 | 26 | IP allowlist / VPN gate for `/admin/` | Report fix | — | Required, deferred to end |
| 4 | 28 | Rate limiting: feedback + catalog endpoints | Report fix | — | Required, deferred to end |
| 4 | 37 | Squash migrations before first release | Report fix | 3, 35 | Required, last |

"Soft" dependency = recommended sequencing to avoid file conflicts or
duplicated rework, not a hard code blocker.

## Parallelization Guidance

- **Phase 1's storage issues (10-16) never touch the same files as anything
  in Phase 2 or 3.** Staff them independently, any time, in parallel with
  the entire rest of this document.
- **Within Phase 1**, every hygiene issue (19-25, 27, 30, 32-34, 36, 38, 39,
  46, 47, 49, 50) is independent of every other one — grab any row and go,
  split across as many people as you have.
- **Within Phase 2**, issues 29, 31, and 35 are independent of each other.
  The geodata_providers refactor block (40-45) has its own internal order:
  40, 41, 43, 44 can run in parallel with each other; 42 needs 40+41 done;
  45 needs 42+43 done.
- **Within Phase 3**, issue 1 is the only true blocker. Once it lands, issues
  2, 3, and 7 can start in parallel. Issues 5 and 6 both only need 3 (and 1)
  — issue 6 does not need issue 4. Issues 8 and 9 can be picked up any time
  after issue 1, no urgency. Issue 51 (Keycloak role projection + GeoServer ACL
  sync) needs 1 and 3, and is the GeoServer-plane counterpart to the Django-plane
  enforcement in 4-6 — it can run in parallel with 4-6 since it touches new sync
  services, not the viewsets/admin those issues change.
- **Why Phase 2 comes before Phase 3, concretely:** issue 35 (UUIDv7) and
  issue 29 (indexes) touch migrations on the same models issue 3 is about to
  add a column to — land first to avoid a migration-graph conflict. Issue 31
  (admin destructive-action safety) touches `campaigns/admin.py` and
  `geodata_providers/admin.py`, the same files issue 6 (admin org-scoping)
  touches. Issues 40-45 touch `geodata_providers/api/views.py` and
  `admin.py`, the same files issue 5 (API permission wiring) touches.
- **File-overlap notes that are not hard blockers:** issue 19 (settings
  hardening) and issue 25 (DB pooling) both touch `settings/base.py`, as does
  Phase 1's issue 11 (S3 backend config) — different setting blocks, low
  conflict risk, just don't land all three in the same PR.
- **Why 18, 26, and 28 moved to Phase 4:** none of the three are depended on
  by anything else in this epic — they were only in Phases 0/1 because they
  were cheap/high-leverage, not because anything requires them. CI (18) is a
  workflow safety net, and admin IP-allowlisting (26) / endpoint rate
  limiting (28) are production-traffic concerns — deferrable while the
  project has no real users. They have no dependencies on each other or on
  issue 37, so do them in any order once picked up; issue 37 still has to be
  last structurally regardless of when 18/26/28 land.

---

# Phase 0: Land First

A trivial, self-contained bug fix. No dependencies, no reason to delay it
even a day. (CI/CD setup, originally also in this phase, has been moved to
Phase 4 — see "Why 18, 26, and 28 moved to Phase 4" above.)

### Issue 17: Fix `LayerViewSet.preview()` missing import

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required, do
first — it's a one-line fix.

**Explanation:** `os.path.splitext()` is called without importing `os` in
`tosca_api/apps/geodata_providers/api/views.py:668`. Any request to the
preview action raises `NameError` today.

**Implementation:** add `import os` at the top of the file.

**Test Cases:** `POST /layers/preview/` returns a successful response instead
of a 500; a regression test pins this.

**Acceptance Criteria:**
- [ ] Import added, endpoint works.
- [ ] Test added that would have caught this before merge.

---

# Phase 1: Parallel Hygiene & Storage

Everything in this phase is independent of Phase 2 and Phase 3, and mostly
independent of each other. Start immediately, alongside Phase 0, with as many
people as you have.

## Phase 1 — Hygiene Fixes

### Issue 19: Settings hardening and system checks

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required.

**Explanation:** Merges the settings-inconsistency findings with the
recommended Django system checks, since both are about the same file and the
same goal — production shouldn't be able to boot in an unsafe state.

**Implementation:**
- Collapse the duplicate `SPECTACULAR_SETTINGS` assignment in `base.py` into
  one; restore the dropped bearer auth scheme/response hook if still needed.
- Make `SECRET_KEY` fail fast in production instead of defaulting to
  `change-me-in-production`.
- Document `FIELD_ENCRYPTION_KEY` as a hard local-dev requirement.
- Remove scratch text and the throwaway `TEMPLATES` assignment in
  `production.py`.
- Switch production logging to stdout/stderr JSON instead of
  `/app/logs` files.
- Add custom Django system checks: `SECRET_KEY` not default,
  `FIELD_ENCRYPTION_KEY` set/valid, `ALLOWED_HOSTS` explicit, `DEBUG` false,
  Keycloak issuer/JWKS/client settings set, `GEOSERVER_ADMIN_PASSWORD` not
  default, media/static roots configured.

**Test Cases:** `manage.py check --deploy`-style test fails when any of the
above is unsafe; drf-spectacular schema generation still includes the bearer
auth scheme after the fix.

**Acceptance Criteria:**
- [ ] No duplicate settings assignments remain.
- [ ] Production fails to start with an unsafe `SECRET_KEY`.
- [ ] System checks cover all seven items listed above.

---

### Issue 20: Fix URL routing duplication

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required.

**Explanation:** `tosca_api/urls.py` imports `SpectacularAPIView`/
`SpectacularSwaggerView` twice and registers `admin/logout/` twice; auth URLs
are included both at root and under `accounts/`.

**Implementation:** remove duplicate imports and the duplicate route. Decide
whether the root/`accounts/` auth URL duplication is a deliberate alias — if
so, document it and add a redirect test; if not, remove one.

**Test Cases:** URL resolution test confirms each route resolves to exactly
one view; no route is registered twice in `django.urls` reverse lookup.

**Acceptance Criteria:**
- [ ] No duplicate imports or route registrations remain.
- [ ] Any intentional alias is documented and tested.

---

### Issue 21: Health and readiness endpoints

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required.

**Explanation:** The Docker healthcheck only hits `/`, proving Django is up
but nothing about DB reachability or dependency health.

**Implementation:** add `/healthz` (process liveness, no dependency checks)
and `/readyz` (DB connectivity + any critical dependency check). Keep
readiness shallow and fast — no GeoServer sync inside it.

**Test Cases:** `/healthz` returns 200 always when the process is up;
`/readyz` returns 503 when the DB is unreachable (simulate via a broken
connection in test) and 200 otherwise.

**Acceptance Criteria:**
- [ ] Both endpoints exist and are wired into the Docker healthcheck.
- [ ] `/readyz` correctly reflects DB down/up.

---

### Issue 22: Deduplicate template tag modules

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required, trivial.

**Explanation:** `geodata_providers/templatetags/__init__.py` and
`engine_tags.py` appear to contain duplicated logic.

**Implementation:** keep the implementation in `engine_tags.py`; empty
`__init__.py`.

**Test Cases:** existing template tag tests still pass after the move.

**Acceptance Criteria:**
- [ ] No duplicated logic between the two files.

---

### Issue 23: Shared visibility queryset helpers

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required.

**Explanation:** Events, feedback, geostories, and layer serializers each
repeat public/published visibility checks inline.

**Implementation:** add small named queryset methods (e.g.
`published_public()`) on the relevant managers/querysets. Avoid a generic
cross-app abstraction — keep each app's method matching its own domain
rules, since the report explicitly warns against hiding real domain
differences behind one shared helper.

**Test Cases:** each app's `published_public()` (or equivalent) returns
exactly the same rows the current inline logic does — a behavior-preserving
refactor test per app.

**Acceptance Criteria:**
- [ ] Each app's visibility logic is behind one named queryset method.
- [ ] No behavior change versus today's inline checks.

---

### Issue 24: Move vendored `geoserver-rest` out of the app package boundary

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required.

**Explanation:** The editable path dependency for `geoserver-rest`, imported
via `sys.path` manipulation in `geoserver/client.py`, causes lint noise and
unclear ownership.

**Implementation:** move to `vendor/` or a proper separate package/submodule;
exclude from app-level lint/test discovery (ties into issue 18's mypy/ruff
config); replace `sys.path` manipulation with a normal import path.

**Test Cases:** existing GeoServer client tests still pass after the move;
`ruff check` no longer flags the vendored code.

**Acceptance Criteria:**
- [ ] Vendored code lives outside `tosca_api/apps/geodata_providers/`.
- [ ] Import path is a normal Python import, no `sys.path` hacks.

---

### Issue 25: DB connection pooling and statement timeout

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required —
production blocker per the review.

**Explanation:** No `CONN_MAX_AGE`/pgbouncer means every request opens a
fresh Postgres connection; no `statement_timeout` means a stuck query can
hang a worker indefinitely. Same settings area, merged into one issue.

**Implementation:** add `CONN_MAX_AGE` at minimum (pgbouncer if
multi-instance deployment is imminent); add `statement_timeout` to the DB
`OPTIONS`.

**Test Cases:** a deliberately long-running query in a test is cut off at
the configured timeout; connection reuse is observable under
`CONN_MAX_AGE` (test via Django's connection introspection).

**Acceptance Criteria:**
- [ ] `CONN_MAX_AGE` (and/or pgbouncer) configured.
- [ ] `statement_timeout` configured and verified to actually cut off a
      long query.

---

### Issue 27: Remove committed `.env.dev` credentials

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required, trivial.

**Explanation:** `.env.dev` is committed with real-looking test credentials
(GeoServer admin password, Keycloak client secret, DB password).

**Implementation:** move values into `.env.example` as placeholders, or
explicitly document `.env.dev` as intentional/dev-only; add a pre-commit
check blocking any other `.env*` file from being committed.

**Test Cases:** pre-commit hook rejects a staged `.env.local` or `.env.prod`
file in a test scenario.

**Acceptance Criteria:**
- [ ] `.env.dev` handling is deliberate and documented either way.
- [ ] Pre-commit check exists for other `.env*` files.

---

### Issue 30: Audit `geocontext` for N+1 queries

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required.

**Explanation:** `events` and `geodata_providers` use `select_related()`/
`prefetch_related()` consistently; `geocontext` uses neither, despite FK
relationships nested serializers will resolve per-row.

**Implementation:** audit `geocontext` serializers/viewsets and add
`select_related`/`prefetch_related` the same way `events` already does.

**Test Cases:** a Django `assertNumQueries`-style test on the relevant
`geocontext` list endpoint, before and after the fix.

**Acceptance Criteria:**
- [ ] No N+1 pattern remains on `geocontext` list endpoints.
- [ ] Query-count regression test added.

---

### Issue 32: Geometry validity checks on public submissions

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required.

**Explanation:** `FeedbackSubmission.clean()` checks *whether* geometry is
allowed but never *whether it's valid* — a self-intersecting polygon from a
public submission flows straight into GeoServer.

**Implementation:** add a `geometry.valid` (GEOS) or `ST_IsValid` check in
`clean()`, raising `ValidationError` for invalid geometry.

**Test Cases:** a self-intersecting polygon submission is rejected with a
clear validation error; a valid polygon passes unchanged.

**Acceptance Criteria:**
- [ ] Invalid geometry is rejected before save, with a clear error message.

---

### Issue 33: Data retention/cleanup job for feedback submissions

**Track:** Report fix. **Depends on:** none. **Priority:** required.

**Explanation:** `FeedbackSubmission.is_anonymized` exists but nothing ever
sets it — PII persists indefinitely with no purge mechanism.

**Implementation:** add a `DATA_RETENTION_DAYS` setting and a management
command that anonymizes/deletes submissions older than the configured
window; schedule it (cron or equivalent) once a scheduler exists.

**Test Cases:** a submission older than the retention window is
anonymized/deleted by the command; one within the window is untouched;
`--dry-run` mode changes nothing.

**Acceptance Criteria:**
- [ ] Management command exists with dry-run support.
- [ ] Retention window is configurable via settings.

---

### Issue 34: Decide the real API versioning approach

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required — cheap
now, expensive once external consumers exist.

**Explanation:** `/api/v1/` today is a hardcoded URL prefix, not a DRF
versioning mechanism — there's no route to `/api/v2/` without a deeper
refactor.

**Implementation:** decide between DRF `URLPathVersioning`,
`NamespaceVersioning`, or continuing with the convention-only approach
deliberately (acceptable if documented as a conscious choice). Implement
whichever is chosen.

**Test Cases:** a versioned request routes to the correct view/serializer
version if a mechanism is implemented; existing `/api/v1/` behavior is
unchanged either way.

**Acceptance Criteria:**
- [ ] A versioning approach is chosen and documented, not left implicit.

---

### Issue 36: Schema hygiene cleanup (opportunistic)

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** optional — fix
opportunistically when those specific models are next touched, not urgent on
its own.

**Explanation:** Three small, unrelated schema nits bundled together because
none of them justify their own PR in isolation: `EventSeries.campaign` is
simultaneously `CASCADE` and nullable (inconsistent if a series can exist
without a campaign); legacy `unique_together` and modern `UniqueConstraint`
are both in use across different models; through-tables (`EventLayer`,
`GeoStoryLayer`, `FeedbackLayer`) don't inherit `TimeStampedModel` so they
get `created_at` but no `updated_at`.

**Implementation:** resolve `EventSeries.campaign`'s cascade/nullable
inconsistency one way or the other; standardize on `UniqueConstraint`;
add `updated_at` to the three through-tables.

**Test Cases:** existing tests for affected models still pass; a new test
confirms `updated_at` exists and updates on the through-tables.

**Acceptance Criteria:**
- [ ] All three nits resolved (can ship independently/incrementally).

---

### Issue 46: Configure DRF throttle rates — throttling is currently inert

**Track:** Report fix. **Depends on:** none. **Priority:** required. Added
2026-07-13 (follow-up code-review pass).

**Explanation:** The three EditorJS views in `geocontext/views.py` declare
`throttle_classes = [UserRateThrottle]`, but no `DEFAULT_THROTTLE_RATES` is
configured anywhere in settings. DRF's built-in default rate for the `user`
scope is `None`, and `SimpleRateThrottle.allow_request()` returns `True`
unconditionally when the rate is `None` — so those endpoints have **zero**
throttling despite appearing throttled. The failure mode is silent, which is
exactly the class of problem issue 19's system checks exist to catch.

**Implementation:**
- Add `DEFAULT_THROTTLE_RATES` for the `user` and `anon` scopes to
  `REST_FRAMEWORK` in `settings/base.py` (rates env-configurable).
- Add a custom system check that fails when any view/setting declares a
  throttle whose scope resolves to a `None` rate.
- Issues 10 and 28 layer per-surface scoped throttles on top — this issue
  only makes the existing declarations real.

**Test Cases:** requests past the configured rate to an EditorJS endpoint
return `429`; a throttle scope with no configured rate fails the system
check.

**Acceptance Criteria:**
- [ ] `DEFAULT_THROTTLE_RATES` configured; a real `429` verified by test.
- [ ] System check rejects a throttle scope with no configured rate.

---

### Issue 47: Restrict `GeodataEngine` write API to admins (interim)

**Track:** Report fix. **Depends on:** none. **Priority:** required,
trivial — interim hardening, superseded by issue 5's full permission model.
Added 2026-07-13.

**Explanation:** `GeodataEngineViewSet` in `geodata_providers/api/views.py`
uses `permission_classes = [permissions.IsAuthenticated]` for CRUD; only the
`sync`/`sync_all` actions require `IsAdminUser`. Engines hold encrypted
GeoServer admin credentials, so today any authenticated Keycloak user can
create or modify engine configuration. Issue 5 replaces this permission
model wholesale, but Phase 3 may be months out and this is a one-line fix.

**Implementation:** require `IsAdminUser` for create/update/delete on
`GeodataEngineViewSet` (reads may stay `IsAuthenticated`); leave the full
org-scoped model to issue 5.

**Test Cases:** a non-staff authenticated user gets `403` on engine
create/update/delete; staff behavior unchanged.

**Acceptance Criteria:**
- [ ] Non-admin users can no longer write to the engines API.
- [ ] Covered by an API test.

---

### Issue 49: Replace hand-rolled CORS middleware with `django-cors-headers`

**Track:** Report fix. **Depends on:** none. **Priority:** required. Added
2026-07-13.

**Explanation:** `tosca_api/middleware.py` implements custom CORS handling.
It is competently written (origin-allowlist echo, `Vary: Origin`,
credentials gating), but it accepts `"*" in CORS_ALLOWED_ORIGINS` — combined
with `CORS_ALLOW_CREDENTIALS=True` (the current default) that would echo
*any* origin back with credentials allowed, the exact combination the CORS
spec forbids. `django-cors-headers` is the maintained standard, and the
existing setting names already match its conventions; adopting it removes
~60 lines of security-sensitive owned code.

**Implementation:** add `django-cors-headers`, swap the middleware entry,
verify the existing `CORS_*` settings map 1:1, delete the custom class.
Alternative if keeping the custom middleware is preferred: remove the `"*"`
branch and add a system check rejecting wildcard origins while credentials
are enabled.

**Test Cases:** preflight and simple-request CORS headers unchanged for an
allowed origin; a disallowed origin receives no CORS headers; wildcard +
credentials configuration fails startup (whichever path is chosen).

**Acceptance Criteria:**
- [ ] Custom middleware removed, or wildcard+credentials made impossible.
- [ ] Existing frontend CORS behavior verified unchanged.

---

### Issue 50: Small review follow-ups (opportunistic)

**Track:** Report fix. **Depends on:** none. **Priority:** optional/
opportunistic — bundled like issue 36 because neither justifies its own PR.
Added 2026-07-13.

**Explanation/Implementation:** two small nits:
- `feedback/views.py`'s `submit` action reads
  `is_anonymized=request.data.get("is_anonymized", False)` directly from raw
  request data, bypassing the serializer — any non-empty string, including
  `"false"`, is truthy. Add `is_anonymized` as a
  `BooleanField(required=False, default=False)` on the submission serializer
  and read it from `validated_data`.
- `pyproject.toml`: `ruff` sits in runtime `dependencies` (ships to
  production), and dev tooling is duplicated between
  `[project.optional-dependencies].dev` and `[dependency-groups].dev` with
  mismatched version floors. Consolidate on `[dependency-groups]` (the
  uv-native mechanism) and drop ruff from runtime deps. (mypy CI wiring
  stays with issue 18.)

**Test Cases:** posting `"is_anonymized": "false"` stores
`is_anonymized=False`; the app boots and the suite passes after the
dependency reshuffle.

**Acceptance Criteria:**
- [ ] `is_anonymized` goes through the serializer as a real boolean.
- [ ] No dev tooling in runtime dependencies; one source of truth for dev
      deps.

---

## Phase 1 — Storage (S3-Compatible, Garage)

Adopted from `docs/development/s3-production-media-roadmap.md`, reformatted
into this epic's issue structure. See that document for the full original
rationale, environment variable list, and rollout/rollback plan — it is not
duplicated in full here. This block has its own internal chain
(10 → 11 → 12 → 13/14, with 15/16 needing only 11; issue 48 — added
2026-07-13 — should land before 12 so upload surfaces write asset rows as
they move to S3) but is otherwise independent of every other phase in this
document.

### Issue 10: Storage guardrails before S3

**Track:** Storage. **Depends on:** none. **Blocks:** nothing
(soft-recommended before 11).

**Explanation**

Ships the security/ops fixes that should land before object storage is
introduced, so S3 rollout doesn't inherit today's known problems (SSRF hole,
no throttling, proxy header issues).

**Implementation**

- Fix SSRF protection for `EditorJSImageUploadByUrlView` (already tracked as
  a standalone high-priority finding in `application-review-report.md`), or
  disable upload-by-url in production if hardening isn't ready in time.
- Add DRF throttle scopes for: EditorJS upload-by-file, upload-by-url, media
  library listing, image derivative generation. Note (2026-07-13): the
  existing `throttle_classes = [UserRateThrottle]` declarations on these
  views are currently inert — no `DEFAULT_THROTTLE_RATES` is configured, so
  DRF resolves the rate to `None` and every request passes. Issue 46 fixes
  the global configuration; this issue adds the per-surface scopes on top.
- Fix proxy header handling: confirm where TLS terminates, preserve trusted
  `X-Forwarded-Proto`, consider `USE_X_FORWARDED_HOST` only if the proxy
  chain is trusted.
- Add a production system check that fails startup for unsafe media
  settings.

**Test Cases**

- SSRF tests: loopback, private ranges, link-local, IPv6, redirects to
  private IPs, DNS names resolving to private IPs — all rejected.
- Throttle limits enforced on each of the four listed endpoints.
- Proxy header test confirms generated URLs use `https://` behind the
  expected proxy chain.

**Acceptance Criteria**

- [ ] SSRF tests pass (per the report's original SSRF finding).
- [ ] All four endpoints have explicit throttle scopes configured.
- [ ] Production startup fails/warns on unsafe media settings.

---

### Issue 11: Configurable S3 storage backend

**Track:** Storage. **Depends on:** 10 (soft). **Blocks:** 12.

**Explanation**

Lets the app boot with either filesystem or S3-compatible storage behind one
setting, with filesystem remaining the default for local dev and tests.

**Implementation**

- Add `django-storages[s3]` and `boto3` to `pyproject.toml`/`uv.lock`.
- Add `DJANGO_STORAGE_BACKEND=filesystem|s3` and the S3 settings block
  (`S3_ENDPOINT_URL`, `S3_REGION_NAME`, `S3_ACCESS_KEY_ID`,
  `S3_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME`, `S3_PUBLIC_BUCKET_NAME`,
  `S3_ADDRESSING_STYLE`, `S3_SIGNATURE_VERSION`) to
  `tosca_api/settings/base.py`, per the exact shape already specified in
  `s3-production-media-roadmap.md`.
- Configure `STORAGES` conditionally based on `DJANGO_STORAGE_BACKEND`.
- Add a small custom storage class only if Django's storage API can't
  express private/public prefix handling or overwrite prevention directly.
- Update `.env.example` and README with the new variables.

**Test Cases**

- App boots with `DJANGO_STORAGE_BACKEND=filesystem` (existing behavior,
  regression check).
- App boots with `DJANGO_STORAGE_BACKEND=s3` and mocked/fake S3 storage.
- Storage backend selection is covered by a settings-level unit test, not
  requiring live Garage.

**Acceptance Criteria**

- [ ] Both backends boot successfully.
- [ ] Existing tests using `override_settings(MEDIA_ROOT=..., MEDIA_URL=...)`
      still pass or are adapted.
- [ ] `.env.example`/README document every new variable.

---

### Issue 48: DB-tracked media assets (`MediaAsset` model)

**Track:** Storage. **Depends on:** none (11 soft). **Blocks:** 12 (soft);
makes 13 and 16 implementable without storage scans. Added 2026-07-13
(follow-up code-review pass).

**Explanation**

Uploads are not tracked in the database. `_list_existing_uploads()` in
`geocontext/views.py` recursively walks `default_storage` and opens every
file with PIL (a 32KB read each, up to 100 files) on every media-library
`GET`. Against the filesystem that's merely slow; against S3/Garage it
becomes ~100 sequential object reads per request — a direct conflict with
this storage track. It also means issue 16's orphan cleanup has no DB side
to diff against, and issue 13's private-original/derivative mapping has no
natural place to live.

**Implementation**

- Add a `MediaAsset` model (UUIDv7 PK, unique storage path, mime, width,
  height, size, uploader FK, `TimeStampedModel` base) — in `core` or
  `geocontext`, whichever scoping survives the design discussion — written
  at upload time in `_store_validated_upload()`.
- The media-library view becomes a single DB query — no storage walking, no
  per-file PIL opens.
- Backfill management command that scans existing storage prefixes and
  creates missing rows (idempotent, `--dry-run` support).
- Issue 16's orphan cleanup later diffs storage keys against `MediaAsset`
  rows; issue 13 hangs derivative paths off the same model.

**Test Cases**

- An upload creates a `MediaAsset` row with correct metadata.
- The library endpoint returns results without opening storage files
  (query-count / storage-access assertion).
- Backfill command is idempotent and honors `--dry-run`.

**Acceptance Criteria**

- [ ] Upload surfaces write `MediaAsset` rows.
- [ ] Media library reads from the DB only.
- [ ] Backfill command exists and is tested.

---

### Issue 12: Route upload surfaces through S3 safely

**Track:** Storage. **Depends on:** 11, 48 (soft). **Blocks:** 13, 14.

**Explanation**

Makes the existing upload surfaces (GeoStory hero image, EditorJS
upload-by-file, EditorJS upload-by-url) actually write through the
now-configurable storage backend, with no application code coupled directly
to S3/boto.

**Implementation**

- Verify `GeoStory.hero_image` (`ImageField`) writes to S3 automatically via
  `STORAGES` — no application code change expected if `ImageField` is used
  correctly.
- Verify EditorJS upload-by-file writes through `default_storage.save(...)`.
- Verify EditorJS upload-by-url writes through the same path after issue
  10's SSRF hardening.
- Ensure returned URLs are stable and browser-usable; ensure
  `core/editorjs.py` can resolve stored image URLs back to storage keys when
  `MEDIA_PUBLIC_BASE_URL` is absolute.

**Test Cases**

- Hero image upload, mocked S3, returns a usable URL.
- EditorJS upload-by-file/by-url, mocked S3, returns the expected
  `{success: 1, file: {...}}` contract unchanged.
- No test or code path leaks S3 credentials or internal endpoint URLs in any
  API response.

**Acceptance Criteria**

- [ ] All three upload surfaces work against mocked S3 storage in tests.
- [ ] Response contracts for EditorJS endpoints are unchanged from the
      client's perspective.
- [ ] No direct boto/S3 client calls introduced in views, serializers, or
      admin — only via Django's storage abstraction.

---

### Issue 13: Private originals and public derivatives

**Track:** Storage. **Depends on:** 12. **Blocks:** nothing further
downstream.

**Explanation**

Closes the "public raw originals expose EXIF/metadata" issue from the
roadmap — originals become private by default, and public delivery uses
metadata-clean derivatives.

**Implementation**

- Decide whether v1 keeps originals private immediately or uses a
  transition window (recommend: private immediately, since pre-production
  means no existing public links to preserve).
- Add derivative URL helpers to serializers so the frontend never needs to
  parse storage paths from raw `/media/...` URLs.
- Update GeoStory and EditorJS response payloads to include a
  derivative/public image URL.

**Test Cases**

- An EXIF-bearing test image, uploaded and processed, produces a
  metadata-clean derivative.
- Public API payloads expose a derivative URL, not a private-original URL.
- Disabling raw original access does not break story/detail page rendering
  in a manual check.

**Acceptance Criteria**

- [ ] Public payloads provide a derivative/public image URL by default.
- [ ] Raw originals are private (bucket/prefix-level, not just
      "not linked").
- [ ] EXIF test proves derivative output is clean.

---

### Issue 14: Media migration to S3

**Track:** Storage. **Depends on:** 12. **Priority:** required before
switching any real environment to S3.

**Explanation**

Moves existing local-filesystem media into S3 without breaking stored
references — mostly relevant once there's real dev/staging content, but
worth having the tooling ready before the first switch-over.

**Implementation**

- Management command: scans known media prefixes, uploads missing files to
  S3, verifies object existence/size, supports `--dry-run`, emits a
  CSV/JSON report.
- Keep storage keys unchanged where possible (e.g. `geostories/<uuid>/hero/...`).
- Define a compatibility mapping if new prefixes (`media/originals/...`) are
  introduced.

**Test Cases**

- Dry-run report lists all expected files without writing anything.
- Live run verifies every copied file's existence and size against source.
- Command is idempotent — re-running after a partial failure doesn't
  duplicate or corrupt already-migrated files.

**Acceptance Criteria**

- [ ] Dry-run and live modes both implemented and tested.
- [ ] Report format documented.
- [ ] Rollback plan documented (per the original roadmap's rollback section).

---

### Issue 15 (deferred): Static files S3/CDN decision

**Track:** Storage. **Depends on:** 11. **Priority:** deferred — roadmap's
own recommendation is to keep static files on Nginx/`collectstatic` for the
first S3 release.

**Explanation**

Decide later whether static files also move to S3/CDN. Not needed for the
media-durability goal this epic is actually chasing.

**Acceptance Criteria**

- [ ] Explicit decision recorded (even if the decision is "not now").

---

### Issue 16 (deferred): Storage operations, backup, and lifecycle

**Track:** Storage. **Depends on:** 11. **Priority:** deferred.

**Explanation**

Operational hardening once S3 storage is live: backups, retention/lifecycle
rules, metrics, and an orphaned-file cleanup command.

**Implementation**

- Bucket backup/replication strategy (ops-level, likely outside Django).
- Retention/lifecycle rules for originals, derivatives, temporary uploads.
- Storage metrics (request errors, latency, bucket size, failed uploads) and
  OpenTelemetry spans around storage reads/writes, once the epic's
  observability work (issue 39) exists.
- Media cleanup management command for orphaned files, with a dry-run mode.

**Test Cases**

- Orphan cleanup dry-run correctly identifies files with no referencing DB
  row without deleting anything.

**Acceptance Criteria**

- [ ] Deferred — write these when picked up, once Phase 1 storage has been
      live long enough to know what "orphaned" actually looks like in
      practice.

---

## Phase 1 — Deferred (start anytime, no urgency)

### Issue 38 (deferred): i18n scope decision

**Track:** Report fix. **Depends on:** none. **Priority:** deferred —
product decision, not committed work.

**Explanation:** `USE_I18N=True` is set but nothing is wired up; `Event.language`
is metadata about what language an event is conducted in, not a translation
mechanism. Whether multi-language content is actually needed is a product
question this epic can't answer on its own.

**Acceptance Criteria:**
- [ ] Explicit decision recorded (scope and timing, or "not needed").
      If "yes," design the translation pattern starting with `GeoContext`
      before adding more content models — see the main review's
      Internationalization section for the two pattern options.

---

### Issue 39 (deferred): OpenTelemetry observability, phases 1-3

**Track:** Report fix. **Depends on:** none. **Priority:** deferred — do
after Phases 0-3 stabilize; purely additive, doesn't block or get blocked by
anything else in this epic.

**Explanation:** Logging alone can't answer "which dependency made this slow
or fail?" across Django, Postgres, GeoServer, and Keycloak.

**Implementation:** Phase 1 (HTTP/DB/outbound-call tracing + log
correlation), Phase 2 (metrics: request counts/latency, GeoServer
error rates, sync job outcomes), Phase 3 (manual domain spans around
publish/sync/catalog operations) — see the main review's Observability
section for the full package list, sampling rates, and settings shape.

**Test Cases:** traces appear in a local OTLP collector for a sample request
touching DB + GeoServer; sensitive fields (tokens, credentials, raw geometry)
are confirmed absent from span attributes.

**Acceptance Criteria:**
- [ ] Phase 1 shipped with production-safe sampling and no sensitive data in
      spans.
- [ ] Phases 2/3 scoped as explicit follow-ups, not required for this epic.

---

# Phase 2: Schema Prep & Geodata Provider Refactor

Everything here touches a file Phase 3 is about to touch. Land this phase
first to avoid two people editing the same file for unrelated reasons — see
"Parallelization Guidance" above for exactly which Phase 3 issue each of
these unblocks.

## Phase 2 — Schema Prep

### Issue 35: Switch existing models to UUIDv7

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required — land
**before** Phase 3's issue 3, since both touch migrations on `campaigns` and
`geodata_providers` models around the same time; doing this first avoids a
migration-graph conflict.

**Explanation:** Every model today uses `UUIDField(primary_key=True,
default=uuid.uuid4)`. Since there's no production data yet, switching the
default to UUIDv7 now is a migration-only change with nothing to backfill —
this is exactly the cheap window the review called out.

**Implementation:** add `uuid_extensions` (or equivalent) as a dependency;
change every model's PK `default=` from `uuid.uuid4` to
`uuid_extensions.uuid7`. No field type change, no serializer change, no API
change.

**Test Cases:** newly created rows across a sample of models (one per app)
get UUIDv7-shaped IDs (verify version bits); existing FK relationships and
lookups are unaffected.

**Acceptance Criteria:**
- [ ] All models' PK default is `uuid7`, not `uuid4`.
- [ ] No behavior change to any existing endpoint or test.

---

### Issue 29: Indexes on existing foreign keys and geometry fields

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required — land
before/with Phase 3's issue 3, since both touch `geodata_providers/models.py`
and `campaigns`-adjacent models around the same time.

**Explanation:** `geodata_providers` relies only on `unique_together` with no
explicit indexes on workspace/store/layer FKs; `events` has one indexed
field; `feedback` indexes some FKs but not geometry columns.

**Implementation:** add `Meta.indexes`/`db_index=True` on FKs used in hot
paths (catalog listing, sync lookups, admin filters); confirm GiST spatial
indexes exist where not already implied by field defaults.

**Test Cases:** `EXPLAIN`-based test or query-count assertion confirms an
index is used for the hot-path queries (e.g. listing layers by workspace).

**Acceptance Criteria:**
- [ ] Indexes added on the FK columns identified in the review.
- [ ] Spatial indexes confirmed present where expected.

---

## Phase 2 — Admin Destructive-Action Safety

### Issue 31: Destructive-action safety in admin

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required — land
before Phase 3's issue 6 (admin org-scoping), since both touch the same
admin files.

**Explanation:** Merges three related findings: admin actions need tests,
multi-step admin operations need `transaction.atomic()`, and — the sharpest
version of this problem — deleting a `Campaign` cascades to every `Event`,
`GeoStory`, and `GeoFeedback` under it with no recovery path, unlike
`Workspace`/`Store` deletion which already has a `usage_summary()`
pre-delete reference count.

**Implementation:**
- Add tests around existing destructive admin actions.
- Wrap multi-step admin operations in `transaction.atomic()`.
- Add a `usage_summary()`-style pre-delete safety check to `Campaign`
  deletion, matching the existing `Store`/`Workspace` pattern.
- Make an explicit decision on soft-delete: given the project is
  pre-production, decide now whether `Campaign`/`Event`/`GeoStory`/
  `GeoFeedback` need `deleted_at`-style soft delete, rather than retrofitting
  it onto live data later. If the decision is "no," document why so it isn't
  re-litigated per findings review.

**Test Cases:** deleting a `Campaign` with dependent Events/Stories/Feedback
shows a confirmation summary (or is blocked) the same way `Store` deletion
already does; a simulated failure mid-multi-step admin action leaves no
partial state (transaction rollback verified).

**Acceptance Criteria:**
- [ ] `Campaign` deletion has the same pre-delete safety pattern as
      `Store`/`Workspace`.
- [ ] Multi-step admin operations are wrapped in `transaction.atomic()`.
- [ ] Soft-delete decision is made and documented, whichever way it goes.

---

## Phase 2 — Geodata Provider Internal Refactor

This is the geodata_providers deep-dive from the review, broken into
independently-workable issues. **Land this block before Phase 3's issue 5**
(applying org permissions to the same viewsets) to avoid two people editing
`geodata_providers/admin.py` and `api/views.py` at the same time for
unrelated reasons.

### Issue 40: Collapse duplicated CRUD in `geoserver/client.py`

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required, before
Phase 3 issue 5.

**Explanation:** `create_workspace` and `create_store` (and the equivalent
layer/style methods) repeat the same five-step cycle — pre-check → POST →
validate → post-verify → return — once per resource type.

**Implementation:** extract one generic `_create_resource(existence_check,
create_fn, verify_fn)` helper (or a small `Resource` class per
workspace/store/layer/style) and have the four `create_*` methods call it.

**Test Cases:** existing `GeoServerClient` tests for all four resource types
still pass unchanged (behavior-preserving refactor); a new test on the shared
helper covers the five-step cycle once instead of four times.

**Acceptance Criteria:**
- [ ] No behavior change to any `create_*`/`delete_*`/`verify_*` method.
- [ ] Duplicated cycle exists in exactly one place.

---

### Issue 41: Split `sync_service.py` by resource

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required, before
Phase 3 issue 5.

**Explanation:** `GeoServerSyncService` is a 934-line, 27-method god class;
`sync_layers_for_workspace` alone handles seven distinct concerns in one
122-line method.

**Implementation:** introduce `WorkspaceSyncer`, `StoreSyncer`,
`LayerSyncer`, `StyleSyncer`, each owning one resource type's sync logic;
`GeoServerSyncService` becomes a thin coordinator calling all four.

**Test Cases:** each syncer is unit-testable in isolation with a mocked
GeoServer client; existing integration-level sync tests still pass
end-to-end.

**Acceptance Criteria:**
- [ ] No single syncer method mixes more than one or two of: fetch, diff,
      persist, error-handling.
- [ ] Existing sync behavior unchanged end-to-end.

---

### Issue 42: Enforce the service layer as the only boundary

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** 40, 41. **Priority:** required,
before Phase 3 issue 5.

**Explanation:** `api/views.py` imports `GeoServerClient` directly (unused)
and does an inline `sync_service` import inside a method body instead of
going through a command; `admin.py`'s `_run_workspace_sync` helper
reimplements sync orchestration instead of calling one service method.

**Implementation:** remove the direct `GeoServerClient` import from
`api/views.py`; move/replace the inline `sync_service` import with a proper
command call; replace `admin.py`'s `_run_workspace_sync` with a call to the
same service method the views use.

**Test Cases:** a static check (or a simple grep-based test) confirms no
view or admin module imports from `geoserver.client` or `sync_service`
directly; existing admin sync action tests still pass.

**Acceptance Criteria:**
- [ ] No view or admin method imports `geoserver.client`/`sync_service`
      directly.
- [ ] "Sync a workspace" logic exists in exactly one place.

---

### Issue 43: Typed results and a real exception hierarchy

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required, before
Phase 3 issue 5. (Merges three report findings: "API response shapes are
inconsistent," "exception handling is too broad," and "repeated
GeoServer-first orchestration dicts" — all three describe the same
untyped-dict/broad-except pattern.)

**Explanation:** Every layer passes around untyped dicts unpacked
differently by every caller; a real exception hierarchy
(`GeodataEngineError`, `PublishingError`, `GeoServerConnectionError`, etc.)
already exists but is bypassed by roughly 78 bare `except Exception` blocks.

**Implementation:** introduce an `OperationResult(success, data, error)` (or
per-operation dataclasses) to replace free-form dicts across
`geoserver/client.py`, `sync_service.py`, and the command services; swap the
`except Exception` blocks for the specific exceptions already defined,
letting genuinely unexpected errors propagate as 500s instead of being
swallowed into a dict.

**Test Cases:** each replaced `except Exception` site has a test proving the
specific exception type is now raised for its known failure mode (e.g. a
connection failure raises `GeoServerConnectionError`, not a generic dict);
callers that used to do `result.get('success')` now use the typed result's
attribute and are covered by the existing call-site tests.

**Acceptance Criteria:**
- [ ] Free-form dict returns are replaced with a typed result across the
      three files listed.
- [ ] `except Exception` count in `geodata_providers` is reduced to the
      integration-boundary cases the review calls acceptable (documented
      inline where kept).

---

### Issue 44: Delete the Martin/PG_Tileserv placeholder abstraction

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** none. **Priority:** required, before
Phase 3 issue 5.

**Explanation:** `engine_factory.py` builds real abstraction machinery
(`MartinClientPlaceholder`, `MartinSyncPlaceholder`) for two engines that
don't work, with no `ABC`/`Protocol` enforcing any contract.

**Implementation:** drop both placeholder classes and their factory
branches; raise an explicit `UnsupportedEngineError` at the one call site
that needs it. Hide `MARTIN`/`PG_TILESERV` from admin/API choices unless a
real capability model replaces this later.

**Test Cases:** selecting an unsupported engine type fails predictably with
`UnsupportedEngineError`, tested explicitly; existing GeoServer-path tests
are unaffected.

**Acceptance Criteria:**
- [ ] Placeholder classes removed.
- [ ] Unsupported engine selection fails with a clear, tested error.

---

### Issue 45: Thin `LayerViewSet.update` and similar fat views

**Status: ✅ Done.**

**Track:** Report fix. **Depends on:** 42, 43. **Priority:** required,
before Phase 3 issue 5.

**Explanation:** `LayerViewSet.update` is 50 lines doing field-allowlist
filtering, a publishing-state check, a service call, manual
exception-to-Response translation, a DB refresh, and conditional
serialization, all inline.

**Implementation:** introduce `LayerUpdateService.apply(layer, fields,
user)` encapsulating all of that branching; the view becomes: validate input
→ call the service → serialize the result.

**Test Cases:** existing `LayerViewSet.update` API tests pass unchanged
(behavior-preserving); a new unit test covers `LayerUpdateService` directly
without going through the API layer.

**Acceptance Criteria:**
- [ ] `LayerViewSet.update` contains no branching beyond input validation and
      response serialization.
- [ ] No behavior change to the existing API contract.

---

# Phase 3: Organizations & Authorization

The epic's main feature. Built once Phase 2 has settled the files these
issues also touch. Internal order: issue 1 first, then 2/3/7 in parallel,
then 4, then 5/6 in parallel, then 8/9 whenever.

### Issue 1: Organization & Membership data models

**Track:** Org/Auth. **Depends on:** none. **Blocks:** 2, 3, 4, 6, 7, 8, 9.

**Explanation**

Nothing in the codebase currently models "which users belong to which
organization, with what role." This issue creates that foundation only — no
permission enforcement, no Keycloak sync, no FKs on existing resources yet.
It should be safe to merge on its own with zero behavior change to any
existing endpoint.

**Implementation**

- New app `tosca_api/apps/organizations/` (models, admin, migrations,
  tests), following the existing app layout convention (see `campaigns/`).
- `Organization` model: `id` (UUID PK — use `uuid_extensions.uuid7()` per
  this repo's PK guidance for new models, not `uuid.uuid4`), `name`,
  `slug` (unique), `keycloak_group_id` (`CharField`, unique, nullable —
  populated once issue 2 exists), `is_active` (bool, default True),
  `created_at`/`updated_at` via the existing `TimeStampedModel` base.
- `Membership` model: `id` (UUIDv7 PK), `user` FK to the user model
  (`on_delete=CASCADE`), `organization` FK (`on_delete=CASCADE`), `role`
  (`CharField` with choices: `OWNER`, `ADMIN`, `EDITOR`, `VIEWER` — mirrors
  the existing `IsAdmin`/`IsEditor`/`IsViewer` naming in
  `authentication/permissions.py`), `created_at`/`updated_at`. Add a
  `UniqueConstraint(user, organization)` — one membership row per user per
  org.
- Register both in Django admin (`OrganizationAdmin`, `MembershipAdmin` with
  an inline `Membership` editor on `OrganizationAdmin`) so organizations and
  memberships can be managed manually before issue 2's sync exists — this is
  what makes local dev/testing of issues 3-7 possible without a working
  Keycloak connection.
- Migration: since pre-production, no backfill needed. If dev fixtures/seed
  data exist, add one "Default Organization" via a data migration or
  management command, not a schema-level default.

**Test Cases**

- `Organization` and `Membership` can be created via the ORM and via Django
  admin.
- `UniqueConstraint(user, organization)` rejects a duplicate membership row.
- Deleting a `User` cascades to their `Membership` rows (does not orphan
  them); deleting an `Organization` cascades to its `Membership` rows.
- `Organization.slug` uniqueness is enforced at the DB level.

**Acceptance Criteria**

- [ ] `organizations` app exists with `Organization` and `Membership` models,
      migrations applied cleanly on a fresh DB.
- [ ] Both models manageable via Django admin.
- [ ] No existing app, view, or test is modified or broken by this issue.
- [ ] `uv run ruff check` and the full test suite pass.

---

### Issue 2: Keycloak group/role → Membership sync

**Track:** Org/Auth. **Depends on:** 1. **Blocks:** nothing (informs 4/5/6
in practice, but they can be built/tested against manually-created
`Membership` rows without this).

**Explanation**

Extends the existing `role_sync.py` pattern (which already maps Keycloak
realm roles to `is_staff`/`is_superuser` on login) to also materialize
`Organization` membership and per-org role from Keycloak groups. This is the
"Keycloak tells us who/what groups, Django decides what they can do" half —
this issue only produces `Membership` rows; it does not decide what those
rows are allowed to do (that's issue 4).

**Design decision — now settled (see "Decisions Already Made" + ACL doc §1).**
The earlier open "(A) roles vs (B) groups" choice is closed: **native Keycloak
Organizations is primary; plain `Group` + realm roles
(`/org-<slug>/{readers,writers,admins}` → `ROLE_ORG_<slug>_<level>`) is the
fallback** if the verification spike shows native Organizations don't behave as
documented on the deployed version. Django and the projection are identical
either way — this sync only ever consumes `ROLE_ORG_*` roles from the token,
regardless of how Keycloak produces them.

**Direction note (see "Decisions Already Made"):** Django `Membership` is the
canonical authorization source; the org-role that GeoServer reads is a
*projection* of it. So this sync is not purely one-way Keycloak → Django —
identity/authentication flows Keycloak → Django, but a user's org `access_level`
is decided in Django and **projected** to the Keycloak `ROLE_ORG_<slug>_<level>`
role that lands in the JWT. The projection must run whenever a `Membership` role
changes, or the API plane and the GeoServer plane diverge. The role-name mapping
(`VIEWER`/`EDITOR`/`ADMIN`/`OWNER` ↔ `READER`/`WRITER` ↔ `.r`/`.w`) is the table
in ACL doc §5.1 — implement against that table, not an ad-hoc mapping.

**Implementation**

- New function in `authentication/role_sync.py` (or a sibling module
  `org_sync.py` if it gets large), called from the same login hook that
  already calls `sync_user_permissions_from_roles()` (`backends.py`).
- Parse the user's Keycloak groups/roles from the token/userinfo claims
  according to the convention chosen above.
- For each org the user belongs to in Keycloak: `get_or_create` the
  `Organization` by `keycloak_group_id` (do **not** silently create new
  organizations from arbitrary Keycloak groups unless a config flag
  `KEYCLOAK_AUTO_CREATE_ORGANIZATIONS` explicitly allows it — default off,
  so orgs are provisioned deliberately in Django admin first, then linked).
  `get_or_create`/`update` the `Membership` row with the resolved role.
- Remove `Membership` rows for orgs the user is no longer part of in
  Keycloak (so access revocation in Keycloak actually revokes Django access
  on next login).

**Test Cases**

- A user logging in with a Keycloak group claim matching an existing
  `Organization.keycloak_group_id` gets a `Membership` row created with the
  correct role.
- A user logging in with a Keycloak group that has no matching `Organization`
  does not create one when `KEYCLOAK_AUTO_CREATE_ORGANIZATIONS=False`
  (default), and does create one when the flag is `True`.
- A user who loses a Keycloak group between logins has the corresponding
  `Membership` row removed on next login.
- Role changes in Keycloak (e.g. editor → admin) update the existing
  `Membership.role` rather than creating a duplicate row.
- Sync failure (malformed claims, missing group) logs and does not raise —
  login should not hard-fail because of a sync data issue.

**Acceptance Criteria**

- [ ] Login syncs Keycloak group membership into Django `Membership` rows.
- [ ] Revoked Keycloak group membership is reflected in Django within one
      login cycle.
- [ ] `KEYCLOAK_AUTO_CREATE_ORGANIZATIONS` setting documented in `.env.example`.
- [ ] Sync logic covered by tests using mocked Keycloak claims (no live
      Keycloak needed for the unit suite).

---

### Issue 3: `organization` FK on Campaign & Workspace

**Track:** Org/Auth. **Depends on:** 1, 29, 35 (soft). **Blocks:** 4, 5, 6.

**Explanation**

This is the actual tenancy boundary: two FK additions that make every
Campaign and every geodata Workspace belong to exactly one Organization.
Everything downstream (Event, GeoStory, GeoFeedback via `campaign`; Store,
Layer, Style via `workspace`) inherits scoping for free through their
existing foreign keys — no other model changes are needed.

**Implementation**

- `tosca_api/apps/campaigns/models.py`: add
  `organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT)`
  to `Campaign`. Use `PROTECT`, not `CASCADE` — deleting an org should not
  silently cascade-delete every campaign (matches this repo's existing use of
  `PROTECT` for audit-relevant FKs like `created_by`).
- `tosca_api/apps/geodata_providers/models.py`: add the same
  `organization` FK (`PROTECT`) to `Workspace`.
- Migration: make it required (`NOT NULL`) directly, per the epic's "cheap
  now" decision — no nullable transition period. If local dev fixtures exist
  without an org, create one default `Organization` in a data migration and
  point existing rows at it.
- Add `Meta.indexes` on the new FK columns (ties into issue 29, which
  should already have landed — this is the same "index the FKs used in hot
  filter paths" pattern applied to the newest FK).
- **`Workspace.visibility` (POC scope — see "Decisions Already Made" + ACL doc
  §5a):** add `visibility = CharField(choices=[PRIVATE, PUBLIC], default=PRIVATE)`
  to `Workspace`. **PRIVATE | PUBLIC only** for the POC — there is no `SHARED`
  choice and **no `shared_with` M2M**. Cross-org sharing is done by creating a
  separate shared `Workspace` (owned by the sharing org), not by a field on the
  workspace; layer-level sharing is deferred to v2. `Campaign` does not get a
  `visibility` field here — this bullet is Workspace-only (Campaign visibility,
  if needed, is a separate later decision). The GeoServer ACL projection of
  `visibility` (PRIVATE → org readers, PUBLIC → anonymous read) is Issue 5's
  concern, not this migration's.

**Test Cases**

- Creating a `Campaign` or `Workspace` without an `organization` fails
  validation/DB constraint.
- Attempting to delete an `Organization` that still has Campaigns or
  Workspaces raises `ProtectedError`, not a silent cascade.
- Existing `Campaign`/`Workspace` admin pages show the new field.
- A query filtering `Event.objects.filter(campaign__organization=org)` and
  `Layer.objects.filter(workspace__organization=org)` returns the expected
  scoped set.

**Acceptance Criteria**

- [ ] `Campaign.organization` and `Workspace.organization` exist, required,
      `PROTECT` on delete.
- [ ] Migration runs cleanly on a fresh DB and on the dev DB with existing
      fixture data (via a default org).
- [ ] New indexes exist on both FK columns.
- [ ] No existing test breaks; tests that create `Campaign`/`Workspace`
      fixtures are updated to pass an `organization`.

---

### Issue 4: Org-scoped DRF permission classes

**Track:** Org/Auth. **Depends on:** 1, 3. **Blocks:** 5.

**Explanation**

This is where "Django decides what a role can do" actually gets enforced.
Replaces the current all-staff-see-everything model with permission classes
that check `Membership` for the requesting user against the specific
resource's organization.

**Implementation**

- New module `tosca_api/apps/organizations/permissions.py`.
- `IsOrganizationMember`: object-level permission — `has_object_permission`
  resolves the object's organization (`obj.organization` for
  Campaign/Workspace, `obj.campaign.organization` /
  `obj.workspace.organization` for children) and checks a `Membership` row
  exists for `request.user`.
- `HasOrganizationRole(*roles)`: parameterized permission factory — e.g.
  `HasOrganizationRole("OWNER", "ADMIN")` for destructive actions,
  `HasOrganizationRole("EDITOR", "ADMIN", "OWNER")` for writes, any role for
  reads.
- Keep `IsSuperAdmin` from `authentication/permissions.py` as an automatic
  bypass inside these new classes (superusers always pass), rather than
  duplicating superuser-check logic at every call site.
- Do not query Keycloak here — everything reads `Membership` only.

**Test Cases**

- A user with `VIEWER` membership can read but not write a resource in their
  org.
- A user with no `Membership` row for an org gets `403` on any action against
  that org's resources.
- A user with `ADMIN` membership in Org A cannot act on Org B's resources
  even if they also happen to be `is_staff`.
- A superuser bypasses org checks entirely regardless of `Membership`.
- Permission checks work correctly for both direct-FK resources (Campaign,
  Workspace) and indirect ones (Event via `campaign`, Layer via `workspace`).

**Acceptance Criteria**

- [ ] `IsOrganizationMember` and `HasOrganizationRole` exist, unit-tested in
      isolation (no full API round-trip needed for this issue).
- [ ] Superuser bypass verified.
- [ ] Permission resolution works for both directly-scoped and
      indirectly-scoped (via FK) models.

---

### Issue 5: Apply org-scoped permissions to API viewsets

**Track:** Org/Auth. **Depends on:** 4, 40-45 (soft). **Blocks:** nothing.

**Explanation**

Wires the permission classes from issue 4 into the actual DRF viewsets for
campaigns, events, geostories, feedback, and geodata_providers
(workspace/store/layer/style). This is the issue that changes real API
behavior — everything before it was additive/inert.

**Implementation**

- Update `permission_classes` on each relevant `ViewSet` (`CampaignViewSet`,
  `EventViewSet`, `GeoStoryViewSet`, `GeoFeedbackViewSet`,
  `WorkspaceViewSet`, `StoreViewSet`, `LayerViewSet`, `StyleViewSet`) to use
  `IsOrganizationMember`/`HasOrganizationRole` in place of (or alongside) the
  current `IsAuthenticated`/`IsAdminUser`.
- Update `get_queryset()` on list endpoints to filter by the user's
  `Membership` set (`Campaign.objects.filter(organization__membership__user=request.user)`
  or equivalent), so list views don't leak other orgs' resource *existence*
  even before object-level permission is checked.
- Public read-only endpoints (public catalog, published events) are
  unaffected — org scoping applies to authenticated staff management
  endpoints, not public consumer-facing reads.
- While these files are open (2026-07-13 note): normalize
  `GeodataEngineViewSet`'s `self._last_engine`/`self._last_sync_result`
  handshake between `perform_create` and `create` — it works today because
  DRF instantiates a viewset per request, but it's fragile style. This issue
  also supersedes issue 47's interim admin-only write restriction on the
  engines API.

**Test Cases**

- List endpoints only return resources from orgs the requesting user belongs
  to (superuser sees all).
- Detail/update/delete on a resource outside the user's org(s) returns `403`
  or `404` (pick one convention and apply it consistently — recommend `404`
  to avoid confirming the resource's existence to non-members).
- Public catalog/published endpoints are unaffected by this change —
  regression test against existing public API contracts.
- A user in two organizations sees the union of both orgs' resources on list
  endpoints.

**Acceptance Criteria**

- [ ] All listed viewsets enforce org scoping on read and write.
- [ ] Public/unauthenticated endpoints unchanged (explicit regression test).
- [ ] Existing API tests updated to create memberships as needed; no
      unrelated test breakage.

---

### Issue 6: Org-scoped filtering in Django admin

**Track:** Org/Auth. **Depends on:** 1, 3, 31 (soft). **Blocks:** nothing.

**Explanation**

Django admin's own permission system (`has_view_permission`,
`get_queryset`) is separate from DRF's — this issue does the admin-side
equivalent of issue 5, independently, since it doesn't need issue 4's DRF
permission classes at all.

**Implementation**

- Override `get_queryset()` on `CampaignAdmin`, `WorkspaceAdmin`, and any
  `ModelAdmin` for their children, filtering by the logged-in staff user's
  `Membership` set unless `request.user.is_superuser`.
- Override `has_change_permission`/`has_delete_permission` similarly for
  object-level checks in the admin change form.
- This directly extends the report's existing "Permissions and object-level
  access" finding and ties into the "Admin hardening" recommendation
  (destructive actions need scoping, not just tests).

**Test Cases**

- A non-superuser staff user's admin changelist for Campaign/Workspace only
  shows their org(s)' rows.
- Attempting to open a change form for another org's resource via direct URL
  is denied.
- Superuser admin behavior is unchanged (sees everything, as today).

**Acceptance Criteria**

- [ ] Admin changelists and change forms are org-scoped for non-superusers.
- [ ] Superuser behavior unchanged.
- [ ] Covered by admin-specific tests (Django test client hitting admin URLs
      as different users).

---

### Issue 7: Organization & Membership management surface

**Track:** Org/Auth. **Depends on:** 1. **Blocks:** nothing.

**Explanation**

Someone needs to create organizations and manage membership day-to-day.
Issue 1 gives bare Django admin CRUD; this issue decides and builds the real
surface — likely just hardened Django admin (add/remove members, assign
roles), possibly a small API if the frontend needs to show "my
organizations" or manage members without going through `/admin/`.

**Implementation**

- Restrict `OrganizationAdmin`/`MembershipAdmin` create/delete to
  superusers only (regular org `OWNER`/`ADMIN` roles manage their own org's
  membership through a scoped path, not raw model admin, to avoid one org
  editing another's `keycloak_group_id`).
- If the frontend needs it: a small `OrganizationViewSet`/`MembershipViewSet`
  read (list "my organizations") and write (org `OWNER`/`ADMIN` can add/remove
  members within their own org) using the permission classes from issue 4.
  This part can be deferred until a frontend consumer actually needs it —
  don't build speculative API surface ahead of a real caller.

**Test Cases**

- A superuser can create an `Organization` and assign an initial `OWNER`
  membership.
- An org `ADMIN` can add/remove members within their own org, not another's.
- An org `EDITOR`/`VIEWER` cannot modify membership.

**Acceptance Criteria**

- [ ] Organization/Membership CRUD is usable end-to-end by a superuser via
      Django admin.
- [ ] Org-scoped membership management by non-superusers is either shipped
      (if a frontend need exists now) or explicitly logged as a follow-up
      with the reason it was deferred.

---

### Issue 8 (deferred): Audit logging for admin/permission-sensitive actions

**Track:** Org/Auth. **Depends on:** 1. **Priority:** deferred — not
required for epic completion.

**Explanation**

No audit trail exists today for who changed what (flagged in
`application-review-report.md`'s Production Readiness section). Tying it to
this epic makes sense because `Organization`/`Membership` changes and
cascading-delete risk (e.g. deleting a `Campaign`) are exactly the actions
worth auditing first.

**Implementation**

- Add `django-simple-history` (or equivalent) to `pyproject.toml`.
- Attach `HistoricalRecords()` to `Organization`, `Membership`, `Campaign`,
  and `Workspace` at minimum.
- Surface history in the relevant `ModelAdmin` via `SimpleHistoryAdmin`.

**Test Cases**

- Changing a `Membership.role` creates a historical record with the actor
  and timestamp.
- Deleting a `Campaign` leaves a historical record even though the row
  itself is gone.

**Acceptance Criteria**

- [ ] History exists and is visible in admin for the four models listed.
- [ ] No measurable write-latency regression on high-frequency models.

---

### Issue 9 (deferred): Service-account / machine-to-machine auth

**Track:** Org/Auth. **Depends on:** 1, 4. **Priority:** deferred — not
required for epic completion.

**Explanation**

Non-interactive clients (scheduled jobs, external integrations) currently
have no distinct credential type from interactive user login. Deferred per
explicit product decision — build only when a real non-interactive caller
exists.

**Implementation**

- Likely shape: Keycloak client-credentials grant issuing a service-account
  token, or a Django-issued API key model scoped to one `Organization` with
  a fixed role (recommend `EDITOR` at most — no service account should get
  `OWNER` by default).
- Reuse the permission classes from issue 4 unchanged — a service account is
  just another `Membership`-bearing principal.

**Test Cases**

- A service-account credential scoped to Org A cannot act on Org B.
- Revoking a service-account credential immediately blocks further use (no
  caching that outlives revocation).

**Acceptance Criteria**

- [ ] Deferred — write these when the issue is actually picked up, once a
      concrete non-interactive use case exists.

---

### Issue 51: Keycloak role projection + GeoServer Data Security ACL sync

**Track:** Org/Auth. **Depends on:** 1, 3. **Blocks:** nothing (but the
GeoServer half of the ACL model in `epic-11-User_ACL_decision.md` is not
enforced without it). Added 2026-08-10 (second grilling round — this closed a
real scope gap: Issues 1-9 covered only the Django API/admin enforcement plane;
the GeoServer plane and the Keycloak role projection had no issue).

**Explanation**

Issues 4-6 enforce authorization on the **Django** plane (API + admin), reading
`Membership`. But the ACL decision has a **second enforcement plane — GeoServer**
— that reads `ROLE_ORG_*` roles from the JWT, and those roles are a *projection*
of Django `Membership` (see "Decisions Already Made": canonical source / synced
projection). Nothing in Issues 1-9 produces that projection or the GeoServer
Data Security config. This issue is the two sync services the ACL doc names:

- **`KeycloakSyncService` (Membership role → Keycloak role, write direction).**
  When a `Membership.role` changes in Django, project it to the corresponding
  `ROLE_ORG_<slug>_<level>` assignment in Keycloak (native Organizations if the
  spike passed, else Group+role fallback — ACL doc §1), using the role-mapping
  table in ACL doc §5.1. This is the inverse of Issue 2's read-direction sync;
  together they keep the two planes from diverging.
- **`GeoServerSecuritySyncService` (Workspace state → GeoServer Data Security).**
  When `Workspace.organization` / `visibility` changes, or a shared workspace is
  created/deleted (ACL doc §9), synchronously (not via a periodic job) push the
  derived Data Security rules: `<ws>.*.r` / `<ws>.*.w` per §6/§6d/§9 —
  PRIVATE → org readers, PUBLIC → `<ws>.*.r = *` (anonymous), sharing → the
  separate shared workspace's ACL granting the partner org reader; write always
  stays `ROLE_ORG_<owner>_WRITER`. A periodic reconcile may be added as a safety
  net **on top of**, never instead of, the synchronous push (ACL doc §8).

**Note on revocation scope (ACL doc §6c):** this sync manages *ACL rule* state
only. User-org membership revocation is bounded by access-token TTL, not by this
push — that policy (short TTL, optional back-channel logout) is a settings/
Keycloak-config concern, tracked in the "Decisions Already Made" revocation
bullet, not code in this issue.

**Test Cases**

- Changing a `Membership.role` (VIEWER → EDITOR) results in the mapped Keycloak
  role assignment being added/removed (mocked Keycloak admin client).
- Creating a PRIVATE Workspace writes `<ws>.*.r = ROLE_ORG_<slug>_READER`,
  `<ws>.*.w = ROLE_ORG_<slug>_WRITER`.
- Flipping a Workspace to PUBLIC writes `<ws>.*.r = *` while `<ws>.*.w` stays
  owner-org locked; flipping back restores org-reader-only.
- Creating a shared workspace writes an ACL granting both owner and partner org
  reader, owner-only writer; deleting it removes partner access synchronously.
- The push runs inside the same transaction/request as the triggering change,
  not on a delayed job (assert synchronous invocation).

**Acceptance Criteria**

- [ ] `Membership.role` changes project to Keycloak roles per the §5.1 table.
- [ ] Workspace visibility/sharing changes push GeoServer Data Security rules
      synchronously, covering PRIVATE, PUBLIC (anonymous read), and shared
      workspace, with write always owner-org locked.
- [ ] Optional periodic reconcile, if added, is documented as a safety net that
      does not replace the synchronous push.
- [ ] Sync logic unit-tested with mocked Keycloak/GeoServer clients (no live
      services in the unit suite).

---

# Phase 4: Pre-Launch Cleanup

Deferred by explicit choice, not by dependency: the project is still in
active development, so CI/CD setup (18) and the two production-traffic
hardening issues (26, 28) add no value yet and are pushed here to keep early
development frictionless. None of the three depend on each other or block
issue 37 — pick them up in any order, any time before the first production
release. Issue 37 (migration squash) is the one item here that's genuinely
last by hard dependency, not just by choice.

### Issue 18: CI/CD, dependency scanning, and mypy baseline

**Track:** Report fix. **Depends on:** none. **Priority:** required,
deferred — moved out of Phase 0 since the project is still in active
development and nothing else in this epic depends on it; implement whenever
before the first production release, or earlier if you want the safety net
sooner.

**Explanation:** Merges three report findings that are really one "set up
tooling" issue: no CI pipeline exists at all, no dependency vulnerability
scanning exists, and `mypy` is a dependency that runs nowhere.

**Review note (2026-07-13):** recommend pulling this forward to land before
Phase 3 starts. The deferral rationale ("no production traffic yet") holds
for issues 26/28, but CI protects *development*, not production — and
Phase 3 is the largest behavior-changing block in the epic, exactly when a
ruff + pytest merge gate pays for itself. A minimal workflow with a PostGIS
service container is roughly a half day.

**Implementation:**
- GitHub Actions workflow running `uv run ruff check tosca_api` and the
  pytest suite (with a PostGIS service container) on every PR.
- Add `pip-audit` as a CI step; enable Dependabot or Renovate.
- Add a minimal, permissive `[tool.mypy]` config pointed at `tosca_api`,
  excluding vendored code (see issue 24); run non-blocking in CI first.

**Test Cases:** a PR with a ruff violation fails CI; a PR with a failing test
fails CI; a PR is unaffected by mypy findings initially (non-blocking).

**Acceptance Criteria:**
- [ ] CI runs ruff + pytest on every PR and blocks merge on failure.
- [ ] `pip-audit` runs in CI (report-only or blocking, your call).
- [ ] Dependabot/Renovate configured.
- [ ] `mypy` runs in CI non-blocking with a minimal config.

---

### Issue 26: IP allowlist / VPN gate for `/admin/`

**Track:** Report fix. **Depends on:** none. **Priority:** required,
deferred — production blocker per the review, but moot until there's real
production traffic to gate.

**Explanation:** `/admin/` is proxied straight through by nginx with no
protection beyond a login form, despite the admin's destructive power.

**Implementation:** add an IP allowlist at the nginx layer
(`docker/nginx/prod.conf`) or require VPN-only network access to the admin
path.

**Test Cases:** a request to `/admin/` from a disallowed IP is rejected at
the proxy layer (integration/manual check, not a Django unit test).

**Acceptance Criteria:**
- [ ] `/admin/` is unreachable from arbitrary public IPs in production.
- [ ] Documented in ops/README how to add a new allowed IP/VPN range.

---

### Issue 28: Rate limiting for feedback and catalog endpoints

**Track:** Report fix. **Depends on:** none. **Priority:** required,
deferred. (Media upload throttling is already covered by Phase 1's storage
issue 10 — this issue covers the non-storage-related endpoints the report
also flagged.)

**Explanation:** Anonymous feedback submission has no rate limit; expensive
catalog inspection endpoints may need one depending on exposure. Not urgent
without real public traffic to abuse these endpoints. Note (2026-07-13):
issue 46 fixes the globally-inert throttle configuration
(`DEFAULT_THROTTLE_RATES` missing entirely); this issue remains the place
for the per-endpoint anon/scoped rates once that global fix exists.

**Implementation:** configure DRF throttle rates in settings; add scoped
throttles for anonymous feedback submission and expensive geodata/catalog
inspection endpoints.

**Test Cases:** repeated anonymous feedback submissions past the configured
rate return `429`; catalog inspection throttle triggers under load-test-style
repeated calls.

**Acceptance Criteria:**
- [ ] Anonymous feedback submission is throttled.
- [ ] Expensive catalog endpoints are throttled.

---

### Issue 37: Squash migrations before first production release

**Track:** Report fix. **Depends on:** 3 (Phase 3), 35 (Phase 2). **Priority:**
required, but scheduled **last** — after all other schema-touching issues in
this epic have landed, right before the first production release.

**Explanation:** Migration volume is small (34 files today) — squashing into
a clean initial set is realistic now and avoids ever needing to do it after
production data exists.

**Implementation:** run `squashmigrations` per app once issue 3 (org FKs) and
issue 35 (UUIDv7) are both merged, along with any other schema issues in this
epic. Verify the squashed migrations produce an identical schema to the
unsquashed history on a fresh DB.

**Test Cases:** a fresh DB migrated with the squashed migrations has an
identical schema (via `manage.py makemigrations --check` or a schema diff)
to one migrated with the full pre-squash history.

**Acceptance Criteria:**
- [ ] Squashed migrations produce an identical schema to the original
      history.
- [ ] Old migration files are removed/archived per Django's squash workflow.

---

## Open Sub-Decisions To Resolve During Implementation

These don't block starting the epic, but need an answer before the specific
issue that depends on them is finished:

- **Issue 2**: exact Keycloak group/role naming convention for
  organizations (option A vs B above) — depends on how the Keycloak realm is
  actually administered, which this repo doesn't currently encode.
- **Issue 5**: `403` vs `404` convention for cross-org access attempts —
  pick one and apply consistently across all viewsets in that issue.
- **Issue 7**: whether a frontend-facing Organization/Membership API is
  needed now or can wait for a concrete consumer.
- **Issue 13**: immediate-private vs. transition-window for existing public
  original URLs — moot if there's no real production content yet, worth
  confirming before implementing.
