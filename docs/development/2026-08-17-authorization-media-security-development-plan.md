# Authorization, Organization Isolation & Media Security — Development Plan

**Date:** 2026-08-17
**Status:** Approved architecture (2026-08-17 review lock-ins incorporated); implementation not started
**Scope:** `tosca_api` backend (Django + DRF), Keycloak-derived authorization, Garage/S3 media confidentiality

> This document is the working roadmap for a substantial refactor. It captures the
> decisions reached in the 2026-08-17 design session, grounded in the current code.
> Where a repository detail was verified, the file/symbol is cited. Where it was not
> fully verified, it is marked **Open Question** — do not fill these from memory.

---

## 1. Executive summary

The work is **two related but distinct tracks** that must not be conflated:

1. **Authorization / organization-isolation refactor** — make Django the *policy
   authority* over Keycloak-assigned roles, using Django-native `has_perm()` for
   model capability and queryset/object scope for tenant isolation.
2. **Media confidentiality / lifecycle fixes** — make private/draft media actually
   unreadable by unauthorized parties at the *storage* layer.

**Critical framing:** fixing Django authorization alone does **not** make private
media confidential. API authorization and object-storage exposure are separate
surfaces. A draft resource can be correctly hidden by the API (`404`) while its
embedded image remains world-readable at a stable, unsigned public-bucket URL.
The two tracks are **two independent failure modes of one confidentiality objective**, not one
leak at two layers: they have distinct root causes and distinct exploit paths (see §3, S1/S2),
and either can be fully closed while the other stays open.

The final architecture is **four orthogonal gates**. None replaces another:

```text
A. Capability
   Keycloak org role → Django view/add/change/delete capability
   "Can this user, in their active org, perform this model/action at all?"

B. Entitlement
   Organization → which TOSCA/Django apps + models it may use
   "Is this app/model even enabled for this organization?"

C. Ownership / tenant isolation
   Queryset + object scope → which organization-owned rows are reachable
   "Does this specific row belong to the caller's organization?"

D. Storage access
   Private media → presigned S3/Garage URL generated only after A+B+C pass
   "Is the bytes-level object reachable only through an authorized, expiring URL?"
```

Why orthogonal:

- **A** is model-level and org-agnostic. Django `has_perm('geostories.view_geostory')`
  has no row/org dimension and the default `ModelBackend.has_perm(user, perm, obj)`
  ignores `obj`. So A can say "READER may view GeoStories" but never "…this QG2 draft".
- **B** is per-organization licensing/entitlement. It gates *whether the app exists*
  for the org, independent of the user's role.
- **C** is the *only* real tenant boundary for reads. It must live in the queryset.
- **D** is the storage layer. Even a perfect A+B+C is defeated if the object sits in
  an unsigned public bucket.

Collapsing any two of these produces either over-grants (B folded into A) or
false-confidence leaks (D assumed from C).

---

## 2. Verified current-state findings

App label reconciliation (verified — important, resolves prior naming confusion):

| Directory | Django `app_label` | Notes |
|---|---|---|
| `tosca_api/apps/authentication/` | **`tosca_authentication`** | Overridden in `authentication/apps.py:7` (`label = "tosca_authentication"`). This is why the app is referred to as `tosca_authentication`. |
| `tosca_api/apps/organizations/` | `organizations` | `Organization` model |
| `tosca_api/apps/campaigns/` | `campaigns` | `Campaign` |
| `tosca_api/apps/geostories/` | `geostories` | `GeoStory` |
| `tosca_api/apps/events/` | `events` | `Event` (+ `EventSeries`, taxonomy, profile tables) |
| `tosca_api/apps/geocontext/` | `geocontext` | `GeoContext` (EditorJS content host) |
| `tosca_api/apps/feedback/` | `feedback` *(Open Question — label not verified in apps.py)* | model is **`GeoFeedback`** (+ `FeedbackSubmission`, `FeedbackLayer`) |
| `tosca_api/apps/geodata_providers/` | `geodata_providers` | `Workspace`, `Layer`, `Store`, `Style`, `LayerGroup`, `GeodataEngine` |
| `tosca_api/apps/core/` | `core` | `MediaAsset` (has `owner_org`, `campaign`, `storage_alias`, `storage_path`) |

Authorization surfaces verified:

- **Live decision path** = `organizations/permissions.py::org_role_level(roles, org_slug)`
  + `LEVEL_RANK = {READER:0, WRITER:1, ADMIN:2}`. Reads **token claims live**; the DB
  is not consulted for decisions.
- **DRF permission classes in use:** `OrgScopedPermission` (Campaign), `CampaignScopedPermission`
  (Event, GeoStory; SAFE methods pass through), plus helpers `check_org_level`,
  `org_scoped_queryset`, `resolve_write_organization`, `has_org_write_access`,
  `validate_campaign_organization`, `get_request_org_context`, `OrgScopedAdminMixin`.
