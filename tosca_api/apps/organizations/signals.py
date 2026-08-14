"""
Organization slug-rename / deletion lifecycle for the KeycloakRole registry
and the GeoServer role service (Epic 11, still-open round-2 follow-up).

The ``Organization.slug`` derives every one of its Keycloak role names
(``ROLE_<SLUG>[_<PROJECT>]_<LEVEL>``). So when a slug is **renamed** or the org
is **deleted**, the catalog rows *and* the mirrored GeoServer roles carry the
now-stale name and must be reconciled. This module closes that gap by reusing
the existing "deactivation -> GeoServer deletion" mechanism (canonical §4
Phase 2, "Deletion is mirrored").

Two cases, two behaviors:

- **Rename** (``post_save``, slug changed): the org's existing ``KeycloakRole``
  rows hold old-slug names. We **deactivate** them (``is_active=False``) -- a
  pure Django write, no GeoServer round-trip -- so the operator's next
  "Sync with Keycloak" reconcile deletes the old roles from GeoServer, and the
  new-slug roles are cataloged on the next Keycloak sync/login. This keeps the
  org save path GeoServer-free, matching the Phase-2 "operator-triggered only"
  decision. An operator still has to rename the roles in Keycloak + fix ACLs;
  we log a warning to that effect.

- **Delete** (``pre_delete``): the FK is ``CASCADE``, so the org's role rows are
  about to be hard-deleted and the catalog can no longer drive their removal
  from GeoServer. We therefore capture the org's active reader/writer role
  names *before* the cascade and mirror the deletion to every engine. This is a
  **hard block**, not best-effort: if GeoServer is unreachable or a role delete
  fails, :func:`mirror_org_role_deletion` raises ``GeoServerRoleCleanupError``
  and the whole delete is aborted (the transaction rolls back). Rationale: we
  must not drop the Django rows while the mirrored GeoServer roles still exist
  (or while we cannot even confirm), and a GeoServer outage is exactly when the
  operator should be *warned*, not silently left with orphans.
  ``OrganizationAdmin`` runs the same cleanup *before* the delete transaction so
  the failure surfaces as a friendly admin message instead of a 500.

Wired up in ``OrganizationsConfig.ready()``.
"""

from __future__ import annotations

import logging

from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver

from .models import Organization

logger = logging.getLogger(__name__)

_PRIOR_SLUG_ATTR = "_role_lifecycle_prior_slug"


@receiver(pre_save, sender=Organization)
def _capture_prior_slug(sender, instance, **kwargs):
    """Stash the persisted slug so ``post_save`` can detect a rename."""
    if instance.pk is None:
        setattr(instance, _PRIOR_SLUG_ATTR, None)
        return
    try:
        prior = sender.objects.get(pk=instance.pk)
    except sender.DoesNotExist:  # pk supplied but row not yet persisted
        setattr(instance, _PRIOR_SLUG_ATTR, None)
    else:
        setattr(instance, _PRIOR_SLUG_ATTR, prior.slug)


@receiver(post_save, sender=Organization)
def _deactivate_roles_on_rename(sender, instance, created, **kwargs):
    """On a slug rename, deactivate the org's old-slug catalog roles.

    The next operator-triggered reconcile then deletes them from GeoServer; the
    new-slug roles are cataloged on the next Keycloak sync. No GeoServer I/O
    happens here.
    """
    if created:
        return
    prior_slug = getattr(instance, _PRIOR_SLUG_ATTR, None)
    if prior_slug is None or prior_slug == instance.slug:
        return

    new_prefix = f"{instance.role_prefix}_"  # e.g. "ROLE_NEWSLUG_"
    stale = instance.keycloak_roles.filter(is_active=True).exclude(
        name__startswith=new_prefix
    )
    count = stale.update(is_active=False)
    if count:
        logger.warning(
            "Organization slug renamed %r -> %r: deactivated %d stale catalog "
            "role(s). Rename the roles in Keycloak and update GeoServer ACLs; "
            "run 'Sync with Keycloak' to mirror the deletion to GeoServer.",
            prior_slug,
            instance.slug,
            count,
        )


@receiver(pre_delete, sender=Organization)
def _mirror_role_deletion_on_delete(sender, instance, **kwargs):
    """Delete the org's reader/writer roles from GeoServer, or block the delete.

    Runs before the ``CASCADE`` wipes the catalog rows (after which the catalog
    can no longer drive the deletion). **Raises** ``GeoServerRoleCleanupError``
    if GeoServer cannot be reached / the roles cannot be removed -- aborting the
    delete rather than orphaning the GeoServer roles. This guards every delete
    path (shell, API, admin); ``OrganizationAdmin`` additionally pre-runs the
    same cleanup so the block surfaces as a friendly message.
    """
    # Imported lazily to avoid an app-load-time dependency on geodata_providers.
    from tosca_api.apps.geodata_providers.role_sync import mirror_org_role_deletion

    mirror_org_role_deletion(instance)
