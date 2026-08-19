# Organization-Scope Bypass & Authorization-Drift — Findings Tickets

**Day:** 2026-08-19
**Scope:** `tosca_api` backend — organization isolation across Django admin, DRF permissions,
and the Keycloak role → capability mapping.
**Source:** cross-check audit of the reader/writer/admin authorization built in
[`2026-08-17-authorization-media-security-tickets.md`](./2026-08-17-authorization-media-security-tickets.md),
triggered by a live incident (below).

GeoFeedback authorization is **out of scope** here — it is a deliberately unfinished module
(open question A8 in the source doc) and is excluded from every ticket below.

---

## Incident that started this (root-cause, proven)

A user who is **not** a DCS member (holds e.g. `DJANGO_STAFF` + `ROLE_HPA_WRITER`, default org
`hpa`) was able to open and edit a **DCS** Workspace's description in the Django admin.

**Proven chain (each link is in code, not inferred):**

```
Keycloak token: {DJANGO_STAFF, ROLE_HPA_WRITER, default_org=hpa}
  → sync_user_permissions_from_roles : is_staff = True        (role_sync.py — from KEYCLOAK_DJANGO_STAFF_ROLES)
  → build_auth_claims                : platform_exempt = roles & ORG_CHECK_EXEMPT_ROLES = True   (role_sync.py:222)
  → sync_snapshot                    : UserAuthorizationSnapshot.platform_exempt = True
  → get_request_org_context (browser): exempt = is_platform_exempt(user) = True   (permissions.py:71)
  → OrgScopedAdminMixin.get_queryset : `if exempt: return qs`  → DCS rows visible  (permissions.py:277)
  → has_change_permission            : is_staff AND has_perm(change_workspace, default_org=hpa) = True
  → WorkspaceAdmin.save_model        : DCS Workspace.description written
```

**Root cause:** `DJANGO_STAFF` conflates two distinct grants that come from two different constants:

| Grant | Source constant | Currently contains |
|---|---|---|
| `is_staff` — may enter the Django admin | `KEYCLOAK_DJANGO_STAFF_ROLES` (settings) | `{DJANGO_STAFF, DJANGO_SUPERADMIN}` |
| `platform_exempt` — bypass **all** org scoping | `ORG_CHECK_EXEMPT_ROLES` (`role_sync.py:12`) | `{DJANGO_SUPERADMIN, DJANGO_STAFF}` |

Because `DJANGO_STAFF` is in **both**, any user who is merely allowed into the admin also becomes a
global cross-org superadmin. There is no "admin access, but scoped to my own org" tier — which is the
tier the product actually wants.

**Intended hierarchy (target):**

- `DJANGO_STAFF` = admin-UI access **only**; still bound to `ROLE_<ORG>_*` for what they can touch.
- `ROLE_<ORG>_READER/WRITER/ADMIN` = real capability inside that org.
- `DJANGO_SUPERADMIN` = global cross-org bypass.

---

## Framing — two independent leak classes

The audit surfaced two mechanically different ways org isolation is bypassed. Fixing one does **not**
fix the other, so they are separate tickets:

- **Class A — `exempt` over-grant.** Everything that flows through `get_request_org_context` /
  `is_platform_exempt`. Closed centrally by narrowing `ORG_CHECK_EXEMPT_ROLES` (ticket 01).
- **Class B — raw `user.is_staff` reads.** Call sites that never consult `exempt` at all and gate on
  the Django `is_staff` column directly. Narrowing `ORG_CHECK_EXEMPT_ROLES` does **nothing** for these;
  each must be converted individually (tickets 02, 03).

A third theme is **registry drift**: `TOSCA_PERMISSION_MODELS` grew well past what the source doc's
gate-A audit covered, creating capability gaps (tickets 04, 05).

---

## Dependency graph

```text
01 Split DJANGO_STAFF: narrow ORG_CHECK_EXEMPT_ROLES          [keystone, Class A]
   ├── 02 Event/EventSeries read scoping → org context         [Class B]
   ├── 03 Geodata admin AJAX endpoints → org-ownership check    [Class B]
   └── 07 Two-org DJANGO_STAFF security acceptance matrix       (blocked by 01, 02, 03)

04 EventSeries DRF capability gate (registry ↔ DRF divergence)  [independent, registry drift]
05 Shared/global model admin exposure decision                 [independent, registry drift]
06 GeodataEngineAdmin duplicate get_queryset dead-code fix      [independent]
08 Decision: geodata DRF API is unmounted — mount or remove     [independent, decision]
```

### Ticket summary

