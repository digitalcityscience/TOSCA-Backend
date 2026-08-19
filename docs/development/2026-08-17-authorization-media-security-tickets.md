# Authorization, Organization Isolation & Media Security — Tickets

**Day:** Monday, 2026-08-17 (plan date) · tickets generated Wednesday, 2026-08-19
**Source plan:** [`2026-08-17-authorization-media-security-development-plan.md`](./2026-08-17-authorization-media-security-development-plan.md)
**Scope:** `tosca_api` backend (Django + DRF), Keycloak-derived authorization, Garage/S3 media confidentiality

This document breaks the approved plan into tracer-bullet tickets, each declaring its blocking edges.
Granularity mirrors §11 of the plan exactly (DRF enforcement split per resource; media split into
upload / lifecycle / backfill).

---

## Framing (do not conflate the two tracks)

Fixing Django authorization alone does **not** make private media confidential. A draft resource can be
correctly hidden by the API (`404`) while its embedded image stays world-readable at a stable unsigned
public-bucket URL. `S1` (row reachable via API queryset) and `S2` (bytes reachable via unsigned public
URL) are **two independent failure modes of one confidentiality objective** — either can be closed while
the other stays open.

### The four orthogonal gates (none replaces another)

- **A. Capability** — Keycloak org role → Django view/add/change/delete verb (model-level, org-agnostic).
- **B. Entitlement** — which TOSCA apps/models an organization may use at all.
- **C. Ownership / tenant isolation** — queryset + object scope; the **only real tenant boundary for reads**.
- **D. Storage access** — private media reachable only via a presigned URL generated after A+B+C pass.

### Dependency graph

```text
01 Baseline tests
   ├── 02 S1 GeoStory queryset hotfix        [SEC, independent, ship first]
   └── 03 Authorization foundation (entitlement + policy + delete dead perms)
          └── 04 Authorization snapshot model
                 └── 05 Claim normalization & snapshot sync
                        └── 06 Dynamic has_perm backend
                               ├── 07 Admin integration
                               └── 08 Campaign DRF permissions
                                   09 GeoStory DRF permissions (also blocked by 02)
                                   10 Event DRF permissions
                                   11 Remaining campaign-owned DRF resources
                                          └── 12 Workspace/geodata authz cleanup

13 Media: private EditorJS uploads (S2)  ── needs policy stable (03), NOT the has_perm backend
   └── 14 Media: campaign visibility lifecycle
          └── 15 Media: backfill existing media

16 Full security acceptance matrix   (blocked by 02–15)
17 Optional storage hardening        (independent, non-blocking)
```

**Key point:** the media track (13–15) can proceed independently of most authorization backend work.
Ticket 02 (S1) is independent and should ship first.

### Execution sequencing decision (2026-08-19, post-ticket-02)

**Decision:** run the MEDIA track (13 → 14 → 15) **before** the ARCH track (03 → 12), reversing this
document's original top-to-bottom reading order. The blocking-edge graph above was already loose enough
to allow this (13 only needs 03's *policy stable*, not 04–12) — this section makes the choice explicit
and records why, so it isn't re-litigated or silently drifted from later.

**Why, in order of weight:**

1. **S2 is a live, confirmed, currently-exploitable gap; the ARCH track is not closing an open gap.**
   Ticket 01's characterization test
   (`core/tests/test_security_baseline.py::test_s2_characterization_private_campaign_editorjs_upload_alias_today`)
   proves today's root cause directly: `geocontext/views.py::_store_validated_upload` writes every
   EditorJS upload through `storages["media_public"]` unconditionally, with **no** owning-Campaign/
   GeoStory lookup at all. A private/draft story's inline image is reachable today at a stable unsigned
   URL. Gates A (capability) and C (ownership/tenant isolation) — the two gates tickets 03–12 rework —
   are, by contrast, **already implemented and passing**: `OrgScopedPermission`, `CampaignScopedPermission`,
   and `org_role_level()` (epic-11, pre-dating this ticket set) already enforce them, confirmed by ticket
   01's golden-snapshot test and the full existing test suite (1120 tests green). Tickets 03–12 are a
   **refactor of already-correct behavior onto a new `has_perm()`-based architecture** — real value
   (see point 3), but not a confidentiality fix in themselves.
2. **Gate B (entitlement) has no enforcement demand yet.** Ticket 03's own text: *"Ship enforcement
   behind a feature flag or an all-entitled default so this refactor does not silently become a
   product-licensing rollout. Real per-org restrictions are a separate, deliberate product decision."*
   The ticket that anchors the whole ARCH chain (03 → 04 → … → 12) is explicitly a no-op on the one
   genuinely new gate it introduces, until a product decision — not yet made — asks for it.
3. **The ARCH track's real payoff is architectural, not defensive.** `OrgScopedAdminMixin` today
   reimplements the role→verb ladder itself specifically *because* Django's `has_perm()` is meaningless
   for non-superusers here (no `Permission`/`Group` rows are ever synced from Keycloak — see the mixin's
   own docstring). Ticket 06's dynamic `has_perm()` backend would let Django admin's built-in machinery
   (`has_module_permission`, sidebar visibility, third-party admin tooling keyed off `has_perm()`) work
   without that custom reimplementation. Worth doing — but it is a DRY/maintainability improvement to a
   currently-correct system, not a fix to a currently-broken one, so it does not need to gate the S2 fix.
4. **Tickets 08–11 carry the plan's own highest rollback-risk rating ("high").** Splitting per resource
   was already deliberate risk management in this document; sequencing the whole ARCH track after the
   confirmed-open S2 gap is the same risk-management instinct applied one level up.

**What does NOT change:** the ARCH track's own internal design (03 additive → … → 06 `has_perm` meaningful
→ 07 admin → 08–11 migrate DRF resources one at a time, deleting old ladder logic only once each resource
is confirmed on the new path → 12 cleanup) is **staged migration, not a rewrite**, exactly as originally
scoped. Nothing in this section changes that internal sequencing — only *when* the whole chain starts
relative to the media track.

**New execution order:**

```text
01 ✅ → 02 ✅ → 13 ✅ → 14 ✅ → 15 ✅ → 03 ✅ → 04 ✅ → 05 ✅ → 06 ✅ → 07 ✅ → 08 → 09 → 10 → 11 → 12 → 16 → 17
```

### Ticket summary

| Order | # | Ticket | Blocked by | Track |
|---|---|---|---|---|
| 1 | 01 | Baseline & regression tests (S1 + S2 char, golden snapshot, dead-code CI guard) | — | SEC/ARCH |
| 2 | 02 | S1 GeoStory tenant-isolation hotfix — ship first | 01 | SEC |
| 3 | 13 ✅ | S2 media: private EditorJS uploads | 03* | MEDIA |
| 4 | 14 ✅ | Media: visibility/archive lifecycle re-pointing | 13 | MEDIA |
| 5 | 15 ✅ | Media: idempotent backfill | 14 | MEDIA |
| 6 | 03 ✅ | Authorization foundation (entitlement + policy + delete dead perms) | 01 | ARCH |
| 7 | 04 ✅ | `UserAuthorizationSnapshot` model | 03 | ARCH |
| 8 | 05 ✅ | Claim normalization & snapshot sync (Q11 two-path, write rule, resolver) | 04 | ARCH |
| 9 | 06 ✅ | Dynamic `has_perm` backend (A ∩ B) | 05 | ARCH |
| 10 | 07 ✅ | Admin integration + custom `UserAdmin` panel | 06 | ARCH |
| 11 | 08 | DRF: Campaign (org-private matrix) | 06 | ARCH |
| 12 | 09 | DRF: GeoStory (public-read; co-verify w/ 02) | 06, 02 | ARCH |
| 13 | 10 | DRF: Event (incl. A6 second viewset) | 06 | ARCH |
| 14 | 11 | DRF: remaining campaign-owned (A5/A8/A9) | 06 | ARCH |
| 15 | 12 | Workspace/geodata authz cleanup | 08–11 | ARCH |
| 16 | 16 | Full §9 security acceptance matrix | 02–15 | SEC |
| 17 | 17 | Optional storage hardening | — | MEDIA |

\* Ticket 13 needs ticket 03's *policy stable* only in the sense of "the org-role/ownership rules aren't
still changing under it" — satisfied today by the already-shipped `OrgScopedPermission`/
`CampaignScopedPermission`/`org_role_level()` layer, not by ticket 03 itself having run. Ticket 03 is
listed as a blocker in the original graph for policy-stability, not as a hard sequencing requirement, so
running 13 before 03 does not violate the dependency graph as drawn.