- **`tosca_authentication/permissions.py`** defines `IsSuperAdmin/IsAdmin/IsEditor/IsViewer`
  — **dead code, zero real call sites** (the only `@permission_classes([...])` occurrences
  are inside their own docstrings). `IsEditor/IsViewer` gate on Django groups
  (`groups.filter(name='editor')`) that are **never populated** by this system, so they
  would deny everyone if wired.
- **`KeycloakRole`** (`tosca_authentication/models.py`) is a **catalog only** — populated by
  `role_registry.py` (login upsert + `sync_realm_roles`), consumed **only** by
  `geodata_providers/role_sync.py` (GeoServer role mirroring) and admin. **Not** referenced
  by any authorization decision. `is_active` = soft-deactivation for GeoServer role cleanup,
  not an authz switch.
- **Auth entry points:** `KeycloakTokenAuthentication.authenticate` (`backends.py:47`, Bearer,
  returns `(user, decoded_token)` → `request.auth`) and `KeycloakAdapter.pre_social_login/save_user`
  (browser/allauth). Registered via `REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]`
  (`KeycloakTokenAuthentication`, `TokenAuthentication`, `SessionAuthentication`) and
  `SOCIALACCOUNT_ADAPTER`.
- **`AUTHENTICATION_BACKENDS`** = `[ModelBackend, allauth.AuthenticationBackend]`. Neither
  computes org-scoped perms, so `has_perm()` returns **False** for every non-superuser today.
- **`sync_user_permissions_from_roles`** sets **only** `is_staff`/`is_superuser` — assigns
  **zero** `Permission` rows. The field is clean; nothing to un-sync.
- **`REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] = ["...IsAuthenticated"]`** globally; public
  viewsets override locally.
- **Admin:** `OrgScopedAdminMixin` used by `WorkspaceAdmin` and `MediaAssetAdmin`
  (`org_lookup="owner_org__slug"`); `OrganizationAdmin`, `KeycloakRoleAdmin` exist. **No custom
  `UserAdmin`** is registered — the default Django `UserAdmin` is in use.

### 2.1 Shared S3/Garage model

Verified intentional design (`core/media_paths.py`, `core/models.py::MediaAsset`,
`settings/base.py::build_storage_config`):

```text
shared private bucket
└── orgs/<org_slug>/campaigns/<campaign_id>/{stories/<id>|events/<id>|misc}/<filename>
```

There is **not** one bucket or IAM credential per organization. Storage *aliases*
select bucket/behavior:

- `default` — private, `querystring_auth=True` (signed URLs)
- `media_public` — separate public bucket, `querystring_auth=False` (unsigned, no expiry)
- `media_archive` — archive bucket, `querystring_auth=True` (optional; present only when
  `archive_bucket_name` is configured)

Organization separation is encoded in the **path** and enforced by **Django before URL
generation**.

**Final decision: keep this design. Do not introduce per-org buckets/IAM.** Rationale:

- Lower operational complexity (no per-tenant credential lifecycle).
- Garage/S3 compatibility (single credential set, path-based layout).
- Django is already the policy authority; isolation belongs *before* URL generation.
- Per-org IAM would duplicate a boundary Django already owns, without closing S2.

### 2.2 Presigned URL behavior

Verified flow:

```text
User → Django API
     → queryset / permission / organization checks (gates A+B+C)
     → storage.url()  → presigned URL (signed aliases only)
     → browser fetches Garage/S3 object directly
```

The presigned URL carries **no** org/user context — anyone holding it may use it until
expiry.

Verified TTL: `querystring_expire` is **not set** in `build_storage_config`
(`settings/base.py:216`), so django-storages' default applies: **3600 s / 1 hour**. It
applies to signed aliases (`default`, `media_archive`). `media_public` is **unsigned with
no expiry**.

**Decision: keep 1 hour as an accepted trade-off. Not part of this refactor.**
Optional future hardening: pin `querystring_expire=3600` so behavior does not depend on a
library default (see Phase 9).

---

## 3. Active security findings (documented separately, on purpose)

These are **not** by-products of the refactor. They are first-class security tickets.

### Security Finding S1 — Cross-organization GeoStory retrieve

**Verified behavior** (`geostories/views.py::GeoStoryViewSet`):

- `permission_classes = [IsAuthenticatedOrReadOnly, CampaignScopedPermission]`.
- `CampaignScopedPermission` **passes all SAFE methods** (reads) by design.
- `get_queryset()` restricts to `published()` **only when the user is anonymous**. An
  **authenticated** user's retrieve queryset is the full set with **no org filter**.

Consequence: an authenticated user from org **DCS** can retrieve org **QG2**'s
**draft/archived** GeoStory. Cross-org tenant isolation is missing for these reads.