| # | Ticket | Blocked by | Class |
|---|---|---|---|
| 01 | Narrow `ORG_CHECK_EXEMPT_ROLES` to `DJANGO_SUPERADMIN` only | — | A (root cause) |
| 02 | Event/EventSeries read tenant-scoping off raw `is_staff` | 01 | B |
| 03 | Org-scope the geodata admin AJAX endpoints | 01 | B |
| 04 | Close EventSeries DRF write-capability gap | — | drift |
| 05 | Resolve shared/global model admin exposure (EventType/Taxonomy*/GeoContext) | — | drift |
| 06 | Fix `GeodataEngineAdmin` dead org-visibility `get_queryset` | — | structural |
| 07 | Two-org security acceptance matrix for `DJANGO_STAFF` | 01, 02, 03 | verification |
| 08 | Decide fate of the unmounted geodata DRF API | — | decision |

---

## 01 — Narrow `ORG_CHECK_EXEMPT_ROLES` to `DJANGO_SUPERADMIN` only

**What to build:** A `DJANGO_STAFF` user without a global-admin role can enter the Django admin and use
the API, but is scoped to their own organization everywhere — they can no longer see or edit another
org's rows. `DJANGO_SUPERADMIN` (and Django `is_superuser`) remain the only global cross-org bypass.
This is the single central fix for the incident above and for every Class-A call site.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] `ORG_CHECK_EXEMPT_ROLES` no longer contains `DJANGO_STAFF`; only `DJANGO_SUPERADMIN` grants
      `platform_exempt`. `is_staff` derivation (from `KEYCLOAK_DJANGO_STAFF_ROLES`) is left unchanged —
      `DJANGO_STAFF` still opens the admin.
- [ ] Both `exempt` computation points flip together: the Bearer path (`permissions.get_request_org_context`)
      and the browser path (`role_sync.build_auth_claims` → `policy.is_platform_exempt`).
- [ ] Every consumer of `get_request_org_context`/`exempt` now scopes a `DJANGO_STAFF+ROLE_DCS_*` user to
      DCS: `OrgScopedPermission`, `CampaignScopedPermission`, `WorkspaceOwnedScopedPermission`,
      `org_scoped_queryset`, `OrgScopedAdminMixin.get_queryset`, `check_org_level`/`has_org_write_access`,
      `resolve_write_organization`, `validate_campaign_organization`/`validate_workspace_organization`,
      and `GeoStoryViewSet._scope_by_visibility`.