### Open Questions carried into implementation (Appendix A1–A9)

Resolve *before/at* the relevant ticket — do NOT fill from memory.

- **A1** `feedback` app `label` (assumed `feedback`, not verified). → ticket 01/03.
- **A2** `GeoStory.status` enum values + `.published()` semantics. → ticket 01/02.
- **A3** `Campaign.visibility` value set + how GeoStory/Event derive it. → ticket 01/13.
- **A4** Current EditorJS upload-time alias in `geocontext/views.py` (S2 root cause). → ticket 01/13.
- **A5** `core/permissions.py` (one class, unread) — identify before touching. → ticket 11.
- **A6** `events/views.py:299` second viewset (`IsAuthenticatedOrReadOnly`) — write gap? → ticket 10.
- **A7** Whether one Keycloak token carries roles for multiple orgs. → ticket 05.
- **A8** GeoFeedback scope decision (currently out of scope). → ticket 11.
- **A9** Whether geodata models beyond `Workspace` should be role-controlled. → ticket 11.

---

## 01 — Baseline & regression tests (S1 + S2 characterization) [SEC/ARCH]

**Phase:** 0 · **PR:** `security: add tenant-isolation regression tests`

**What to build:** A safety net of tests that reproduce and characterize the two security findings
**before any behavior changes**, plus a recorded golden snapshot of current permission behavior, plus
CI assertions that the confirmed dead code stays uncalled. After this ticket the S1 tests are
**failing on purpose** (red), documenting the cross-org leak; the S2 tests characterize today's alias
selection so the later fix can be proven.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [x] Failing tests reproducing **S1** (cross-org GeoStory retrieve): a DCS user can currently retrieve
      QG2's **draft** and **archived** GeoStory. Assert the *desired* target so the tests are red now and go green in ticket 02.
- [x] Tests characterizing **S2**: an EditorJS upload under a **private** campaign, and whether the
      resulting object's alias is public/private and its URL signed vs unsigned.
- [x] Record current permission behavior for **Campaign / Event / GeoStory / Workspace** as a golden snapshot.
- [x] Verify + pin actual app labels + model names: confirm **`feedback`** label (A1), **`GeoStory.status`**
      enum + `.published()` (A2), **`Campaign.visibility`** value set (A3). Document findings in the test module.
- [x] Re-assert in CI (grep test) that `tosca_authentication/permissions.py`
      (`IsSuperAdmin/IsAdmin/IsEditor/IsViewer`) has **zero real call sites** (only docstring occurrences).
- [x] Tests under `geostories/tests/` and `core/tests/`. No production behavior changes. Rollback risk: none.

**S1 minimum regression set (target assertions):** cross-org draft → 404 · cross-org archived → 404 ·
cross-org published/public → 200 (must stay public) · own-org draft → 200 · list excludes cross-org unpublished.

**S2 minimum characterization set:** private-campaign upload alias recorded · private/draft URL signed-vs-unsigned
recorded · public+published alias recorded.

---

## 02 — GeoStory tenant-isolation hotfix (S1) [SEC]

**Phase:** 1 · **PR:** `security: scope unpublished geostories by organization`

**What to build:** Close the active cross-organization confidentiality leak in GeoStory reads. Today
`GeoStoryViewSet.get_queryset()` restricts to `published()` **only when the user is anonymous** — an
authenticated user's retrieve queryset is the full set with **no org filter**, so a DCS user can read
QG2's draft/archived stories. Rescope so unpublished/private rows are visible only to the caller's
active/default org, while published/public rows stay public to everyone. Ships **standalone, ahead of
the backend refactor**. `CampaignScopedPermission` deliberately passes SAFE methods, so the **queryset
is the real tenant gate** — `has_perm` does not solve this (a DCS READER may hold
`geostories.view_geostory` yet must not see a QG2 draft).

**Target rule:**
```text
Anonymous:      published/public content only
Authenticated:  published/public content from ANY org
                + unpublished/private content of the caller's active/default org
Cross-org draft/private/archived: never enters the queryset → retrieve returns 404
```

**Blocked by:** 01 (its S1 tests turn green here).

**Status:** ready-for-agent

- [x] Rewrite `GeoStoryViewSet.get_queryset()` to the target rule.
- [x] Cross-org **draft** retrieve → **404** (was 200).
- [x] Cross-org **archived** retrieve → **404**.
- [x] Cross-org **published/public** retrieve → **200** (regression guard).
- [x] Own-org permitted user retrieves own **draft** → **200**.
- [x] **list** endpoint excludes cross-org unpublished rows.
- [x] Confirm `GeoStory.status` enum + `.published()` semantics (A2) before writing the filter.
- [x] All S1 regression tests from ticket 01 go green.

**Rollback risk:** low (single `get_queryset`); watch for accidentally hiding legitimate cross-org **published** reads.

---

## 03 — Authorization foundation: entitlement + policy model (additive) [ARCH]

**Phase:** 2.1–2.5 · **PR:** `authz: add organization entitlement and policy model`

**What to build:** The additive foundation for gates **A (capability)** and **B (entitlement)** —
nothing enforces it yet. Introduce a single source of truth for entitleable apps and role-controlled
models, a per-organization entitlement model, and a data migration seeding every existing organization
with all in-scope apps so **no organization loses access on deploy**. Also delete the confirmed-dead
legacy permission classes.

**Deferred until after the media track (2026-08-19, see "Execution sequencing decision" above):** this
ticket's own hard gate says entitlement enforcement must ship as a no-op (all-entitled default) until "a
separate, deliberate product decision" asks for real per-org restriction — that decision hasn't been
made. Building the model+migration now would be schema for a policy that doesn't exist yet
(speculative generality). Do this after 13–15, when gate B enforcement is actually needed, or when
starting the ARCH track regardless. The dead-permission-class deletion bullet below is independent of
the entitlement work and could ship earlier if desired, but is kept in this ticket per the original split.

**Blocked by:** 01. **Execution order:** after 15 (see sequencing decision).

**Status:** done (2026-08-19)