**Why `has_perm` does not solve this:** a DCS **READER** may legitimately hold
`geostories.view_geostory` (model-level capability, gate A) while still being forbidden
from a QG2 private/draft story (gate C). Capability ≠ ownership. **The real tenant gate for
reads is queryset scoping.**

**Target rule:**

```text
Anonymous:            published/public content only
Authenticated:        published/public content from ANY org
                      + unpublished/private content of the caller's active/default org
Cross-org draft/private/archived: never enters the queryset → retrieve returns 404
```

**Make this an explicit early fix (Phase 1)** — small blast radius, active cross-tenant
confidentiality issue; do **not** wait for the full backend refactor.

**Required regression tests (minimum):**

- cross-org **draft** retrieve → 404
- cross-org **archived** retrieve → 404
- cross-org **published/public** retrieve → 200 (must stay public)
- own-org permitted user retrieves own **draft** → 200
- **list** endpoint excludes cross-org unpublished rows

**Open Question:** confirm `GeoStory` status enum values and the `.published()`
queryset method semantics (draft/published/archived) before writing the scope filter.

### Security Finding S2 — EditorJS media stored publicly

**Verified problem:** EditorJS-embedded media (and hero images) may be stored under
`media_public` regardless of the owning Campaign/GeoStory visibility. The migration path
default is hardcoded (`core/management/commands/migrate_media_paths.py:45` → `"media_public"`),
and the lifecycle alias policy (`core/media_lifecycle.py::desired_alias_for_asset`:
`media_archive` if archived, else `media_public` if `Campaign.visibility` public, else
`default`/private) exists but is a *migration/lifecycle* computation — **Open Question:
verify the alias chosen at initial upload time in `geocontext/views.py`**.

Consequence: API authorization may correctly return `404/403` for a private GeoStory while
the underlying object URL still returns `200`:

```text
GET /geostories/<private-id>/            → 404/403   (API, gate C)
GET <public-bucket>/orgs/.../image.png   → 200        (unsigned public object)
```

**S1 and S2 are two independent failure modes of the same confidentiality objective — not the
same leak.** Track them as separate root causes in security ticketing:

```text
S1 = application/data authorization failure (row reachable via the API queryset)
S2 = object-storage exposure failure        (bytes reachable via an unsigned public URL)
```

They have different exploit paths and either can be closed while the other stays open — closing S1
without S2 still leaks the bytes; closing S2 without S1 still leaks the row's existence/metadata.
**The authorization refactor does not touch storage alias selection and therefore does not close S2.**

**Target media policy — definitive truth table (lock before Phase 8).** An asset's alias is
resolved from its *owning entity* by this precedence (first match wins):

```text
1. Campaign archived                       → media_archive (else default/private if no archive bucket)
2. Owning GeoStory archived (campaign not) → media_archive (else default/private)
3. Otherwise: PUBLIC iff (campaign public) AND (owning entity published); else PRIVATE
```

General rule: **an asset is public only if its owning Campaign is public AND its specific owning
entity is published.** Archived (campaign or story) always wins and is never public.

| Campaign visibility | Owning entity status | Storage alias |
|---|---|---|
| private | draft | `default` (private) |
| private | published | `default` (private) |
| public | draft | **`default` (private)** |
| public | published | `media_public` |
| any | archived (campaign or story) | `media_archive` (else private) |

The `public campaign + draft entity → private` row is the one that closes S2: without it, a story
that is unpublished in the API still exposes public media.

Owning entity per asset (`media_paths.resolve_entity`): GeoStory (hero + EditorJS in its context),
Event (EditorJS in its context), or **misc** (campaign-level EditorJS, e.g.
`EventSeries.default_context`) which has no per-entity status → falls back to **campaign visibility
only**. Events have no ARCHIVED status (`Event.Status`), so Event assets archive only when their
Campaign archives. **Open Question:** confirm the misc/campaign-level default (campaign-public ⇒
public, or conservatively private).

**This requires extending `core/media_lifecycle.py::desired_alias_for_asset`**, which today selects
`media_public` on `Campaign.visibility == public` **without** checking the owning story/event
publication status — that missing `AND entity published` condition is the concrete S2 code gap.

**Lifecycle transitions that must re-point media** (not choose an alias once at upload):

```text
private → public,  public → private,  active → archived,  archived → active (if supported),
and entity draft → published / published → draft under a public campaign
```

Media state must follow the **effective** Campaign+entity lifecycle (`Event.effective_visibility`
already derives visibility from the owning Campaign; mirror that pattern for the published axis).

**Treat as a separate media-layer workstream (Phase 8).**

**Required tests (minimum):**

- EditorJS upload under a private campaign uses the private/default alias
- private/draft media URL is signed
- public + published media uses public alias
- public → private transition re-points/moves media
- private → public transition publishes media
- archived transition follows archive policy

---

## 4. Final authorization architecture

### Layer A — Capability (Django-native `has_perm`)

