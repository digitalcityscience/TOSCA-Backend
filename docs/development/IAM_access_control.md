### TOSCA-Web IAM & Access Control

Final POC Architecture 26012026

⸻

1. Purpose of This Design

The goal is to provide clear, secure, and low-maintenance access control for geospatial data served directly from GeoServer, without proxying requests through Django.

Key constraints:
• GeoServer is accessed directly by clients
• Authorization must work without a backend proxy
• Complexity must stay low for POC
• System must still be expandable later

⸻

2. Core Principles

2.1 Single Source of Truth
• Django is the control plane (decisions & orchestration)
• Keycloak is the IAM provider (users & roles)
• GeoServer enforces access using JWT roles

Django creates and synchronizes,
Keycloak stores and distributes,
GeoServer only consumes.

⸻

2.2 RBAC Only (No ABAC)
• GeoServer can only evaluate roles from JWT
• No request-time attribute logic
• No workspace claims, no policy engine

➡️ Role-Based Access Control (RBAC) only

This is a hard technical constraint, not a preference.

⸻

2.3 Direct GeoServer Access
• Clients talk directly to GeoServer (WMS, WFS, WMTS)
• Django is not a proxy >>> that's why No ABAC
• Therefore:
• All access rules must be precomputed
• No per-request logic

⸻

3. System Roles (Bootstrap – Fixed)

These roles exist in every deployment.

3.1 ROLE_GEOSERVER_ADMIN
• Full administrative access
• Can manage:
• Workspaces
• Layers
• Styles
• Security rules
• Assigned manually to very few users
• Global rule:

_._.a = ROLE_GEOSERVER_ADMIN

⸻

3.2 ROLE_AUTHENTICATED
• Automatically assigned by Keycloak
• Represents: “any logged-in user”
• Used for internal / organization-wide read access

⸻

3.3 ROLE_PUBLIC_ACCESS
• Represents anonymous access
• Mapped to GeoServer’s ROLE_ANONYMOUS
• Used only for public data

⸻

4. Workspace-Centric Access Model

4.1 Workspace Is the Security Boundary

For the POC:
• All access rules are defined at workspace level
• No layer-specific ACLs
• Layers always inherit workspace rules

This keeps the model:
• Understandable
• Maintainable
• Safe

⸻

4.2 Workspace Read Access (Policy-Based)

Each workspace has one read policy, selected at creation:

Policy Meaning GeoServer Role
Public Anyone can read ROLE_PUBLIC_ACCESS
Internal Any logged-in user ROLE_AUTHENTICATED
Private Not exposed (POC: disabled or future) —

➡️ Users never select roles, only intent.

⸻

5. Workspace Admin Role (Explicit & Optional)

5.1 No Automatic Admin Role Creation

To avoid role explosion and confusion:
• Workspace admin roles are not created automatically
• They are created only on demand

⸻

5.2 “Create Workspace Admin Role” Button

In the Django admin UI:
• Admin can click:
“Create Workspace Admin Role”

When clicked: 1. ROLE*WS*<WORKSPACE>\_ADMIN is created in Keycloak 2. Role metadata is stored in Django 3. Role becomes selectable for user assignment

This action is:
• Explicit
• Rare
• Intentional

⸻

5.3 Assigning Workspace Admins

After the role exists:
• Admin assigns it to users manually in Keycloak

Workspace admins can:
• Create layers in that workspace
• Manage workspace configuration
• But do not have system-wide power

⸻

1. User Lifecycle (POC)

6.1 Creating a User

1. User is created in Keycloak
2. 2. User has:
      • ROLE_AUTHENTICATED
3. User can:
   • Read public workspaces
   • Read internal workspaces
4. User cannot write anything

⸻

6.2 Granting Write/Admin Rights
• Only via:
• Creating a workspace admin role
• Assigning it manually
• No default write permissions
• No editor / writer roles in POC

⸻

7. Why We Deliberately Avoided Certain Things

❌ Workspace Reader Roles
• Too many roles
• High operational overhead
• Low value for POC

Read access is handled via:
• Public
• Authenticated

⸻

❌ Layer-Level ACLs (POC)
• Adds complexity
• Harder to reason about
• Easy to break security unintentionally

Layer-level rules are deferred to v2.

⸻

❌ ABAC / Claims-Based Access
• GeoServer cannot evaluate them
• Would require a proxy
• Breaks direct-access requirement

⸻

8. Final Mental Model

Read Access

“Who is allowed to see this workspace?”

Answer:
• Everyone
• Logged-in users
• (Later) restricted groups

⸻

Write / Admin Access

“Who is explicitly trusted to manage this workspace?”

Answer:
• Only users with a manually created admin role

⸻

9. Why This Design Is Good
   • Minimal roles
   • Clear responsibility boundaries
   • Matches GeoServer’s real capabilities
   • POC-friendly
   • Scales conceptually to production

Most importantly:

Security is explicit, not implicit.

⸻

10. What Comes Later (Not in POC)
    • Workspace reader roles (optional)
    • Layer-level overrides
    • Editor / writer roles
    • UI role grouping
    • Auditing & policy previews

None of these decisions block future growth.

⸻

Final One-Sentence Summary

In the POC, read access is policy-based at workspace level, admin access is explicit and rare, roles are created only when needed, and GeoServer enforces everything directly using JWT roles from Keycloak.

This is a clean, defensible, and realistic architecture.
