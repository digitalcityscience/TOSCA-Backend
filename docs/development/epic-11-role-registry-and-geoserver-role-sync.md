# Epic-11 — Keycloak Role Registry & GeoServer Role-Service Sync

> **Status:** Design agreed, ready for implementation.
> **Date:** 2026-08-13
> **Branch context:** `epic-11-org-scoping-and-workspace-visibility`
> **Audience:** the implementing agent/developer. This document is the single
> source of truth for *why* and *what*. Read it fully before writing code.

---

## 1. Problem statement (the bug that started this)

GeoServer data-security ACL rules are created correctly by Django and
**enforcement works**. But the roles they reference are **not declared in
GeoServer's role service**. Symptom in the GeoServer UI:

- In the **ACL list** the rule shows up (e.g. `dcs.*.r → ROLE_DCS_READER`).
- Opening the rule, the **"selected roles" list is empty** — because
  `ROLE_DCS_READER` does not exist under the active *Role Service*.

**Root cause (confirmed):** GeoServer ACL rules are plain string matches
against the authenticated user's granted authorities, so access control still
functions. The role, however, was never registered in GeoServer's role
registry, so it is not selectable/visible in the Role Service UI. This is a
**manageability / first-class-registration** gap, not an enforcement gap.

---

## 2. How things work today (verified facts)

### 2.1 Django side
- **There is no Role model / role table.** Keycloak is the single source of
  truth. Roles are read from the JWT at login (`realm_access.roles`), used to
  set `is_staff` / `is_superuser` and the org level, then discarded. Nothing is
  persisted.
- Role names are **derived properties** on `Organization`
  (`tosca_api/apps/organizations/models.py`), never stored as columns:
  ```python
  role_prefix  → f"ROLE_{self.slug.upper()}"   # ROLE_DCS
  reader_role  → f"{role_prefix}_READER"       # ROLE_DCS_READER
  writer_role  → f"{role_prefix}_WRITER"       # ROLE_DCS_WRITER
  admin_role   → f"{role_prefix}_ADMIN"        # ROLE_DCS_ADMIN
  ```
  The only stored field is `Organization.slug` (`dcs`, `gq2`, ...).
- Keycloak→Django role mapping lives in
  `tosca_api/apps/authentication/role_sync.py`
  (`extract_roles_from_token`, `org_role_level`, `sync_user_permissions_from_roles`).
- **ACL push:** `GeoServerSecuritySyncService.sync()`
  (`tosca_api/apps/geodata_providers/security_sync.py`) runs on `Workspace`
  save (signals in `geodata_providers/signals.py`, wrapped in
  `transaction.atomic()` so a failed ACL push rolls back the workspace).
  `_rule_map()` produces **only two** rules and references **only**
  `reader_role` + `writer_role`:
  ```
  f"{ws}.*.r" → "*" if PUBLIC else org.reader_role
  f"{ws}.*.w" → org.writer_role
  ```
- **GeoServer REST client:** `GeoServerClient`
  (`tosca_api/apps/geodata_providers/geoserver/client.py`) wraps workspaces,
  stores, layers and the ACL endpoint `/rest/security/acl/layers`. It has
  **no** role-service endpoints and never calls `/rest/security/roles`.
- No Celery. All sync is synchronous.

### 2.2 GeoServer side (`docker/geoserver_docker`)
- Primary role service is **`jdbc_role`** (`JDBCRoleService`) →
  Postgres schema **`gs_auth_role_schema`**, table
  **`roles(name varchar(64) PK, parent varchar(64))`** (+ `role_props`,
  `user_roles`, `group_roles`). Config:
  `docker/geoserver-init/jdbc_role_service/jdbc_role/config.xml`.
- **BUT** with the sample-env defaults (`GEOSERVER_SECURITY_MODE=default`,
  JDBC flags off, startup bootstrap skipped) GeoServer runs on the built-in
  **`default`** XML role service until an operator activates JDBC. So Django
  must **not** assume which role service is active.