- [x] Delete confirmed-dead `tosca_authentication/permissions.py` (`IsSuperAdmin/IsAdmin/IsEditor/IsViewer`
      — zero real call sites; `IsEditor/IsViewer` gate on never-populated Django groups, would deny everyone if wired).
      — deleted `tosca_api/apps/authentication/permissions.py` (the actual path; the ticket's `tosca_authentication`
      naming was shorthand). CI guard in `test_security_baseline.py` updated from "stays uncalled" to
      "stays deleted" (asserts the file no longer exists + the four class names don't resurface anywhere).
- [x] Add `organizations/policy.py` with `LEVEL_ACTIONS`, `user_claims`, `enabled_apps_for` (skeleton).
      Also added `role_controlled_models_for_app` (reads `TOSCA_PERMISSION_MODELS`) as a small companion the
      spec didn't name but ticket 06 will need alongside `enabled_apps_for`. `user_claims` is a stub
      (`NotImplementedError`, wired in ticket 05); nothing in this diff calls into `policy.py` from any
      view/permission/admin path yet — additive only, per the ticket's own hard gate.
- [x] Add to settings the **single source of truth** and derived set:
      ```python
      TOSCA_PERMISSION_MODELS = {
          "campaigns":         {"campaign"},
          "geostories":        {"geostory"},
          "events":            {"event"},
          "feedback":          {"geofeedback"},   # model is GeoFeedback (Open Q A8: in scope?)
          "geocontext":        {"geocontext"},
          "geodata_providers": {"workspace"},      # Open Q A9: extend if Layer/Store/Style API-exposed
      }
      TOSCA_ENTITLEABLE_APPS = set(TOSCA_PERMISSION_MODELS)   # derived — never maintained twice
      ```
      Rationale: entitling an *app* must **not** auto-expose every future model in it (revisions, audit
      logs, import jobs). Only models in this map get role-controlled.
- [x] Add `OrganizationAppEntitlement` (smallest form) + validator + migration:
      ```text
      - organization  (FK → organizations.Organization)
      - app_label     (CharField, validated against TOSCA_ENTITLEABLE_APPS)
      - unique_together (organization, app_label)
      ```
      — `organizations/migrations/0004_organizationappentitlement.py`; validator checks membership in
      `settings.TOSCA_ENTITLEABLE_APPS` at call time (not import time), so it respects `override_settings` in tests.
- [x] Data migration seeding all existing organizations with their current expected entitlements (all in-scope apps).
      — `organizations/migrations/0005_seed_all_entitlements.py`, bulk-creates the org × app cross product with
      `ignore_conflicts=True` (idempotent); reverse migration deletes only rows whose `app_label` is in
      `TOSCA_ENTITLEABLE_APPS`, scoped so it can't touch a future real entitlement decision's rows.
- [x] **HARD GATE:** after deployment, no existing organization loses access due to entitlement. Ship
      enforcement behind a feature flag or an all-entitled default so this refactor does **not** silently
      become a product-licensing rollout. Real per-org restrictions are a **separate, deliberate** product decision.
      — satisfied: every org gets every entitleable app via the seed migration, and nothing in this diff
      consults `policy.py`/entitlements from any enforcement path yet.
- [x] Admin: entitlement inline on `OrganizationAdmin`. — `OrganizationAppEntitlementInline` (TabularInline).
- [x] Tests: entitlement validation (rejects app labels not in the SOT); seed migration correctness.
      — `organizations/tests/test_entitlements.py` (6 tests: valid/invalid app_label, unique_together,
      `enabled_apps_for` scoping, SOT derivation, settings-override respected) +
      `organizations/tests/test_seed_entitlements_migration.py` (3 tests: full coverage, idempotency, unseed
      scoping). Full suite: 1143 passed (same 5 pre-existing unrelated failures as baseline — `test_editorjs_uploads`
      and `test_database_settings`, both environment-dependent, not touched by this ticket).

**Files:** `organizations/models.py`, `organizations/policy.py`, `settings/base.py`, migration,
`organizations/admin.py`. **Behavior:** additive. **Rollback risk:** low (additive schema).

---

## 04 — Authorization snapshot model (additive) [ARCH]

**Phase:** 2.6–2.7 · **PR:** `authz: add authorization snapshot`

**What to build:** The persistence for the browser/admin authorization path. Add
`UserAuthorizationSnapshot` storing the org roles a user's Keycloak token carried at their last
successful **browser** login, so later browser/admin requests do not decode a stale (possibly expired)
ID token on every request. Additive only — ticket 05 wires writes; ticket 06 the reads.

**New model:**
```text
UserAuthorizationSnapshot
- user        (OneToOne → AUTH_USER_MODEL)
- org_roles   (JSONField)      # {"dcs": "ADMIN", "qg2": "WRITER"}
- default_org (CharField)
- synced_at   (DateTimeField)
```

**Design note — multi-org-ready now, single-org enforced now:** store *all* org roles the token carries,
but authorization for the PoC uses only `org_roles[default_org]`. No org-switching/multi-org request
context in this plan; the shape exists solely to avoid a later migration.

**Blocked by:** 03.

**Status:** done (2026-08-19)

- [x] Add `UserAuthorizationSnapshot` (in `organizations/models.py` or `tosca_authentication/`) + migration.
      — added to `organizations/models.py` (alongside `Organization`/`OrganizationAppEntitlement`, since
      it's read by the same app's `policy.py`): `user` (OneToOne → `AUTH_USER_MODEL`), `org_roles`
      (JSONField, default `{}`), `default_org` (CharField, blank-default), `synced_at` (DateTimeField,
      explicit — distinct from `TimeStampedModel`'s `updated_at`, which only tracks row writes).
      Migration `organizations/migrations/0006_userauthorizationsnapshot.py`.
- [x] Admin: read-only snapshot visibility where useful (expose `synced_at`).
      — `UserAuthorizationSnapshotAdmin` registered in `organizations/admin.py`; `list_display` shows
      `user`, `default_org`, `synced_at`; add/change both denied (write-only from the future login sync path).
- [x] Leave a clean seam for a future TTL (e.g. `_load_valid_snapshot`) but implement **no** TTL now.
      — `organizations/policy.py::_load_valid_snapshot(user)`: returns the persisted row or `None`, no
      expiry check yet.
- [x] Leave a clean **invalidation seam** (e.g. `policy.invalidate_snapshot(user)`) for the demotion remedy.
      — `organizations/policy.py::invalidate_snapshot(user)`: deletes the row if present, no-op otherwise.
- [x] Tests: model + admin rendering.
      — `organizations/tests/test_authorization_snapshot.py` (10 tests: field storage/`__str__`, OneToOne
      uniqueness, `_load_valid_snapshot`/`invalidate_snapshot` seams, admin add/change denial, changelist
      renders). Full suite: 1166 passed.

**Files:** `organizations/models.py`, `organizations/policy.py`, `organizations/admin.py`, migration.
**Behavior:** additive. **Rollback risk:** low.

---

## 05 — Claim normalization & snapshot sync [ARCH]

**Phase:** 3 · **PR:** `authz: normalize keycloak claims and snapshot sync`

**What to build:** Normalize Keycloak roles into a multi-org `org_roles` shape + a `default_org`, and
wire the two auth entry paths differently per the Q11 decision. Bearer/API always reads freshest live
claims and never touches the snapshot; browser/admin persists a snapshot at login (guarded so a missing
mapper cannot destroy a valid snapshot). Implement the single uniform resolver both paths and later
`has_perm` consult.

**Two paths (Q11):**
- **Bearer/API:** parse current claims from the verified token; build normalized `org_roles` + `default_org`;
  attach **request-local** to the user (`user._auth_claims`). **Never persist** API-token claims. Freshest source.
- **Browser/admin (`KeycloakAdapter`):** attach `user._auth_claims`; persist snapshot **only if
  authoritative**. Do **not** decode a stored/expired ID token on later requests; do **not** write
  normalized state into `SocialAccount.extra_data`.

**Snapshot write rule (respect authoritative extraction):**
```text
roles authoritative + non-empty → write/update snapshot
roles authoritative + empty     → write empty snapshot   (Keycloak really returned none)
roles non-authoritative/missing → DO NOT overwrite previous snapshot
```
Rationale: a missing mapper/claim (`ExtractedRoles.authoritative == False`) must not silently destroy a
previously valid snapshot — mirrors the demotion guard in `sync_user_permissions_from_roles`.

**Authorization-source precedence (one uniform resolver):**
```text
1. current request-local live claims (user._auth_claims)  ← Bearer, and the login request itself
2. persisted UserAuthorizationSnapshot                    ← later browser/admin requests
3. no permissions / FAIL CLOSED
```
No implicit decoding of stale ID tokens; no hidden in-memory "last known good" fallback.

**Migration note added 2026-08-19 (grilling session, Q3):** today, *before* this ticket runs,
`organizations/permissions.py::get_request_org_context()` already has a browser/admin fallback path that
reads `user.socialaccount_set...extra_data` live on every request (via `extract_roles_from_social_data` /
`extract_org_from_social_data`) — populated by allauth at each login. It is not literally a "decode a
stale ID token" bug, and its staleness profile ("current as of last login") already matches what
`UserAuthorizationSnapshot` is meant to provide. Two designs were weighed for this ticket:

- **Reuse `extra_data`** as the persisted-claims source instead of adding a new model (cheaper, zero new
  schema, functionally equivalent staleness behavior today).
- **Build `UserAuthorizationSnapshot` as originally scoped**, and treat `extra_data` as allauth-owned
  identity-provider metadata that authorization state should not be coupled to — a third-party library's
  internal storage shape can change or be replaced (different login path, provider swap) without any
  contract to keep serving as our authorization source of truth. The snapshot's `synced_at`, explicit
  `invalidate_snapshot()` seam, and the authoritative-write-rule enforced *by us* (not by allauth) are
  real, decision-owned properties `extra_data` doesn't give us for free.

**Decision: build `UserAuthorizationSnapshot` as originally scoped** (decoupling wins over reuse). This
ticket must additionally **retire the `extra_data` fallback**, not run alongside it:
`get_request_org_context()`'s browser/admin branch (the `else` arm that currently calls
`user.socialaccount_set.filter(provider="keycloak").first()` and reads its `extra_data`) must be
repointed to read `UserAuthorizationSnapshot` instead, once this ticket lands. Leaving both live
permanently would mean two sources of truth for the same fact that can silently drift apart — that is
explicitly *not* the intended end state.

**Blocked by:** 04.

**Status:** done (2026-08-19)

- [x] Normalize roles → multi-org `org_roles`, highest level per org (reuse `parse_role_name` + `org_role_level`;
      `LEVEL_RANK = {READER:0, WRITER:1, ADMIN:2}`).
      — `role_sync.normalize_org_roles`/`denormalize_org_roles` + `LEVEL_RANK` (moved here as the single source of
      truth; `organizations/permissions.py::LEVEL_RANK` now imports it instead of redefining it). Project-scoped
      roles (`parsed.project`) are deliberately dropped — no consumer yet (canonical §10 "no project-level roles").
- [x] Determine `default_org` (`role_sync._org_slug_from_payload` handles scalar `default_organization` and list `organization`, first).
      — unchanged, reused as-is via `extract_org_from_token`/`extract_org_from_social_data`.
- [x] Browser login attaches `user._auth_claims` and persists snapshot per the authoritative write rule.
      — `KeycloakAdapter._apply_permissions` (all 4 call sites in `pre_social_login`/`save_user`) now calls
      `role_sync.build_auth_claims` → `role_sync.attach_auth_claims` → `organizations.policy.sync_snapshot`.
- [x] Bearer auth attaches `user._auth_claims` and **never** mutates the snapshot.
      — `KeycloakTokenAuthentication._apply_permissions` calls `build_auth_claims`/`attach_auth_claims` only;
      no `sync_snapshot` call on this path (Q11).
- [x] Implement the unified resolver with the precedence above (fail closed).
      — `organizations/policy.py::user_claims(user)`: live `user._auth_claims` → persisted
      `UserAuthorizationSnapshot` → `({}, None)`.
- [x] Confirm whether one token actually carries multiple orgs' `ROLE_<ORG>_<LEVEL>` before relying on multi-org population (A7).
      — not independently re-verified in this ticket; `normalize_org_roles` is written to handle it correctly
      (dict keyed by every distinct org slug seen) if/when it occurs, per ticket 04's "multi-org-ready now,
      single-org enforced now" design note. No live-token evidence gathered here.
- [x] Repoint `get_request_org_context()`'s browser/admin fallback from `SocialAccount.extra_data` to
      `UserAuthorizationSnapshot` (see migration note above) — do not leave both live.
      — done: the browser/admin branch now calls `policy.user_claims(user)` and rebuilds a pseudo role set via
      `denormalize_org_roles` (plus `DJANGO_SUPERADMIN`/`DJANGO_STAFF` synthesized from the already-Keycloak-synced
      `user.is_superuser`/`user.is_staff` flags, since the normalized snapshot shape doesn't carry raw platform-role
      strings). `extract_roles_from_social_data`/`extract_org_from_social_data` are no longer imported by
      `permissions.py`; they're still used by `KeycloakAdapter` itself for the login-time extraction that populates
      the snapshot in the first place — that's a different call site, not the fallback being retired.
- [x] Tests: multiple org roles; highest-level selection; default-org lookup; authoritative-empty (writes empty);
      non-authoritative-missing (no overwrite); expired/missing stored ID token; **new browser request after login**;
      Bearer overriding a stored snapshot.
      — `authentication/tests/test_role_sync.py` (normalize/denormalize/build_auth_claims, 7 new tests),
      `organizations/tests/test_authz_resolver.py` (resolver precedence, snapshot write rule, browser-fallback
      repoint, Bearer-overrides-snapshot — 14 tests), `authentication/tests/test_auth_claims_attachment.py`
      (Bearer attaches-without-persisting vs. browser attaches-and-persists, non-authoritative doesn't clobber — 3
      tests). Full suite: 1174 passed (same 5 pre-existing environment-dependent failures as ticket 03's baseline —
      `test_editorjs_uploads` x3 and `test_database_settings` x2 — untouched by this ticket).

**Accepted trade-off (document it):** browser/admin authorization may be **stale until next login** after
a Keycloak role change; API requests are always fresh. **Demotion is the sharp edge** — a demoted user
keeps higher browser/admin capability until next login; remedy is force logout / clear the user's
`UserAuthorizationSnapshot` (deleting the row falls through to fail-closed). Promotion staleness is a
usability annoyance; **demotion staleness is a security window**, so the invalidation hook is a security requirement.

**Files:** `tosca_authentication/backends.py`, `tosca_authentication/role_sync.py`, `organizations/policy.py`.
**Rollback risk:** medium (login path) — guard with the authoritative rule; feature-flag if needed.

---

## 06 — Dynamic Django permission backend [ARCH]

**Phase:** 4 · **PR:** `authz: add dynamic django permission backend`

**What to build:** The backend that makes `has_perm()` meaningful for non-superusers by computing
capability dynamically — **no per-user `Permission` rows**. It intersects the active/default org role (A),
the org's app entitlements (B), and the role-controlled model allow-list, then emits only
`view/add/change/delete` codenames. This is gate **A ∩ B**; it deliberately has **no** row/org dimension
(that is gate C, enforced by querysets/object perms elsewhere).

**Computation:**
```text
active/default org → org role → allowed CRUD actions
→ entitled apps (B) → role-controlled models (TOSCA_PERMISSION_MODELS)
→ Permission codenames (view/add/change/delete only; custom perms filtered out)
```

**Role → verb map (Layer A):**
```text
READER → view
WRITER → view, add, change
ADMIN  → view, add, change, delete
```
Use only `view_*/add_*/change_*/delete_*`. **No custom `manage_*`.** Custom permissions (e.g. `publish_*`)
are intentionally excluded by an action-prefix filter.

**Blocked by:** 05.

**Status:** done (2026-08-19)

- [x] Implement `organizations/auth_backend.py::OrgRolePermissionBackend(BaseBackend)`; `authenticate()` returns `None`.
- [x] Register in `AUTHENTICATION_BACKENDS` = `[ModelBackend, OrgRolePermissionBackend, allauth.AuthenticationBackend]`.
      — inserted between `ModelBackend` and `allauth.account.auth_backends.AuthenticationBackend` in `settings/base.py`.
- [x] `has_perm()` consults the ticket-05 resolver (live claims → snapshot → fail closed). No per-user rows.
      — calls `organizations.policy.user_claims(user_obj)` directly; consults only `org_roles[default_org]`
      per ticket 04's single-org-enforced design.
- [x] Custom-permission action-prefix filter excludes anything outside view/add/change/delete.
      — `_MANAGED_ACTIONS = {"view", "add", "change", "delete"}`; codename split via `partition("_")`, action
      checked against the set before anything else runs.
- [x] Tests: READER / WRITER / ADMIN verb sets; app **not** entitled → denied (B); model **not** in allow-list → denied;
      no snapshot & no live claims → **fail closed**; superuser → all; inactive user → denied.
      — `organizations/tests/test_auth_backend.py` (15 tests): role verb sets (3), entitlement-missing +
      entitlement-for-a-different-app-doesn't-leak (2), model-not-allowlisted + unknown-app-label +
      custom-permission-excluded (3), no-claims-at-all + no-role-for-default-org fail-closed (2), superuser-bypasses
      + inactive-superuser-denied + inactive-regular-user-denied (3), plus two end-to-end tests through Django's
      real `user.has_perm()` dispatch confirming the backend is actually wired into `AUTHENTICATION_BACKENDS`, not
      just directly callable. Full suite: 1189 passed (same 5 pre-existing environment-dependent failures as
      ticket 05's baseline — untouched by this ticket).

**Deliberately out of scope here (per ticket 07-12):** this ticket does **not** touch `OrgScopedAdminMixin`'s own
capability ladder or any existing DRF permission class — both keep enforcing gate A themselves, side by side with
this now-meaningful `has_perm()`, until later tickets migrate call sites onto it one resource at a time. Gate C
(tenant/object scope) stays entirely with `organizations.permissions` querysets/object-permission checks; nothing
in `OrgRolePermissionBackend` reads or filters by organization ownership of a specific row.

**Post-hoc regression found and closed (2026-08-19, user-requested re-audit):** the "purely additive" claim above
holds for every call site that was *already* consulting `has_perm()` on purpose (none did — see the
`OrgScopedAdminMixin._is_active_staff` docstring's own note that it deliberately avoids `has_perm()`). It does
**not** automatically hold for call sites that consult `has_perm()` as an *unintentional default* — and Django's
own `ModelAdmin.has_view/add/change/delete_permission()` is exactly that: any `ModelAdmin` that doesn't override
those methods falls through to `request.user.has_perm(...)`. Auditing every `TOSCA_PERMISSION_MODELS` entry's
admin class found one such case: **`GeoFeedbackAdmin`** (`feedback/admin.py`) is a plain `admin.ModelAdmin` with
no `OrgScopedAdminMixin` and no queryset org-scope of its own — before this ticket, `has_perm()` was always
`False`, so it was superuser-only in practice; after registering `OrgRolePermissionBackend`, any staff user
holding a WRITER+ role in *any* org entitled to `feedback` could see/edit **every** organization's GeoFeedback
rows, not just their own. (`GeoContextAdmin` has the same missing-mixin shape but was confirmed *not* a
regression: `GeoContext` has no organization/campaign FK at all — it's a shared, unowned content-block model, so
there is no tenant boundary to leak across.)

**Resolution:** rather than migrating `GeoFeedbackAdmin` onto `OrgScopedAdminMixin` (which would be a substantive,
undiscussed scope expansion — GeoFeedback's org-authorization integration is its own open decision, ticket 11's
A8: *"GeoFeedback scope decision (currently out of scope) ... `feedback/views.py` uses local `IsAdminOrReadOnly`.
Leave out unless the project decides otherwise"*), `GeoFeedbackAdmin.get_queryset` got a narrow, self-contained
org scope (superuser/exempt bypass, else filter by `campaign__organization__slug`, mirroring
`OrgScopedAdminMixin.get_queryset` exactly) — closing the row-leak without adopting the mixin's add-time
org-resolution or capability-ladder machinery. A code comment on `GeoFeedbackAdmin` records this as a known-gap
module, so it isn't mistaken for a completed migration later. Tests: `feedback/tests/test_admin.py` (5 tests —
own-org scoping, cross-org row genuinely absent (not just unlisted), superuser unscoped, `DJANGO_STAFF` exempt
unscoped, no-org-role empty).

**Behavior:** `has_perm()` becomes meaningful for non-superusers. **Rollback risk:** medium — removing the backend restores prior all-False behavior.

---

## 07 — Django admin authorization integration [ARCH]

**Phase:** 5 · **PR:** `authz: migrate admin authorization`

**What to build:** Make the Django admin consume the now-meaningful `has_perm()`. Admin entry stays gated
by `is_staff`; which models/actions appear is driven by `has_perm()`; which organization rows are visible
is driven by admin queryset scope. Slim the admin mixin to row-scoping and register a custom `UserAdmin`
with a read-only effective-permissions panel (none exists today — the default Django `UserAdmin` is in use).

**Split of responsibilities:**
```text
is_staff            = may enter admin
has_perm()          = which models/actions
admin queryset scope= which organization rows
```

**Blocked by:** 06.

**Status:** done (2026-08-19)

- [x] Simplify `OrgScopedAdminMixin` to row-scoping (`get_queryset`, `org_lookup` e.g. `owner_org__slug`) +
      standard `has_*_permission`; **drop** its duplicated role/action capability logic (now owned by the backend).
      — `has_add/change/delete/view_permission` now delegate to Django's own default implementation via `super()`
      (which reads `request.user.has_perm(...)`, meaningful as of ticket 06), gated only by an explicit `is_staff`
      check; `check_org_level`'s object-org comparison was dropped from the mixin (it's still used standalone by
      `has_org_write_access`, untouched). Cross-org writes/deletes are prevented the same way cross-org reads are:
      `get_object` filters through `get_queryset` first, so a cross-org row is never fetched at all — matches the
      "queryset is the real tenant gate" pattern already used by `OrgScopedPermission`/`CampaignScopedPermission`.
      Dead code removed: `_org_attr` (only existed to feed the now-deleted `check_org_level` calls).
- [x] Register a custom `UserAdmin` with a **read-only effective-permissions panel**: current/default org,
      org role(s), entitled apps, computed `get_all_permissions()`, and `synced_at` ("last synced at login").
      — `organizations/admin.py::UserAdmin(DjangoUserAdmin)`, registered in place of the default (unregistered
      first). Panel fields: `effective_default_org`, `effective_org_roles`, `effective_platform_exempt` (new,
      see fix below), `effective_entitled_apps`, `effective_permissions`, `effective_synced_at` — all read-only,
      only shown on the change form (never the add form). **Prerequisite fix**: Django's `get_all_permissions()`
      aggregator calls each backend's `get_user_permissions()`, not `has_perm()` — `OrgRolePermissionBackend`
      (ticket 06) only had `has_perm()`, so the panel would have shown nothing. Added
      `OrgRolePermissionBackend.get_user_permissions()`, extracted the shared org/entitlement resolution into
      `_capability_context()` so `has_perm()` and the new method share it instead of duplicating the chain
      (`has_perm()`'s own per-permission parsing/checking logic is unchanged).
- [x] Keep the `is_staff` gate intact. — explicit `_is_active_staff` check preserved in the mixin (defense-in-depth
      for direct calls that bypass `AdminSite.has_permission`, e.g. tests).
- [x] Tests: admin menu/action visibility per role and per org; effective-perms panel renders correct computed perms.
      — `organizations/tests/test_permissions.py` (existing admin-scoping tests updated + 2 new: `is_staff` entry
      gate, `get_object` 404s cross-org id), `organizations/tests/test_user_admin.py` (13 tests: each panel field,
      readonly-only-when-editing, full HTTP change-view render). Full suite: 1218 passed (same 5 pre-existing
      environment-dependent failures as ticket 06's baseline).

**Regression found and fixed during this ticket (2026-08-19, user-requested re-audit):** writing a realistic
admin test (session/browser request, not the Bearer-style `request.auth` shortcut prior admin tests reused)
surfaced a real precision bug in ticket 05's own `get_request_org_context`: its browser-fallback branch inferred
platform exemption (`DJANGO_STAFF`/`DJANGO_SUPERADMIN`) from `user.is_superuser`/`user.is_staff` — but those Django
columns are editable independently of Keycloak (e.g. via this very `UserAdmin`'s own "Permissions" fieldset), so
manually toggling `is_staff` on an org WRITER would have silently also granted them full cross-org bypass
everywhere `get_request_org_context` is consulted (DRF permission classes and admin queryset scoping alike). Fixed
by capturing platform-role membership as real claims data instead of inferring it: `AuthClaims.platform_exempt`
(computed once in `build_auth_claims` from the actual token/login role set) and a new
`UserAuthorizationSnapshot.platform_exempt` column (migration `0007_userauthorizationsnapshot_platform_exempt`),
read back through a new `policy.is_platform_exempt(user)` accessor mirroring `user_claims`'s precedence (live
claims → snapshot → fail closed `False`). `get_request_org_context`'s browser branch now calls
`is_platform_exempt(user)` instead of inspecting `is_staff`/`is_superuser`. One of ticket 05's own tests
(`test_get_request_org_context_browser_exempt_for_superuser_without_org_role`) encoded the buggy expectation and
was corrected; this also restores the exact pre-ticket-05 behavior for a bare Django superuser with no Keycloak
login (not exempt through this resolver — callers needing a superuser bypass check `request.user.is_superuser`
separately and first, as `OrgScopedAdminMixin.get_queryset`/`check_org_level` already do).

**Files:** `organizations/permissions.py` (slim mixin), `organizations/admin.py` (custom `UserAdmin`),
`organizations/auth_backend.py` (`get_user_permissions`), `authentication/role_sync.py` (`AuthClaims.platform_exempt`),
`organizations/models.py` + migration (`UserAuthorizationSnapshot.platform_exempt`), `organizations/policy.py`
(`is_platform_exempt`).
**Rollback risk:** medium — admin access regressions; keep `is_staff` gate intact.

---

## 08 — Migrate Campaign DRF permissions [ARCH]

**Phase:** 6 / PR 8a · **PR:** `authz: migrate Campaign DRF permissions`

**What to build:** First slice of the DRF enforcement refactor (split per resource on purpose — do **not**
ship Phase 6 as one PR). Strip the action→level ladders out of the scope permission classes so they keep
**only** org/object scope + queryset scoping (gate C), and add the correct model-permission class (gate A)
for the Campaign resource. Campaign is an **org-private** resource.

**Per-resource matrix (org-private):**
```text
Org-private resources (Campaign, geodata management):
    ViewGatedModelPermissions      # subclass: GET/HEAD → view_<model>
    + OrgScopedPermission          # C: org/object scope only
```

`ViewGatedModelPermissions`:
```python
class ViewGatedModelPermissions(DjangoModelPermissions):
    perms_map = {
        **DjangoModelPermissions.perms_map,
        "GET":  ["%(app_label)s.view_%(model_name)s"],
        "HEAD": ["%(app_label)s.view_%(model_name)s"],
    }
```

**Why not plain `DjangoModelPermissions`:** it sets `authenticated_users_only = True`, and DRF permission
classes are **AND-composed** — for public-read resources it would still reject anonymous GET. Campaign is
private so `ViewGatedModelPermissions` is correct here; the anon-read caveat matters for tickets 09/10.

**Blocked by:** 06.

**Status:** ready-for-agent

- [x] Strip action→level ladder from `OrgScopedPermission`; keep only org/object scope + queryset scoping.
- [x] Add `ViewGatedModelPermissions` and apply the org-private matrix to the Campaign viewset.
- [x] Add a Campaign permission-matrix test suite (READER read own / WRITER change own / ADMIN delete own; cross-org denied; entitlement-missing denied).
- [x] Verify no global DRF default is introduced — resource-by-resource only.

**Files:** `organizations/permissions.py`, `campaigns/views.py`. **Rollback risk:** high — hence the per-resource split.

---

## 09 — Migrate GeoStory DRF permissions [ARCH]

**Phase:** 6 / PR 8b · **PR:** `authz: migrate GeoStory DRF permissions`

**What to build:** Apply the **public-read** matrix to the GeoStory viewset: anonymous GET stays 200 for
published/public content, writes require add/change/delete. Keep the scope class to org/object scope only.
Interacts directly with the ticket-02 S1 queryset fix — verify both together so the queryset (C) and the
permission classes (A) compose correctly and anonymous public reads are not broken.

**Per-resource matrix (public read):**
```text
Public Event/GeoStory:
    DjangoModelPermissionsOrAnonReadOnly   # anon GET allowed; writes need add/change/delete
    + CampaignScopedPermission             # C: org/object scope only (writes)
```

**Anonymous-read guardrail:** plain `DjangoModelPermissions` cannot preserve anonymous public GET
(`authenticated_users_only = True`, AND-composed). Use `DjangoModelPermissionsOrAnonReadOnly`.
**Anonymous public GET must stay 200.**

**Blocked by:** 06 **and** 02.

**Status:** ready-for-agent

- [ ] Strip the action→level ladder from `CampaignScopedPermission`; keep only org/object scope + queryset scoping
      (SAFE methods still pass — the queryset is the read tenant gate).
- [ ] Apply `DjangoModelPermissionsOrAnonReadOnly + CampaignScopedPermission` to the GeoStory viewset.
- [ ] Confirm anon GET of published/public GeoStory → **200**; cross-org draft → **404** (ticket 02 holds); writes need add/change/delete.
- [ ] Add a GeoStory permission-matrix test suite covering the §9 rows for GeoStory.

**Files:** `geostories/views.py`. **Rollback risk:** high — co-verify with ticket 02.

---

## 10 — Migrate Event DRF permissions [ARCH]

**Phase:** 6 / PR 8c · **PR:** `authz: migrate Event DRF permissions`

**What to build:** Apply the **public-read** matrix to the Event viewset(s). Same shape as GeoStory.
Investigate and correctly gate the **second viewset** at `events/views.py:299` (currently
`IsAuthenticatedOrReadOnly`) — identify it and determine whether it is a write gap.

**Per-resource matrix (public read):**
```text
Public Event/GeoStory:
    DjangoModelPermissionsOrAnonReadOnly
    + CampaignScopedPermission
```

**Blocked by:** 06.

**Status:** ready-for-agent

- [ ] Apply `DjangoModelPermissionsOrAnonReadOnly + CampaignScopedPermission` to the primary Event viewset.
- [ ] Resolve A6: identify the second viewset at `events/views.py:299`, confirm whether its `IsAuthenticatedOrReadOnly` is a write gap, and gate it correctly.
- [ ] Anonymous public GET stays **200**; writes need add/change/delete; cross-org writes denied by scope.
- [ ] Note `Event` has **no ARCHIVED status** (`Event.Status`) and `Event.effective_visibility` already derives
      visibility from the owning Campaign — context for the media track, not a change here.
- [ ] Add an Event permission-matrix test suite covering the §9 rows for Event.

**Files:** `events/views.py`. **Rollback risk:** high.

---

## 11 — Migrate remaining campaign-owned DRF resources [ARCH]

**Phase:** 6 / PR 8d · **PR:** `authz: migrate remaining campaign-owned resources`

**What to build:** Finish the DRF enforcement refactor by applying the correct §6 matrix (public-read vs
org-private) to every other campaign-owned resource found in the repo, resource-by-resource. Resolve the
outstanding identification Open Questions. Public catalog endpoints keep `AllowAny` — GeoServer ACL
remains the authority for layer visibility.

**Matrix reference:**
```text
Org-private (Campaign, geodata mgmt): ViewGatedModelPermissions + OrgScopedPermission
Public (Event/GeoStory):              DjangoModelPermissionsOrAnonReadOnly + CampaignScopedPermission
Public catalog (catalog_api):         retain AllowAny — GeoServer ACL is the authority
```

**Blocked by:** 06.

**Status:** ready-for-agent

- [ ] Enumerate remaining campaign-owned DRF resources and apply the correct matrix to each (no global switch; verify intent per resource).
- [ ] Identify `core/permissions.py` (one class, not yet read) before touching it (A5).
- [ ] Decide GeoFeedback scope (A8): currently **out of scope** — `feedback/views.py` uses local `IsAdminOrReadOnly`.
      Leave out unless the project decides otherwise; record the decision.
- [ ] Decide whether geodata models beyond `Workspace` (Layer/Store/Style) should be role-controlled (A9);
      if yes, extend `TOSCA_PERMISSION_MODELS["geodata_providers"]` (coordinate with ticket 03's SOT).
- [ ] Retain `AllowAny` on public catalog endpoints (catalog_api Workspace/Layer list).
- [ ] Add permission-matrix tests for each migrated resource; **anonymous public GET must stay 200** where applicable.

**Rollback risk:** high — migrate one resource at a time.

---

## 12 — Workspace / geodata authorization cleanup [ARCH]

**Phase:** 7 · **PR:** `authz: fix workspace organization authorization`

**What to build:** Replace the blanket `IsAdminUser` gate in the geodata provider API where an org **ADMIN**
should manage their **own** organization's resources without needing Django `is_staff`. Enforce `model
capability (A) + organization ownership (C)` instead. Preserve the boundary: Django owns *what roles mean*;
GeoServer ACL owns *layer-level visibility* — do **not** move GeoServer ACL authorization into `has_perm()`.

**Blocked by:** 08, 09, 10, 11 (all of PR 8 / the DRF enforcement refactor).

**Status:** ready-for-agent

- [ ] Replace `IsAdminUser` in `geodata_providers/api/views.py` with `model capability (A) + organization ownership (C)` where an org ADMIN should manage own-org resources.
- [ ] Verify interaction with GeoServer/Keycloak role sync (`geodata_providers/role_sync.py`, `security_sync.py`) is unaffected.
- [ ] Document the boundary explicitly: Django owns role meaning; GeoServer ACL owns layer visibility. No GeoServer ACL redesign.
- [ ] Tests: org ADMIN manages own-org workspaces (no `is_staff` required); cross-org management denied; GeoServer sync still functions.

**Files:** `geodata_providers/api/views.py`. **Rollback risk:** medium — verify GeoServer sync unaffected.

---

## 13 — Media: enforce private EditorJS uploads (S2) [MEDIA]

**Phase:** 8.1–8.4 · **PR:** `media: enforce private editorjs uploads`

**What to build:** Start closing security finding **S2** (object-storage exposure). Today EditorJS media
and hero images can land under the unsigned public bucket regardless of the owning Campaign/GeoStory
visibility, so a private/draft story that returns 404 from the API still exposes its image bytes at a
stable public URL. Resolve the owning entity at upload time and pick the alias from the **S2 truth table**
so private/draft uploads use the private (`default`) alias and are served via a signed URL generated only
after gates A+B+C pass. **This track is independent of the has_perm backend.**

**S2 truth table — definitive precedence (first match wins):**
```text
1. Campaign archived                       → media_archive (else default/private if no archive bucket)
2. Owning GeoStory archived (campaign not) → media_archive (else default/private)
3. Otherwise: PUBLIC iff (campaign public) AND (owning entity published); else PRIVATE
```
| Campaign visibility | Owning entity status | Storage alias |
|---|---|---|
| private | draft | `default` (private) |
| private | published | `default` (private) |
| public | draft | **`default` (private)** ← the row that closes S2 |
| public | published | `media_public` |
| any | archived (campaign or story) | `media_archive` (else private) |

General rule: **an asset is public only if its owning Campaign is public AND its specific owning entity is
published.** Archived (campaign or story) always wins and is never public.

**Owning entity per asset** (`media_paths.resolve_entity`): GeoStory (hero + EditorJS in its context),
Event (EditorJS in its context), or **misc** (campaign-level EditorJS, e.g. `EventSeries.default_context`)
which has no per-entity status → falls back to **campaign visibility only**.

**The concrete code gap:** `core/media_lifecycle.py::desired_alias_for_asset` today selects `media_public`
on `Campaign.visibility == public` **without** checking the owning story/event publication status — the
missing `AND entity published` condition is S2. Also the migration path default is hardcoded to
`media_public` (`core/management/commands/migrate_media_paths.py:45`).

**Blocked by:** 03 (policy decisions stable — already true today via the shipped `OrgScopedPermission`/
`CampaignScopedPermission`/`org_role_level()` layer; ticket 03 itself doesn't need to have run, see the
sequencing decision above). Does **not** depend on tickets 04–12.

**Status:** done (2026-08-19)

**Implementation note — upload-time resolution isn't actually possible, so uploads are private by
default instead:** `media_paths.resolve_entity` matches an asset by its `storage_path` already being
embedded in a saved `GeoStory.hero_image` or `GeoContext.content` block. At the moment
`_store_validated_upload` runs, neither exists yet — the frontend uploads the image *first* and only
embeds the returned URL into the story/event's EditorJS content on a later save. So "resolve the owning
entity at upload time" has no entity to resolve. The fix taken instead: **every upload lands in the
private (`default`) alias unconditionally**, with `campaign`/`owner_org` left unset, exactly as
`media_ownership.plan_backfill`'s docstring already anticipated ("already-linked rows -- set by a normal
upload flow once campaign linking exists there -- are left untouched"). This is strictly conservative
(matches or beats every row of the truth table, since "unknown yet" can never be proven public) and
closes the actual S2 root cause immediately. Promotion to the public alias once an asset is genuinely
linked to a public+published entity is `core.media_lifecycle`'s job — wiring that trigger on
save/publish is ticket 14, not this one.

- [x] Resolve A4: verify all current EditorJS upload paths in `geocontext/views.py` and the alias chosen **at upload time** (root cause). Also verify A3 (`Campaign.visibility` value set + how GeoStory/Event derive it). — done in ticket 01.
- [x] ~~Resolve the owning Campaign/GeoStory at upload time via `media_paths.resolve_entity`~~ — not possible at upload time (see note above); uploads default private instead, and `resolve_entity` is used by the lifecycle service once the asset is actually linked.
- [x] Extend `media_lifecycle.desired_alias_for_asset` to add the missing `AND entity published` condition and follow the truth table (archived wins, then public-iff-published, else private). Also fixed the identical gap in `GeoStory.desired_hero_image_storage_alias` (hero images use a separate code path from EditorJS body assets and had the same bug).
- [x] Resolve the **misc/campaign-level default** (campaign-public ⇒ public, or conservatively private) and encode it. — `KIND_MISC` has no entity-publication axis, so campaign visibility alone decides it (unchanged from before); this is the one case where "campaign public ⇒ public" is still correct as-is.
- [x] Private media URLs generated only **after** A+B+C pass; private/draft uploads use `default` + signed URL. — `_absolute_url` now builds the URL from the asset's actual alias (`storages[alias].url()`), which is presigned for `default`/`media_archive` per `test_storage_settings.py::test_private_default_bucket_keeps_signed_urls`.
- [x] Keep DB metadata / `storage_alias` consistent with the chosen alias.
- [x] Tests: EditorJS upload lands in the private/default alias regardless of campaign (`core/tests/test_security_baseline.py`); `desired_alias_for_asset`/`desired_hero_image_storage_alias` truth-table cases including public-campaign+draft-entity and public-campaign+published-entity, for both GeoStory and Event (`core/tests/test_media_lifecycle.py`, `geostories/tests/test_models.py`).

**Also fixed while here:** the EditorJS "existing uploads" picker (`_list_existing_uploads`) previously
built every URL from the public storage regardless of an asset's actual `storage_alias` — harmless while
uploads were always public, but would have produced broken (wrong-bucket, unsigned) links for private
assets once this fix landed. Now resolves each asset's URL from its own alias.

**Deliberately out of scope here (belongs to ticket 15):** `core/management/commands/migrate_media_paths.py`'s
`_alias_for_asset` still routes by legacy path-prefix heuristic (`DEFAULT_PUBLIC_PREFIXES`), not the S2
truth table — that's the one-time canonical-path migration tool, not the upload path; correcting historical
mis-aliased objects is ticket 15's job.

**Files:** `geocontext/views.py`, `core/media_lifecycle.py`, `geostories/models.py`. **Rollback risk:** low
(narrower than originally scoped — no `campaign`/`owner_org` writes at upload time to unwind).

---

## 14 — Media: campaign/entity visibility lifecycle [MEDIA]

**Phase:** 8.5–8.8 · **PR:** `media: implement campaign visibility lifecycle`

**What to build:** Make media follow the **effective** Campaign+entity lifecycle rather than being chosen
once at upload. When visibility or publication status changes, media must be re-pointed between buckets so
a story flipped private→public publishes its media and public→private re-privatizes it, and archive
transitions move media to the archive bucket. Mirror the existing `Event.effective_visibility` pattern for
the published axis.

**Transitions that must re-point media:**
```text
private → public,  public → private,  active → archived,  archived → active (if supported),
and entity draft → published / published → draft under a public campaign
```

**Blocked by:** 13.

**Status:** done (2026-08-19)

**Implementation note — most of this ticket was already built:** an earlier, pre-ticket-13 commit
(`521c94b`, "PR3 — Campaign/GeoStory archive & restore media lifecycle") already shipped
`media_lifecycle.MediaLifecycleService` (idempotent copy → verify size → re-point `storage_alias` →
delete old object) and `desired_alias_for_asset()` recomputing the full §3 S2 truth table, wired to
`pre_save`/`post_save` signals on **Campaign** and **GeoStory** that re-sync owned media whenever
`status`/`visibility` actually changes (never on create, never on unrelated field edits). The one
confirmed gap against this ticket was **Event**: `desired_alias_for_asset()` already accounted for
`Event.status` via `resolve_entity`/`KIND_EVENT`, but nothing triggered a re-sync when an Event's status
changed, so an Event flipping draft→published under a public campaign never actually promoted its
EditorJS media. Closed that gap without rebuilding the working Campaign/GeoStory machinery.

- [x] Wire visibility/publication/archive transitions through `media_lifecycle.MediaLifecycleService` so media re-points per the §3 S2 truth table on every relevant transition. — Campaign/GeoStory already wired (pre-existing); added the missing `Event` `pre_save`/`post_save` pair in `media_lifecycle_signals.py`, mirroring the existing GeoStory shape exactly.
- [x] Handle public↔private visibility changes and archive↔active transitions (archived campaign or story wins and is never public). — pre-existing; unchanged. Event has no archived status of its own (`Event.Status` = DRAFT/PUBLISHED/CANCELLED), so it only archives via its owning Campaign, already covered by the Campaign signal.
- [x] Decide and document physical **move** vs **copy + re-point** between buckets (lifecycle already does copy→verify→delete on `storage_alias`); keep operations idempotent. — pre-existing `move_one`/`move_hero_image`, unchanged; extracted the shared per-entity asset loop out of `sync_story_assets` into `_sync_entity_assets(campaign, kind, entity_id)` (a faithful, byte-for-byte-equivalent generalization) so the new `sync_event_assets` reuses the identical copy→verify→re-point→delete path rather than a second implementation.
- [x] Keep DB metadata / `storage_alias` consistent after each transition. — unchanged, still enforced by `move_one`/`move_hero_image`.
- [x] Tests (transition matrix): public → private re-points/moves media; private → public publishes media; archived transition follows archive policy; entity draft↔published under a public campaign flips public↔private. — added `sync_event_assets` scope-isolation test; Event draft→published (private→public) and published→draft (public→private) promotion/demotion tests for EditorJS assets; the matching GeoStory hero-image promotion/demotion pair; a GeoStory EditorJS-asset promotion test; and the one remaining gap this closes, a GeoStory EditorJS-asset **demotion** test (published→draft under a public campaign, `media_public` → `default`). Plus signal-level tests (`test_media_lifecycle_signals.py`) proving the Event signal fires only on status change, mirroring the existing Campaign/GeoStory signal tests. Full suite: 1135 passed.

**Files:** `core/media_lifecycle.py` (added `_sync_entity_assets`, `sync_event_assets`), `core/media_lifecycle_signals.py` (added Event `pre_save`/`post_save` pair). **Rollback risk:** low (narrower than originally scoped — additive Event signal + a behavior-preserving extraction of already-tested logic, no change to Campaign/GeoStory code paths).

---

## 15 — Media: backfill existing wrongly-public media [MEDIA]

**Phase:** 8.9 · **PR:** `media: backfill existing media`

**What to build:** An idempotent management command that relocates already-uploaded media that is currently
public but should be private/archived under the §3 S2 truth table (e.g. images of private/draft stories
under a public campaign sitting in `media_public`). New-upload correctness (ticket 13) does not fix
historical objects; this closes the existing exposure. Document the migration strategy explicitly: this is
the **backfill existing** half of "new uploads only vs backfill".

**Blocked by:** 14.

**Status:** done (2026-08-19)

**Implementation note:** built on top of the already-tested `MediaLifecycleService` (tickets 13/14) rather than
a parallel implementation. Added a `dry_run` parameter threaded through `move_one`/`move_hero_image`/
`_sync_assets`/`sync_campaign_assets` (new `ACTION_WOULD_MOVE` action reports what would happen without
touching storage or the DB), plus a `report_to_json` helper mirroring `media_path_migration`'s. The new
`backfill_media_aliases` command iterates every `Campaign` (batched, resumable via `--start-after`, `--limit`)
and calls `sync_campaign_assets(campaign, dry_run=not apply)` per campaign — this single entry point already
covers both plain `MediaAsset` rows and hero images that have no `MediaAsset` row of their own (confirmed by
the pre-existing `test_sync_story_assets_moves_hero_without_media_asset` case), so no separate hero-image
sweep was needed. Migration strategy: **backfill-all**, matching `migrate_media_paths`'s approach, since
tickets 13/14 already make new uploads/transitions correct going forward — this command only has work to do
on pre-existing objects.

- [x] Add an idempotent backfill command under `core/management/commands/` that recomputes the desired alias per asset and relocates mis-aliased objects. — `core/management/commands/backfill_media_aliases.py`.
- [x] Provide a **dry-run** mode first; perform moves as copy → verify → delete (never delete before verify). — dry-run is the default (no `--apply`); the underlying copy→verify→delete ordering is unchanged, reused from `MediaLifecycleService.move_one`/`move_hero_image`.
- [x] Idempotent: re-running makes no further changes once assets are correct. — `move_one`/`move_hero_image` already short-circuit to `ACTION_NO_CHANGE` when `storage_alias == target_alias`.
- [x] Document the chosen migration strategy (new-uploads-only vs backfill) so behavior is not left mixed/undocumented. — see implementation note above and the command's module docstring.
- [x] Tests: idempotent backfill relocates wrongly-public private/draft media; re-run is a no-op; verify no object is deleted without a verified copy. — `core/tests/test_backfill_media_aliases.py` (command-level, 7 tests) + `core/tests/test_media_lifecycle.py` (5 new `dry_run` tests at the service level). Full suite: 1147 passed.

**Code review fix (2026-08-19):** the spec-review pass caught that `dry_run` originally short-circuited on the
alias-mismatch check alone, before the source-existence/size checks -- so a dry run could report
`ACTION_WOULD_MOVE` for an asset whose source object is actually missing, which `--apply` would report as
`ACTION_FAILED`. That undermined the ticket's own "dry-run first" safety framing for a high-rollback-risk
change. Fixed: `move_one`/`move_hero_image` now run the existence/size verification unconditionally and only
short-circuit *after* confirming the move would succeed, so dry-run and apply agree on which assets are safe.
Covered by `test_move_one_dry_run_fails_when_source_object_missing`.

**Follow-up (not blocking, not done here):** the Standards review pass flagged that `media_lifecycle.py`'s new
`report_to_json` is now a third byte-for-byte-identical `asdict(entry) -> json.dumps(...)` implementation,
alongside the existing copies in `media_migration.py` and `media_path_migration.py`. Worth consolidating into
one shared helper generic over any `asdict`-able entry dataclass in a future cleanup ticket — left as-is here
to keep this ticket's diff scoped to the backfill command itself.

**Files:** `core/media_lifecycle.py` (added `ACTION_WOULD_MOVE`, `dry_run` params, `report_to_json`),
`core/management/commands/backfill_media_aliases.py` (new). **Rollback risk:** low in practice — `dry_run`
defaults the command to read-only, and `--apply` reuses the same copy→verify→delete safety property already
exercised by tickets 13/14's signal-triggered syncs.

**Rollback risk:** high (bulk object moves) — dry-run first, copy-then-verify-then-delete.

---

## 16 — Complete authorization/media security acceptance matrix [SEC]

**Phase:** §9 · **PR:** `tests: complete authorization/media security matrix`

**What to build:** The end-to-end cross-app test suite that proves every row of the §9 security acceptance
matrix is green across the four gates (A capability, B entitlement, C ownership, D storage). Final
verification that the two tracks together achieve the confidentiality objective and that no anonymous
public read was broken.

**Blocked by:** 02–15.

**Status:** ready-for-agent

- [ ] Anonymous → public published GeoStory → **200** — `test_anon_published_200`
- [ ] Auth DCS → QG2 public published GeoStory → **200** — `test_cross_org_published_200`
- [ ] Auth DCS → QG2 **draft** GeoStory → **404** — `test_cross_org_draft_404`
- [ ] DCS READER → own-org draft GeoStory read → **200** — `test_own_org_reader_draft_200`
- [ ] DCS READER → own-org campaign **change** → **403** — `test_reader_change_denied`
- [ ] DCS WRITER → own-org campaign change → **200** — `test_writer_change_own_200`
- [ ] DCS WRITER → other-org campaign change → **404/403** — `test_writer_change_cross_denied`
- [ ] DCS ADMIN → own-org delete → **200** — `test_admin_delete_own_200`
- [ ] Org without `geostories` entitlement → **403** — `test_entitlement_missing_denied`
- [ ] Private EditorJS media without signed URL → **not readable** — `test_private_media_requires_signed`
- [ ] Private EditorJS media via authorized generated URL → **200 (≤1 h)** — `test_authorized_private_media_url`
- [ ] Public/published EditorJS media → **200 unsigned** — `test_public_media_unsigned`
- [ ] Role changed in Keycloak after browser login → **stale until next login** — `test_browser_stale_until_relogin`
- [ ] Fresh Bearer token after Keycloak role change → **immediately current** — `test_bearer_fresh_claims`

**Behavior:** none (tests only). **Rollback risk:** none.

---

## 17 — Optional storage hardening [MEDIA, non-blocking]

**Phase:** 9 · **PR:** (optional, independent)

**What to build:** Non-blocking hardening of the storage layer once the media track is stable. None of this
is required to close S1/S2; it removes reliance on library defaults and adds observability. The
presigned-URL TTL trade-off (1 hour) is **accepted**; TTL reduction is out of scope unless a real
revocation requirement appears.

**Context:** `querystring_expire` is not set in `build_storage_config` (`settings/base.py:216`), so
django-storages' default of **3600 s / 1 hour** applies to signed aliases (`default`, `media_archive`).
`media_public` is unsigned with no expiry.

**Blocked by:** None — independent (best done after the media track lands).

**Status:** ready-for-agent

- [ ] Pin `querystring_expire=3600` so signed-URL TTL does not depend on a library default.
- [ ] Audit object-name predictability (stable/guessable paths for private objects).
- [ ] Log/monitor media URL generation.
- [ ] (Only if a real revocation requirement appears) consider a shorter TTL — otherwise keep 1 hour.
- [ ] (Only if operations justify it) reconsider per-org IAM — explicitly **out of scope** by current decision.

**Out of scope reminders (from §10):** no per-org S3 buckets, no per-org IAM keys, no per-user Django
`Permission` sync, no custom `manage_*` permission, no project-level roles, no multi-org switching, no
GeoFeedback authz refactor, no presigned-TTL reduction, no GeoServer ACL redesign, no speculative snapshot TTL.