Keycloak remains the **source of truth for role assignment**. Role levels map to
Django-native permission verbs only:

```text
READER → view
WRITER → view, add, change
ADMIN  → view, add, change, delete
```

- Use only `view_* / add_* / change_* / delete_*`. **No custom `manage_*`** at this stage.
- **No per-user `Permission` rows.** Instead, an **authorization-only backend** computes
  `has_perm()` dynamically from: active org role (A) ∩ org entitlement (B) ∩ role-controlled
  models. Custom permissions (e.g. `publish_*`) are intentionally **excluded** by an
  action-prefix filter.
- Answers *"can this user, given their active org role, perform this model/action at all?"* —
  **not** *"does this row belong to their org?"* (that is C).

### Layer B — Organization entitlement

Each organization explicitly declares which TOSCA apps/models it may use. Use Django-native
app labels + `ContentType` at expansion time; **do not invent an application registry**.

New model (smallest form):

```text
OrganizationAppEntitlement
- organization  (FK → organizations.Organization)
- app_label     (CharField, validated against the single source of truth below)
- unique_together (organization, app_label)
```

**Single source of truth** for both entitleable apps *and* role-controlled models:

```python
TOSCA_PERMISSION_MODELS = {
    "campaigns":         {"campaign"},
    "geostories":        {"geostory"},
    "events":            {"event"},
    "feedback":          {"geofeedback"},          # model is GeoFeedback (Open Q: in scope?)
    "geocontext":        {"geocontext"},
    "geodata_providers": {"workspace"},            # extend if Layer/Store/Style are API-exposed
}
TOSCA_ENTITLEABLE_APPS = set(TOSCA_PERMISSION_MODELS)   # derived, never maintained twice
```

Why the explicit model allow-list: entitling an *app* must **not** auto-expose every future
model in it (revisions, audit logs, background/import jobs). The backend expands perms only
for models in this map, so an internal model added later is not silently role-controlled.

### Layer C — Tenant ownership / organization scope

Row-level isolation stays **out** of `has_perm()`:

```text
queryset            = which rows may even exist for this request   ← primary read boundary
has_perm()          = whether the user has model/action capability  (A ∩ B)
object permission   = whether THIS object belongs to the allowed org/context (writes)
```

For reads, **queryset scoping is the primary tenant-isolation boundary** (see S1). Do not
rely on object permission alone for list/retrieve confidentiality — `CampaignScopedPermission`
deliberately lets SAFE methods pass.

### Layer D — Storage access

Private media is reachable only via a presigned URL generated **after** A+B+C pass; public
published media uses the unsigned public bucket. This is the media-layer track (§3 S2, Phase 8).

---

## 5. Browser/API claim handling (Q11 decision)

### Bearer/API path

- Parse current Keycloak claims from the verified token.
- Build normalized `org_roles` + `default_org`, attach **request-local** to the user object
  (e.g. `user._auth_claims`).
- **Do not persist** API-token claims to the login snapshot.
- Freshest source; always current with the presented token.

### Browser/admin path

- Do **not** decode a stored (possibly expired) ID token on every later session request.
- Persist a dedicated snapshot at successful browser login. **Do not** write normalized state
  into `SocialAccount.extra_data`.

New model:

```text
UserAuthorizationSnapshot
- user        (OneToOne → AUTH_USER_MODEL)
- org_roles   (JSONField)      # {"dcs": "ADMIN", "qg2": "WRITER"}
- default_org (CharField)
- synced_at   (DateTimeField)
```

**Multi-org-ready now, single-org enforced now:** store all org roles the token carries, but
authorization for the PoC uses **only `org_roles[default_org]`**. No org-switching / multi-org
request context yet. The multi-org storage shape exists solely to avoid a later migration.

### Snapshot write rule (respect authoritative extraction)

```text
roles authoritative + non-empty   → write/update snapshot
roles authoritative + empty       → write empty snapshot   (Keycloak really returned none)
roles non-authoritative / missing → DO NOT overwrite previous snapshot
```

Rationale: a missing mapper/claim (`ExtractedRoles.authoritative == False`) must not silently
destroy a previously valid snapshot — mirrors the existing demotion guard in
`sync_user_permissions_from_roles`.

### Authorization-source precedence (one uniform resolver)

```text
1. current request-local live claims (user._auth_claims)   ← Bearer, and the login request itself
2. persisted UserAuthorizationSnapshot                     ← later browser/admin requests
3. no permissions / FAIL CLOSED
```

- No implicit decoding of stale ID tokens in the normal path.
- **No hidden in-memory "last known good" fallback.** If neither 1 nor a valid 2 exists → empty
  perms.
- **No snapshot TTL now**, but leave a clean seam (`_load_valid_snapshot`) for one later.

**Accepted trade-off (document it):** browser/admin authorization may be **stale until the next
login** after a Keycloak role change; API requests are always fresh per their Bearer token.
Expose `synced_at` / "last synced at login" in the admin effective-permissions view.