- [ ] Stale docstrings that still describe `DJANGO_STAFF` as exempt are corrected (policy/permissions/role_sync).
- [ ] Existing tests that assert `DJANGO_STAFF`-role → cross-org/exempt are updated to the new contract
      (notably the `feedback` admin's `DJANGO_STAFF`-exempt case and any resolver/permissions tests). The
      test asserting the bare `is_staff`/`is_superuser` **column** does not imply exempt must still pass.
- [ ] Documented behaviour changes verified deliberate: (a) a pure `DJANGO_STAFF` user with **no** org
      role now sees an empty scoped queryset instead of everything; (b) `DJANGO_STAFF` bearer/service
      tokens lose cross-org API reach; (c) an org-less `DJANGO_STAFF` login now emits the existing
      `no_org`/`org_without_role` warnings.

**Note:** This closes the incident (Workspace admin edit) and all Class-A surfaces. It does **not** touch
Class-B raw-`is_staff` sites — see 02/03. The full `DJANGO_STAFF` matrix is only green after 01+02+03.

---

## 02 — Event/EventSeries read tenant-scoping off raw `is_staff`

**What to build:** Reading events follows the same tenant rule as GeoStory. A member sees published/public
events from any org plus their **own** org's drafts/private; only a true platform-exempt role sees other
orgs' unpublished events. A `DJANGO_STAFF+ROLE_HPA_*` user can no longer read DCS's draft/private events
or event series.

**Blocked by:** 01 (so "exempt" means `DJANGO_SUPERADMIN` only, and the shared matrix verifies together).

**Status:** ready-for-agent

- [ ] `EventViewSet` read visibility scoping stops branching on `user.is_staff` and instead uses the org
      context (`exempt` → unscoped; else `published_public` OR own-org), mirroring
      `GeoStoryViewSet._scope_by_visibility`.
- [ ] `EventSeriesViewSet.get_queryset` likewise stops using raw `is_staff`.
- [ ] A member (non-exempt) can read their own org's draft event via the API (parity with GeoStory).
- [ ] A `DJANGO_STAFF+ROLE_DCS_*` user reading events sees DCS drafts but **not** HPA drafts.
- [ ] Anonymous and cross-org published/public reads are unchanged (regression guard).

---

## 03 — Org-scope the geodata admin AJAX endpoints

**What to build:** The workspace/store/layer admin AJAX actions enforce organization ownership, not just
`is_staff`. A staff user scoped to org HPA cannot sync/clone/publish/operate on a DCS-owned
store or layer through these side-channel endpoints.

**Blocked by:** 01.

**Status:** ready-for-agent

- [ ] Every state-changing geodata admin AJAX endpoint that loads an org-owned object by id
      (workspace sync, store actions, layer publish/tables/stores helpers) confirms org ownership via the
      same `has_org_write_access`/org-context gate the workspace visibility-toggle already uses — not a
      bare `is_staff` check.
- [ ] Endpoints that touch **no** org-owned object (stateless helpers, shared-engine actions already
      superuser-gated) are explicitly exempted with a recorded reason, not silently.
- [ ] A cross-org staff user is denied (403) on each org-owned action; the owning org's WRITER+ still
      succeeds without `is_superuser`.

---

## 04 — Close the EventSeries DRF write-capability gap

**What to build:** Writing an EventSeries through the API requires the same WRITER+ capability the admin
already requires. A READER can no longer create or update an event series via the API. The DRF and admin
enforcement for `eventseries` stop disagreeing.

**Blocked by:** None — can start immediately (independent of the exempt work).

**Status:** ready-for-agent

- [ ] `eventseries` is either (a) gated by the model-permission capability class on its viewset so a
      READER is denied writes — matching its presence in `TOSCA_PERMISSION_MODELS` and its admin — or
      (b) removed from `TOSCA_PERMISSION_MODELS` if it is intentionally org-membership-only; pick one and
      make admin + DRF consistent.
- [ ] READER → create/update EventSeries → denied; WRITER+ in the owning org → allowed; cross-org write
      → denied.
- [ ] A permission-matrix test for EventSeries exists (there is none today).

---

## 05 — Resolve shared/global model admin exposure

**What to build:** Decide and enforce who may edit the app's **shared, un-owned** models that are now
role-controlled — `EventType`, `TaxonomyDimension`, `TaxonomyTerm`, and `GeoContext`. Today each is a
plain admin with no org scope, so with the current role→verb map a **WRITER** in **any** org entitled to
that app can `add`/`change` these rows and an **ADMIN** can `delete` them — including global reference
data and content blocks that other orgs' entities reference. This contradicts "each org owns its own
content" and is inconsistent with `GeodataEngine`, which is locked to superuser for change/delete.

**Capability reminder (do not generalize):** the role→verb map is exactly `READER → view`,
`WRITER → view, add, change`, `ADMIN → view, add, change, delete`. State the exposure and any test in
these exact terms (e.g. "a WRITER can `change`", "an ADMIN can `delete`") — never "WRITER+ can
add/change/delete".

**GeoContext ownership is NOT assumed — it was checked and is shareable across orgs.** Verified against
the model/DB: `GeoStory.context` and `GeoFeedback.context` are `OneToOneField`, but `Event.context`
(`related_name="events"`) and `EventSeries.default_context` are **plain `ForeignKey`s** — so one
`GeoContext` row can be referenced by many events, whose campaigns can belong to **different
organizations**. There is no `organization` FK on `GeoContext` and no uniqueness/constraint forcing a
single owner (and the OneToOnes only enforce uniqueness within their own table, not against an Event FK
pointing at the same row). Therefore a single owning org is **undefined** for a shared `GeoContext`, and
org-scoping it by queryset is not even well-formed. Do **not** treat `GeoContext` as transitively owned.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] A decision is recorded per model. For models with **no** ownership path — `EventType`, `Taxonomy*`,
      and (per the verification above) `GeoContext` — the only coherent options are **platform-only**
      (restrict `change`/`delete` to superuser, like `GeodataEngine`) or **accept shared editing** with a
      recorded rationale. True org-scoping is **not** an option for these without a model change.
- [ ] If org ownership of `GeoContext` is genuinely wanted, it is written up as a **separate model-change
      decision** (add an org FK, make `Event.context` non-shared / per-org), not delivered here as a
      queryset scope over a false ownership assumption.
- [ ] Chosen restriction enforced in each admin; the "a WRITER can `change` / an ADMIN can `delete`
      global rows" path is closed, or explicitly and deliberately accepted with a recorded rationale.
- [ ] Tests assert the chosen rule in exact-capability terms (e.g. a non-superuser org ADMIN cannot
      `delete` a global taxonomy term; a non-superuser org WRITER cannot `change` it).

---

## 06 — Fix `GeodataEngineAdmin` dead org-visibility `get_queryset`