- Roles are currently seeded only via `psql`/bootstrap scripts
  (`ADMIN`, `GROUP_ADMIN`, extras). Nothing uses the `/rest/security/roles`
  REST endpoint today.
- The active role service is whatever `security/config.xml`'s
  `<roleServiceName>` points to.

### 2.3 Keycloak side
- `KEYCLOAK_SERVER_URL=https://auth2.dcs.hcu-hamburg.de/`, realm `tosca-dev`,
  login client `django-dev` (+ `KEYCLOAK_CLIENT_SECRET`). Settings in
  `tosca_api/settings/base.py`.
- `.env.example` also carries `KEYCLOAK_ADMIN_USER` / `KEYCLOAK_ADMIN_PASSWORD`
  placeholders (password-grant fallback).

---

## 3. Decisions taken this session (in order)

1. **Do we need a persisted role store at all?**
   For the *current* auto-ACL flow, no — role names are deterministically
   derived from `Organization.slug`. **But** for the roadmap goal (letting users
   pick roles at **event / workspace / layer** granularity) we need a
   **selectable role pool (catalog)**. You cannot feed a dropdown from Python
   properties. → **Build a persisted `KeycloakRole` registry.**

2. **How is the registry populated?**
   - **Opportunistic (login-triggered):** on each login, upsert every
     `ROLE_`-prefixed token role into `KeycloakRole`. Zero extra requests, rides
     the existing login path. **Limitation:** a role is only seen once a user
     carrying it logs in.
   - **Authoritative (Keycloak Admin API):** a `sync_keycloak_roles`
     management command / admin-panel button pulls the *full* realm role list,
     including free roles nobody has logged in with yet.
   → **Do both.** Login-triggered keeps it fresh cheaply; the Admin API sync
     gives the complete list on demand.

3. **What auth does the Admin API sync need?** (this was a key worry)
   - Django superadmin status is **irrelevant** — a Django admin session carries
     no Keycloak permission. The backend needs its **own machine identity.**
   - **Chosen (clean) path:** enable *Service Accounts* on the existing
     `django-dev` client and grant its service account the `realm-management`
     role **`view-realm`**. Then `client_credentials` grant with the **existing
     `KEYCLOAK_CLIENT_SECRET`** works — **no extra username/password.**
   - Fallback: `KEYCLOAK_ADMIN_USER/PASSWORD` password grant (works but worse:
     human account, rotation/MFA issues). Avoid for permanent use.
   → **Use client_credentials on `django-dev` + `view-realm`.**

4. **✅ VERIFIED LIVE (see §5):** the operator enabled Service Accounts +
   `view-realm`; the probe successfully obtained a token and listed realm roles
   with the existing secret. No extra credentials required.

5. **Which roles are "selectable"?** Filter to **`ROLE_`-prefixed** roles.
   This naturally excludes Keycloak system roles (`offline_access`,
   `uma_authorization`, `default-roles-*`) and platform roles
   (`DJANGO_STAFF`, `DJANGO_SUPERADMIN`, `ADMIN`).

6. **GeoServer write mechanism (for the later sync phase):** use the GeoServer
   **REST endpoint** `POST /rest/security/roles/role/{roleName}` via the
   existing `GeoServerClient._request()`, **not** direct SQL.
   Rationale: REST writes to *whichever role service is currently active*
   (`jdbc_role` if activated, else `default`) and refreshes GeoServer's cache.
   This implements the operator's "put it in JDBC if it exists, otherwise the
   default role service" intent **without Django knowing the schema or which
   service is active.** Direct SQL would hard-code `gs_auth_role_schema`, assume
   `jdbc_role` is active, and need a separate reload.

7. **Which roles to mirror into GeoServer per org:** **reader + writer +
   admin** (the full triad), even though ACLs reference only reader+writer
   today. Completing the registry is cheap (idempotent) and lets operators
   hand-build admin ACLs in the UI.