**Demotion is the sharp edge, not promotion.** A user *demoted* in Keycloak (e.g. ADMIN → READER,
or removed from an org) keeps the higher browser/admin capability until their next login. Document
the operational remedy explicitly: **to revoke immediately, force a logout / clear the user's
`UserAuthorizationSnapshot`** — deleting the row makes the resolver fall through to fail-closed on
the next request. Leave a clean **invalidation seam** now — e.g. `policy.invalidate_snapshot(user)`
plus snapshot invalidation on Django logout — even though automatic Keycloak-driven invalidation is
out of scope for the PoC. Promotion staleness (READER → ADMIN not yet visible) is a usability
annoyance; **demotion staleness is a security window**, so the invalidation hook is a security
requirement, not a nicety.

**Open Question:** token claim shape for multi-org. Verified today: `role_sync._org_slug_from_payload`
handles both a scalar `default_organization` and a list `organization` claim (takes first). Confirm
whether multiple `ROLE_<ORG>_<LEVEL>` for different orgs actually appear in one token before relying
on multi-org `org_roles` being populated.

---

## 6. Public-read DRF behavior (do not reintroduce the mistake)

Plain `DjangoModelPermissions` **cannot** preserve anonymous public GET: it sets
`authenticated_users_only = True`, so an empty GET perm list still rejects anonymous users.
DRF permission classes are **AND-composed**, so adding it beside `IsAuthenticatedOrReadOnly`
would still block anonymous reads.

Per-resource matrix:

```text
Org-private resources (Campaign, geodata management):
    ViewGatedModelPermissions      # subclass: GET/HEAD → view_<model>
    + OrgScopedPermission          # C: org/object scope only

Public Event/GeoStory:
    DjangoModelPermissionsOrAnonReadOnly   # anon GET allowed; writes need add/change/delete
    + CampaignScopedPermission             # C: org/object scope only (writes)

Public catalog endpoints (catalog_api Workspace/Layer list):
    retain existing AllowAny — GeoServer ACL is the authority for layer visibility
```

`ViewGatedModelPermissions` (for private reads):

```python
class ViewGatedModelPermissions(DjangoModelPermissions):
    perms_map = {
        **DjangoModelPermissions.perms_map,
        "GET":  ["%(app_label)s.view_%(model_name)s"],
        "HEAD": ["%(app_label)s.view_%(model_name)s"],
    }
```

**Do not** introduce a blanket global DRF default until each resource's intended public/private
behavior is verified resource-by-resource.

---

## 7. Numbered development sequence

Order is adjusted to the verified repo. Security fixes are marked **[SEC]**, architecture
**[ARCH]**, media **[MEDIA]**.

### Phase 0 — Baseline & regression tests **[SEC/ARCH]**

1. Add failing tests reproducing **S1** (cross-org GeoStory draft/archived retrieve).
2. Add tests characterizing **S2** where feasible (private-campaign EditorJS upload alias;
   signed-vs-unsigned URL of resulting object).
3. Record current permission behavior for Campaign/Event/GeoStory/Workspace (golden snapshot).
4. Verify actual app labels + model names (mostly done in §2; confirm `feedback` label,
   `GeoStory.status`, `Campaign.visibility` value sets).
5. Confirm `tosca_authentication/permissions.py` classes have zero call sites (verified;
   re-assert in CI grep).

Purpose: safety net before behavior changes.

### Phase 1 — GeoStory tenant-isolation hotfix (S1) **[SEC]**

Standalone, do not wait for the backend refactor. Update
`GeoStoryViewSet.get_queryset()` so unpublished/private rows are visible only for the caller's
org while published/public rows stay public (rule in §3 S1). Add all S1 regression tests.

Early because: active cross-tenant confidentiality issue, small blast radius.

### Phase 2 — Authorization foundation (additive) **[ARCH]**

1. Delete confirmed-dead `tosca_authentication/permissions.py`.
2. Add `organizations/policy.py` (`LEVEL_ACTIONS`, `user_claims`, `enabled_apps_for`).
3. Add `TOSCA_PERMISSION_MODELS` + derived `TOSCA_ENTITLEABLE_APPS` to settings.
4. Add `OrganizationAppEntitlement` (+ validator, + migration).
5. Data migration: seed existing organizations with their current expected entitlements
   (all in-scope apps) so nothing loses access on deploy.
   **Acceptance criterion (hard gate): after deployment, no existing organization loses access
   due to entitlement.** Start entitlement enforcement behind a feature flag or with an
   all-entitled default, so the authorization refactor does not silently double as a product-
   licensing rollout. Flip real per-org entitlement restrictions only as a separate, deliberate
   product decision.
6. Add `UserAuthorizationSnapshot` (+ migration).
7. Admin: entitlement inline on `OrganizationAdmin`; read-only snapshot visibility where useful.