**What to build:** The intended per-org engine list scoping actually takes effect. `GeodataEngineAdmin`
currently defines `get_queryset` twice; the second (count-annotation) definition silently overrides the
first (org-visibility) one, so every entitled staff user sees every engine.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] The two `get_queryset` definitions are merged into one that applies **both** the org-visibility
      filter (unrestricted engines + engines allow-listed for the caller's org) **and** the count
      annotations.
- [ ] A non-exempt org-scoped staff user's engine changelist excludes engines restricted to other orgs.
- [ ] Change/delete stay superuser-only (unchanged); this ticket only restores the read/list scoping.

---

## 07 — Two-org security acceptance matrix for `DJANGO_STAFF`

**What to build:** An end-to-end test suite, using two organizations (DCS + HPA), that proves the
intended hierarchy holds across Workspace, Campaign, GeoStory, and Event — so this class of regression
cannot silently return.

**"Cross-org" is not blanket-denial (must agree with ticket 02's visibility policy).** Cross-org
**published/public** reads stay allowed — that is the existing public-read contract, not a leak. What
must be denied cross-org is HPA's **org-private / draft / non-published** content **and every**
write/admin/config operation. Per resource class:

- **Public-read (GeoStory, Event, public Workspace/Layer catalog):** cross-org **published/public read
  → allowed (200)**; cross-org **draft/unpublished read → denied (404)**.
- **Org-private (Campaign, Store, private Workspace content):** **all** cross-org read → denied.
- **Any resource, write/change/delete/admin-config (incl. Workspace description):** cross-org → denied
  for everyone except `DJANGO_SUPERADMIN`.

**Blocked by:** 01, 02, 03.

**Status:** ready-for-agent

- [ ] `DJANGO_STAFF+ROLE_DCS_READER`: DCS read (incl. own-org drafts) ✅; **all** DCS write/delete ❌;
      HPA published/public read ✅; HPA draft/private/org-private read ❌; HPA write/admin ❌.
- [ ] `DJANGO_STAFF+ROLE_DCS_WRITER`: DCS `add`/`change` ✅ (no `delete`); HPA published/public read ✅;
      HPA draft/private read ❌; HPA write/admin ❌.
- [ ] `DJANGO_STAFF+ROLE_DCS_ADMIN`: DCS full CRUD (incl. `delete`) ✅; HPA published/public read ✅;
      HPA draft/private read ❌; HPA write/admin ❌.
- [ ] `DJANGO_SUPERADMIN`: DCS + HPA full access ✅.
- [ ] Regression guards: an anonymous/other-org caller still gets **200** on a published cross-org
      GeoStory/Event (public-read contract preserved), and **404** on a cross-org draft.
- [ ] Coverage spans both the admin path (queryset scope + `has_*_permission`) and the DRF path
      (permission classes + queryset), including Event reads (guards the 02 fix) and Workspace admin
      description edit (guards the incident directly).

---

## 08 — Decide the fate of the unmounted geodata DRF API

**What to build:** A recorded decision (and follow-through) about the `geodata_providers` DRF API —
`WorkspaceViewSet`/`StoreViewSet`/`LayerViewSet`. Its router is **not** included in the root URLconf, so
all the `OrgScopedPermission`/`WorkspaceOwnedScopedPermission` enforcement built in the source doc's
tickets 11/12 is currently unreachable in production; tests exercise it only via direct `.as_view()`.
This also means Workspace management is **admin-only** today, which is exactly why ticket 01's admin
path matters so much.

**Blocked by:** None — this is a decision ticket.

**Status:** done (2026-08-19)

**Decision: quarantine, do not mount.** Auditing the viewsets before mounting surfaced a live gap
that makes mounting unsafe as-is: `GeodataEngineViewSet` (`geodata_providers/api/views.py`) uses plain
`IsAuthenticated` on its default CRUD actions -- only the custom `sync`/`sync_all`/`validate`/`push`
actions are `IsAdminUser`-gated. `GeodataEngine` rows hold admin GeoServer credentials
(`admin_username`/`admin_password`); mounting the router today would let *any* authenticated user
list/create/update/delete engines via the standard `list`/`create`/`update`/`destroy` actions,
regardless of org or staff status -- a worse hole than the one this whole findings doc is about.
`WorkspaceViewSet`/`StoreViewSet`/`LayerViewSet` are correctly org-scoped (ticket 11/12 of the source
doc), but the router registers all four viewsets together, so mounting can't be done piecemeal without
splitting the router.

- [x] Decision recorded: **quarantine** — router stays unmounted. Documented in-code
      (`geodata_providers/api/urls.py` module comment) and in the source doc
      (`2026-08-17-authorization-media-security-tickets.md` ticket 12 annotation) so ticket 11/12's
      "done" status is not mistaken for an active production control.
- [ ] Follow-up (separate ticket, not this one): harden `GeodataEngineViewSet`'s permission_classes
      (capability + superuser-only change/delete, matching `GeodataEngineAdmin`) before mounting is
      revisited.
