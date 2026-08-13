"""
KeycloakRole registry -- a persisted, self-growing catalog of *our* Keycloak roles.

Epic 11 Phase 1 (canonical §3.1, §4): Keycloak remains the single source of
truth for role *assignment*; this table is only a *catalog* of role names so
later UIs (event/workspace/layer role pickers) and the Phase-2 GeoServer
role-service sync have a selectable pool to read from. You cannot feed a dropdown
from the convention-derived ``Organization`` properties, hence a real table.

Scope of the catalog (deliberately narrow):
- Only ``ROLE_``-prefixed roles that conform to the grammar
  ``ROLE_<ORG>[_<PROJECT>]_<LEVEL>`` enter here. Non-conforming Keycloak/platform
  roles (``offline_access``, ``DJANGO_STAFF``, ``ADMIN``, free test roles) are
  noise -- we only care about our own system's roles.
- Every row therefore has an **organization** (the first segment, resolved to an
  existing ``Organization``) and a **level**. ``project`` is the optional middle
  segment: a named sub-scope *within* an org (e.g. ``ROLE_DCS_TOSCA_WRITER`` ->
  org ``dcs`` / project ``tosca``). ``organization`` and ``project`` are separate
  concepts on purpose (an org owns many projects).

Population is dual (canonical §3 decision 2): a cheap login-triggered upsert of
token roles, plus an authoritative ``sync_keycloak_roles`` command hitting the
Keycloak Admin API for the full realm role list. Rows are soft-deactivated
(``is_active=False``), never hard-deleted, because ACL history may still
reference a role that disappeared from Keycloak.
"""

from __future__ import annotations

from django.db import models


class KeycloakRole(models.Model):
    """A single conforming Keycloak role name known to Django (catalog entry)."""

    class Source(models.TextChoices):
        LOGIN = "login", "Login token"
        KEYCLOAK_ADMIN = "keycloak_admin", "Keycloak Admin API"

    class Level(models.TextChoices):
        READER = "READER", "Reader"
        WRITER = "WRITER", "Writer"
        ADMIN = "ADMIN", "Admin"

    name = models.CharField(
        max_length=255,
        unique=True,
        help_text="Full Keycloak role name, e.g. ROLE_DCS_TOSCA_WRITER.",
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="keycloak_roles",
        help_text="Owning org (first role segment). Mandatory -- a role that "
        "resolves to no known Organization is not cataloged.",
    )
    project = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Optional project sub-scope within the org (middle segment); "
        "empty for org-level roles like ROLE_DCS_WRITER.",
    )
    level = models.CharField(
        max_length=16,
        choices=Level.choices,
        help_text="Access level (trailing role segment).",
    )
    source = models.CharField(
        max_length=32,
        choices=Source.choices,
        help_text="How this role was first seen.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Soft-deactivation flag; set False when a role disappears "
        "from Keycloak (never hard-deleted -- ACL history may reference it).",
    )
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Keycloak role"
        verbose_name_plural = "Keycloak roles"

    def __str__(self) -> str:
        return self.name