8. **Overall GeoServer-sync approach:** **dedicated, decoupled role
   reconciliation** — NOT inline-per-ACL-push. The existing auto-ACL flow stays
   exactly as it is. Roles are ensured separately (hooked at org
   materialization + a backfill command), so we never hit the role table on
   every request.

### 3.1 Agreed sequencing
> **Phase 1 first: the `KeycloakRole` registry table + population.**
> **Phase 2 next: GeoServer `default`/`jdbc_role` matching (push registry roles
> into the active GeoServer role service).**

---

## 4. Implementation plan

### Phase 1 — Keycloak role registry (do this first)

**Goal:** a persisted, self-growing catalog of Keycloak roles that later UIs
(event/workspace/layer role pickers) and Phase 2 can consume.

1. **Model `KeycloakRole`** (suggested app: `authentication`, or a small new
   `roles` app — implementer's call, keep it near `role_sync.py`):
   - `name` — `CharField(unique=True)` (e.g. `ROLE_DCS_READER`)
   - `organization` — nullable FK to `Organization` (link when the slug segment
     matches a known org; leave null for free roles like `kose-rol-test`)
   - `source` — enum/char: `login` | `keycloak_admin` (how it was first seen)
   - `is_active` — `BooleanField(default=True)` (for soft-deactivation when a
     role disappears from Keycloak)
   - `first_seen_at`, `last_seen_at` — timestamps
   - Register in Django admin (list, search by name, filter by org/source).
2. **Login-triggered upsert:** in `role_sync.py` where token roles are already
   extracted, upsert each `ROLE_`-prefixed role
   (`update_or_create(name=..., defaults={last_seen_at: now, ...})`). Must be
   non-blocking / best-effort — never fail a login because the registry write
   failed (wrap in try/except + log).
3. **Keycloak Admin API client** (small helper, e.g.
   `authentication/keycloak_admin.py`):
   - `get_admin_token()` → `client_credentials` grant using
     `KEYCLOAK_CLIENT_ID` + `KEYCLOAK_CLIENT_SECRET`.
   - `list_realm_roles()` → `GET /admin/realms/{realm}/roles?briefRepresentation=true&max=1000`.
   - Reuse the exact endpoints proven in §5.
4. **`sync_keycloak_roles` management command:**
   - Pull full realm role list, filter to `ROLE_` prefix, upsert into
     `KeycloakRole`, link `organization` where the slug segment resolves.
   - Deactivate (`is_active=False`) roles no longer present (don't hard-delete —
     ACL history may reference them).
   - Support `--dry-run`.
   - Optionally surface as an admin-panel button that triggers the same code.
5. **Tests:** upsert idempotency; login path best-effort behavior;
   Admin-API client with mocked HTTP; command dry-run vs apply; org linking.

### Phase 2 — GeoServer role-service matching (after Phase 1)

**Goal:** every role in the registry (at minimum the org reader/writer/admin
triad) is declared in the *active* GeoServer role service, so ACL "selected
roles" render and roles are UI-selectable.

1. **Extend `GeoServerClient`** with role-service methods (raw `_request`):
   - `get_roles()` → `GET /rest/security/roles`
   - `role_exists(name)` / `create_role(name)` →
     `POST /rest/security/roles/role/{name}` (idempotent: treat "already
     exists" as success)
   - (optional) `delete_role(name)` for lifecycle.
2. **Role reconciliation service / hook:**
   - Ensure an org's `reader/writer/admin` roles exist in GeoServer. Hook where
     orgs are materialized (`get_or_create_organization` in
     `organizations/services.py`) so roles exist **before** any workspace/ACL
     save.
   - Keep it **decoupled** from `GeoServerSecuritySyncService.sync()` — the ACL
     push flow is unchanged.
3. **`sync_geoserver_roles` management command:** backfill/reconcile — push all
   active `KeycloakRole`s (or all org triads) into GeoServer. `--dry-run`,
   `--engine`.
4. **Failure handling (decide in round 2):** whether a failed role push blocks
   the operation or just warns. Default recommendation: **warn + log**, don't
   block org/workspace creation (roles are a manageability nicety; ACL
   enforcement already works by string match).

### Still-open (round-2) design questions — resolve before/within Phase 2
- Active-role-service resolution & explicit fallback behavior when neither
  `jdbc_role` nor `default` is reachable.
- Whether to populate the `roles.parent` hierarchy column (e.g. writer→reader).
- Role **rename/delete lifecycle** (org slug change, org deletion): what
  happens to registry rows and GeoServer roles.
- Backfill command scope (all engines vs one; all roles vs org triads only).

---

## 5. Verification test (already run — it works)

We confirmed the clean auth path end-to-end **before** writing any product
code. This is the exact probe; keep it as the reference for the Phase-1
Admin-API client.

**What it does:** loads `.env.dev`, does a `client_credentials` token request
against `django-dev`, then `GET /admin/realms/tosca-dev/roles`.

```python
# scratch probe — client_credentials -> list realm roles from Keycloak Admin API
import urllib.parse, urllib.request, json

def load_env(path):
    env = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env

env    = load_env(".env.dev")
base   = env["KEYCLOAK_SERVER_URL"].rstrip("/")
realm  = env["KEYCLOAK_REALM"]
cid    = env["KEYCLOAK_CLIENT_ID"]
secret = env["KEYCLOAK_CLIENT_SECRET"]

token_url = f"{base}/realms/{realm}/protocol/openid-connect/token"
roles_url = f"{base}/admin/realms/{realm}/roles?briefRepresentation=true&max=1000"

body = urllib.parse.urlencode({
    "grant_type": "client_credentials",
    "client_id": cid, "client_secret": secret,
}).encode()
req = urllib.request.Request(token_url, data=body, method="POST")
req.add_header("Content-Type", "application/x-www-form-urlencoded")
access = json.loads(urllib.request.urlopen(req).read())["access_token"]

req = urllib.request.Request(roles_url, method="GET")
req.add_header("Authorization", f"Bearer {access}")
roles = json.loads(urllib.request.urlopen(req).read())
print(sorted(r["name"] for r in roles if r["name"].startswith("ROLE_")))
```

**Result (2026-08-13, realm `tosca-dev`, client `django-dev`):**
```
[*] token alındı  → Service Accounts açık ✅
[+] /admin/.../roles cevap verdi → view-realm yetkisi var ✅  (extra user/pass GEREKMEDİ)

toplam realm rol sayısı: 14
ROLE_ ile başlayanlar (6):
    ROLE_DCS_ADMIN
    ROLE_DCS_READER
    ROLE_DCS_WRITER
    ROLE_GQ2_ADMIN
    ROLE_GQ2_READER
    ROLE_GQ2_WRITER
diğer roller (8): ADMIN, DJANGO_STAFF, DJANGO_SUPERADMIN, db-data-test-rol,
                  default-roles-tosca-dev, kose-rol-test, offline_access,
                  uma_authorization
```

**Takeaways confirmed by the test:**
- The existing `KEYCLOAK_CLIENT_SECRET` is sufficient once Service Accounts +
  `view-realm` are enabled — no new credentials.
- The `ROLE_` filter cleanly isolates the selectable pool; org triad
  (`ROLE_<SLUG>_{READER,WRITER,ADMIN}`) matches the `Organization` property
  convention exactly.
- Free/non-conventional roles exist in Keycloak (`kose-rol-test`,
  `db-data-test-rol`) that a login-only pool would miss — justifying the
  Admin-API sync command.

---

## 6. Keycloak setup prerequisite (already done in `tosca-dev`)

For any environment where the Admin-API sync must run:
1. On the `django-dev` (login) client → **enable "Service Accounts"**.
2. Service Account roles → assign `realm-management` → **`view-realm`**
   (add `view-clients` too if client-role listing is later needed).
3. No env changes required — the existing `KEYCLOAK_CLIENT_SECRET` is reused.