Additive: nothing reads the new backend yet.

### Phase 3 — Claim normalization & snapshot sync **[ARCH]**

1. Normalize Keycloak roles → multi-org `org_roles`, highest level per org
   (reuse `parse_role_name` + `org_role_level`).
2. Determine `default_org`.
3. Browser login (`KeycloakAdapter`): attach `user._auth_claims`; persist snapshot **only if
   authoritative** (per §5 write rule).
4. Bearer (`KeycloakTokenAuthentication`): attach `user._auth_claims`; **never** mutate snapshot.
5. Implement the unified resolver (precedence in §5).

Tests: multiple org roles; highest-level selection; default-org lookup; authoritative-empty;
non-authoritative-missing (no overwrite); expired/missing stored ID token; **new browser request
after login**; Bearer overriding stored snapshot.

### Phase 4 — Dynamic authorization backend **[ARCH]**

Implement `organizations/auth_backend.py::OrgRolePermissionBackend(BaseBackend)`:

```text
active/default org → org role → allowed CRUD actions
→ entitled apps (B) → role-controlled models (TOSCA_PERMISSION_MODELS)
→ Permission codenames (view/add/change/delete only; custom perms filtered out)
```

`authenticate()` returns `None`; register in `AUTHENTICATION_BACKENDS`
(`[ModelBackend, OrgRolePermissionBackend, allauth...]`). No per-user rows.

Tests: READER / WRITER / ADMIN; app not entitled; model not in allow-list; no snapshot &
no live claims (fail closed); superuser; inactive user.

### Phase 5 — Django admin integration **[ARCH]**

Let admin use the now-meaningful `has_perm()`:

```text
is_staff            = may enter admin
has_perm()          = which models/actions
admin queryset scope= which organization rows
```

Simplify `OrgScopedAdminMixin` to row-scoping (`get_queryset`) + standard `has_*_permission`;
drop its duplicated role/action capability logic. **Register a custom `UserAdmin`** (none exists
today) with a read-only effective-permissions panel: current/default org, org role(s), entitled
apps, computed `get_all_permissions()`, and `synced_at`.

### Phase 6 — DRF enforcement refactor **[ARCH]**

Strip the action→level ladders from `OrgScopedPermission` / `CampaignScopedPermission`; they keep
**only** org/object scope + queryset scoping. Add the correct model-permission class per resource
(§6 matrix). Resource-by-resource, **not** a global switch. Verify at least: Campaign, GeoStory,
Event, Workspace/geodata management, and any other campaign-owned resource found in the repo. Add
a permission-matrix test suite. **GeoFeedback stays out of scope** unless the project decides
otherwise (see §10 + Open Question).

### Phase 7 — Workspace/geodata authorization cleanup **[ARCH]**

Replace `IsAdminUser` in `geodata_providers/api/views.py` where an org **ADMIN** should manage
their own org's resources: enforce `model capability (A) + organization ownership (C)`. Verify
interaction with GeoServer/Keycloak role sync (`geodata_providers/role_sync.py`,
`security_sync.py`). **Do not** move GeoServer ACL authorization into Django `has_perm()` —
document the boundary: Django owns *what roles mean*; GeoServer ACL owns *layer-level visibility*.

### Phase 8 — Media confidentiality fix (S2) **[MEDIA]**

Separate but coordinated workstream:

1. Verify all EditorJS upload paths (`geocontext/views.py`) — **Open Question: current upload alias.**
2. Resolve owning Campaign/GeoStory at upload time (`media_paths.resolve_entity`).
3. Select private/public alias per the **§3 S2 truth table** (public iff campaign public AND
   owning entity published; archived wins), not once-at-upload. Concretely, extend
   `media_lifecycle.desired_alias_for_asset` to add the missing `AND entity published` condition.
4. Generate private media URLs only after A+B+C pass.
5. Handle public/private visibility changes; 6. handle archive transitions
   (`media_lifecycle.MediaLifecycleService`).
7. Decide physical **move** vs **copy + re-point** between buckets (lifecycle already does
   copy→verify→delete on `storage_alias`).
8. Keep DB metadata / `storage_alias` consistent.
9. Idempotent backfill for existing wrongly-public private/draft media.
10. Regression/security tests (§3 S2 list).

Document the migration strategy explicitly: **new uploads only** vs **backfill existing** — do not
leave mixed behavior undocumented.

### Phase 9 — Optional storage hardening **[MEDIA, non-blocking]**

Pin `querystring_expire=3600`; audit object-name predictability; log/monitor media URL
generation; consider shorter TTL only if a real revocation requirement appears; per-org IAM only
if operations justify it.

---

## 8. Implementation dependency graph

