"""Login-time auto-provisioning for Organization rows.

Keycloak is the source of truth for org membership (canonical §4). Django only
mirrors organizations as ownership labels, and it must never block a login
because that mirror hasn't caught up yet -- so the first time a user shows up
with a ``default_organization`` claim Django hasn't seen before, we create the
row lazily instead of requiring a manual admin step.
"""

from __future__ import annotations

import logging

from django.db import IntegrityError

from .models import Organization

logger = logging.getLogger(__name__)


def get_or_create_organization(slug: str) -> Organization:
    """Return the ``Organization`` for ``slug``, creating it if it doesn't exist yet.

    One Keycloak org == one Django Organization row, keyed by slug. ``name``
    has no independent source of truth on the Django side (Keycloak doesn't
    hand us a display name in the token), so it's derived from the slug and
    can be edited later in the admin without affecting anything (only
    ``slug`` drives the ``ROLE_<SLUG>_*`` convention).
    """
    try:
        org, created = Organization.objects.get_or_create(
            slug=slug,
            defaults={"name": slug.upper(), "is_active": True},
        )
    except IntegrityError:
        # Concurrent logins for a brand-new org can race the get/create; the
        # loser here just re-fetches what the winner committed.
        org = Organization.objects.get(slug=slug)
        created = False

    if created:
        logger.info("Auto-provisioned organization from Keycloak login", extra={
            "org_slug": slug,
        })
    return org
