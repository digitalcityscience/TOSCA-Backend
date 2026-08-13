"""
Organization model - the Django mirror of a native Keycloak Organization.

Epic 11 canonical decision: Keycloak is the single source of truth for identity
AND role assignment. Django is a pure enforcer + resource control-plane. This
table is only an ownership label that Workspace/Campaign rows FK onto; it never
stores users or role assignments (those live in Keycloak).

The ``slug`` is authoritative: it *derives* the Keycloak role names via the
convention ``ROLE_<SLUG>[_<PROJECT>]_<READER|WRITER|ADMIN>`` (see canonical §2).
The slug is the **first** role segment, so it must be a single segment -- no
underscores. The underscore is the role-name delimiter: ``ROLE_DCS_TOSCA_WRITER``
is org ``dcs`` + project ``tosca``, never an atomic ``dcs_tosca``. This lets the
registry parse a role name back into (org, project, level) unambiguously
(see ``authentication.role_sync.parse_role_name``).
"""

from __future__ import annotations

import uuid

from django.core.validators import RegexValidator
from django.db import models

from tosca_api.apps.core.models import TimeStampedModel

# Single-segment slug: lowercase letters, digits, hyphens -- but no underscore,
# which is reserved as the role-name segment delimiter (see module docstring).
SINGLE_SEGMENT_SLUG_VALIDATOR = RegexValidator(
    regex=r"^[a-z0-9-]+$",
    message="Slug must be a single segment: lowercase letters, digits and "
    "hyphens only (no underscore -- it delimits role-name segments).",
)


class Organization(TimeStampedModel):
    """Django mirror of a native Keycloak Organization (ownership label only)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(
        unique=True,
        validators=[SINGLE_SEGMENT_SLUG_VALIDATOR],
        help_text="Single segment (no underscore). Derives Keycloak role names: "
        "ROLE_<SLUG>_READER/WRITER/ADMIN. Stable identifier -- renaming means "
        "renaming Keycloak roles + GeoServer ACL too.",
    )
    keycloak_org_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        help_text="Native Keycloak organization id. Nullable until reconciled.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Organization"
        verbose_name_plural = "Organizations"

    def __str__(self) -> str:
        return self.name

    @property
    def role_prefix(self) -> str:
        """The Keycloak role prefix derived from the slug (e.g. ``ROLE_DCS``)."""
        return f"ROLE_{self.slug.upper()}"

    @property
    def reader_role(self) -> str:
        return f"{self.role_prefix}_READER"

    @property
    def writer_role(self) -> str:
        return f"{self.role_prefix}_WRITER"

    @property
    def admin_role(self) -> str:
        return f"{self.role_prefix}_ADMIN"