```text
Phase 0  Baseline tests
   ├── Phase 1  S1 GeoStory queryset hotfix        [SEC, independent, ship first]
   └── Phase 2  Authorization foundation (additive)
          └── Phase 3  Claim normalization & snapshot
                 └── Phase 4  Dynamic has_perm backend
                        ├── Phase 5  Admin integration
                        └── Phase 6  DRF enforcement refactor
                               └── Phase 7  Workspace/geodata authz cleanup

Phase 8  Media confidentiality (S2)   [MEDIA]
   ├── may start once §3/§4 policy decisions are stable (needs Campaign/GeoStory
   │   visibility+lifecycle semantics, not the has_perm backend)
   └── does NOT depend on Phases 3–7

Phase 9  Optional storage hardening    [independent]
```

Key point: **the media fix (Phase 8) can proceed independently of most authorization backend
work.** Phase 1 (S1) is independent and should ship first.

---

## 9. Security acceptance matrix

| Scenario | Expected | Enforcement layer(s) | Test |
|---|---|---|---|
| Anonymous → public published GeoStory | 200 | queryset (published) | `test_anon_published_200` |
| Auth DCS → QG2 public published GeoStory | 200 | queryset (published, cross-org allowed) | `test_cross_org_published_200` |
| Auth DCS → QG2 **draft** GeoStory | **404** | **queryset scope (C)** | `test_cross_org_draft_404` |
| DCS READER → own-org draft GeoStory read | 200 | queryset (own org) + `view` (A) | `test_own_org_reader_draft_200` |
| DCS READER → own-org campaign **change** | 403 | `has_perm` add/change absent (A) | `test_reader_change_denied` |
| DCS WRITER → own-org campaign change | 200 | `change` (A) + org scope (C) | `test_writer_change_own_200` |
| DCS WRITER → other-org campaign change | 404/403 | queryset/object scope (C) | `test_writer_change_cross_denied` |
| DCS ADMIN → own-org delete | 200 | `delete` (A) + org scope (C) | `test_admin_delete_own_200` |
| Org without `geostories` entitlement → geostories access | 403 | entitlement (B) → no perm | `test_entitlement_missing_denied` |
| Private EditorJS media without signed URL | not readable | storage policy (D) | `test_private_media_requires_signed` |
| Private EditorJS media via authorized generated URL | 200 (≤1 h) | A+B+C then D | `test_authorized_private_media_url` |
| Public/published EditorJS media | 200 unsigned | storage policy (D, public alias) | `test_public_media_unsigned` |
| Role changed in Keycloak after browser login | stale until next login | snapshot precedence (§5) | `test_browser_stale_until_relogin` |
| Fresh Bearer token after Keycloak role change | immediately current | live claims (§5) | `test_bearer_fresh_claims` |

---

## 10. Out of scope (this plan)

Unless the codebase forces otherwise:

- No per-org S3 buckets.
- No per-org IAM keys/policies.
- No per-user Django `Permission` synchronization.
- No custom `manage_*` permission.
- No project-level roles (`ROLE_<ORG>_<PROJECT>_<LEVEL>` is parsed into the catalog but
  `org_role_level` ignores `project`; project-scoped capability would be a *fourth* gate).
- No multi-org switching UI / request context (storage shape is multi-org-ready; logic is not).
- No GeoFeedback authorization refactor (**Open Question:** confirm this remains the decision;
  `feedback/views.py` currently uses a local `IsAdminOrReadOnly`, not org scope).
- No presigned-URL TTL reduction.
- No GeoServer ACL redesign (Django owns role meaning; GeoServer owns layer visibility).
- No speculative snapshot TTL.

---

## 11. Recommended PR / commit breakdown

Small, reviewable units. Each PR: **goal · main files · dependencies · behavior change ·
tests · rollback risk.**

1. **`security: add tenant-isolation regression tests`**
   - Goal: capture S1 (+ S2 where feasible) as failing tests. Files:
     `geostories/tests/`, `core/tests/`. Deps: none. Behavior: none (tests only).
     Tests: S1 list. Rollback risk: none.
2. **`security: scope unpublished geostories by organization`** *(S1 fix — Phase 1)*
   - Files: `geostories/views.py`. Deps: PR1. Behavior: cross-org draft/archived → 404;
     published stays public. Tests: S1 turns green. Rollback risk: low (single `get_queryset`);
     watch for legitimate cross-org published reads.
3. **`authz: add organization entitlement and policy model`** *(Phase 2.1–2.5)*
   - Files: `organizations/models.py`, `organizations/policy.py`, `settings/base.py`,
     migration, `organizations/admin.py`. Deps: PR1. Behavior: additive (nothing enforces yet).
     Tests: entitlement validation, seed migration. Rollback risk: low (additive schema).
   - Also removes dead `tosca_authentication/permissions.py`.
4. **`authz: add authorization snapshot`** *(Phase 2.6–2.7)*
   - Files: `organizations/models.py` (or `tosca_authentication/`), migration, admin.
     Deps: PR3. Behavior: additive. Tests: model + admin. Rollback risk: low.
