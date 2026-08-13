"""
KeycloakRole registry -- a persisted, self-growing catalog of Keycloak roles.

Epic 11 Phase 1 (canonical §3.1, §4): Keycloak remains the single source of
truth for role *assignment*; this table is only a *catalog* of role names so
later UIs (event/workspace/layer role pickers) and the Phase-2 GeoServer
role-service sync have a selectable pool to read from. You cannot feed a dropdown
from the convention-derived ``Organization`` properties, hence a real table.

Population is dual (canonical §3 decision 2):
- login-triggered upsert of every ``ROLE_``-prefixed token role (cheap, rides
  the existing login path, but only sees a role once a carrier logs in), and
- an authoritative ``sync_keycloak_roles`` command hitting the Keycloak Admin API
  for the *full* realm role list (including free roles nobody has logged in with).

Rows are soft-deactivated (``is_active=False``), never hard-deleted, because ACL
history may still reference a role that disappeared from Keycloak.
"""

from __future__ import annotations

from django.db import models


class KeycloakRole(models.Model):
    """A single Keycloak realm role name known to Django (catalog entry)."""

    class Source(models.TextChoices):
        LOGIN = "login", "Login token"
        KEYCLOAK_ADMIN = "keycloak_admin", "Keycloak Admin API"

    name = models.CharField(
        max_length=255,
        unique=True,
        help_text="Keycloak realm role name, e.g. ROLE_DCS_READER.",
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="keycloak_roles",
        help_text="Owning org when the role's slug segment matches a known "
        "Organization; null for free roles (e.g. kose-rol-test).",
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