5. **`authz: normalize keycloak claims and snapshot sync`** *(Phase 3)*
   - Files: `tosca_authentication/backends.py`, `tosca_authentication/role_sync.py`,
     `organizations/policy.py`. Deps: PR4. Behavior: claims attached; snapshot written on
     browser login (authoritative-guarded). Tests: §Phase 3 list. Rollback risk: medium
     (login path) — guard with authoritative rule; feature-flag if needed.
6. **`authz: add dynamic django permission backend`** *(Phase 4)*
   - Files: `organizations/auth_backend.py`, `settings/base.py` (`AUTHENTICATION_BACKENDS`).
     Deps: PR5. Behavior: `has_perm()` becomes meaningful for non-superusers. Tests: §Phase 4.
     Rollback risk: medium — removing the backend restores prior (all-False) behavior.
7. **`authz: migrate admin authorization`** *(Phase 5)*
   - Files: `organizations/permissions.py` (slim `OrgScopedAdminMixin`), custom `UserAdmin`,
     admin modules. Deps: PR6. Behavior: admin menus/actions driven by `has_perm`; effective-
     perms panel. Tests: admin visibility per role/org. Rollback risk: medium — admin access
     regressions; keep `is_staff` gate intact.
8. **`authz: migrate DRF permission enforcement`** *(Phase 6)* — **highest-risk; split per
   resource, each merged with its own permission matrix. Do NOT ship as one PR.**
   Deps: PR6. Shared behavior: model-perm gate + slimmed scope classes per §6 matrix;
   **anonymous public GET must stay 200**. Rollback risk: high — public/private regressions.
   - **8a** `authz: migrate Campaign DRF permissions` — `organizations/permissions.py`,
     `campaigns/views.py` (org-private matrix).
   - **8b** `authz: migrate GeoStory DRF permissions` — `geostories/views.py` (public-read
     matrix; interacts with the Phase 1 S1 queryset fix — verify both together).
   - **8c** `authz: migrate Event DRF permissions` — `events/views.py` (public-read matrix;
     incl. the second viewset, Open Question A6).
   - **8d** `authz: migrate remaining campaign-owned resources` — any others found in the repo.
9. **`authz: fix workspace organization authorization`** *(Phase 7)*
   - Files: `geodata_providers/api/views.py`. Deps: PR8. Behavior: org ADMIN manages own-org
     workspaces without Django `is_staff`. Tests: own-org vs cross-org management. Rollback
     risk: medium — verify GeoServer sync unaffected.
10. **`media: enforce private editorjs uploads`** *(Phase 8.1–8.4)*
    - Files: `geocontext/views.py`, `core/media_paths.py`, `core/media_lifecycle.py`. Deps:
      policy stable (PR2–3). Behavior: private/draft uploads → private alias + signed URL.
      Tests: S2 upload/signing. Rollback risk: medium — media URL breakage; verify existing
      public assets unaffected.
11. **`media: implement campaign visibility lifecycle`** *(Phase 8.5–8.8)*
    - Files: `core/media_lifecycle.py`, signals. Deps: PR10. Behavior: media re-points on
      visibility/archive transitions. Tests: transition matrix. Rollback risk: medium
      (object moves) — idempotent copy→verify→delete.
12. **`media: backfill existing media`** *(Phase 8.9)*
    - Files: `core/management/commands/`. Deps: PR11. Behavior: existing wrongly-public
      private/draft media relocated. Tests: idempotent backfill. Rollback risk: high (bulk
      object moves) — dry-run first, copy-then-verify-then-delete.
13. **`tests: complete authorization/media security matrix`** *(§9)*
    - Files: cross-app tests. Deps: PR2–12. Behavior: none. Tests: full §9 matrix green.
      Rollback risk: none.

---

## Appendix — Open Questions to resolve before/at implementation

- **A1** `feedback` app `label` (assumed `feedback`; not verified in `feedback/apps.py`).
- **A2** `GeoStory.status` enum values + `.published()` semantics (needed for S1 filter).
- **A3** `Campaign.visibility` value set (public/private/…) and how `GeoStory`/`Event` derive it.
- **A4** Current EditorJS **upload-time** alias selection in `geocontext/views.py` (S2 root cause).
- **A5** `core/permissions.py` (one class, not read) — identify before touching.
- **A6** `events/views.py:299` second viewset (`IsAuthenticatedOrReadOnly`) — identity + whether
  it is a write gap.
- **A7** Whether one Keycloak token actually carries roles for multiple orgs (multi-org
  `org_roles` population).
- **A8** GeoFeedback scope decision (currently out of scope; `feedback/views.py` uses local
  `IsAdminOrReadOnly`).
- **A9** Whether `geodata_providers` API-exposed models beyond `Workspace` (Layer/Store/Style)
  should be role-controlled in `TOSCA_PERMISSION_MODELS`.
