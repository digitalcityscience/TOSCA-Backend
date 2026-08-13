"""
GeoServer role-service reconciliation (Epic 11 Phase 2, canonical §4 Phase 2).

Mirrors the roles Django knows about into the *active* GeoServer role service so
ACL "selected roles" render in the UI and roles are UI-selectable. Decoupled from
the ACL push (`GeoServerSecuritySyncService`): the auto-ACL flow is unchanged and
never touches the role table.

Product decisions (2026-08-13, superseding the earlier triad/org-hook sketch):
- **Only reader + writer** roles are mirrored (admin is intentionally *not*
  pushed -- ACLs reference only reader+writer, so GeoServer's role list stays
  minimal).
- The **catalog is the single source of truth**: what gets ensured/deleted in
  GeoServer is driven entirely by ``KeycloakRole`` rows. A role Django never
  cataloged is never touched -- so system roles (``ADMIN``, ``GROUP_ADMIN``,
  ``db-data-test-rol``) can never be deleted by us.
- **Deletion is mirrored**: a reader/writer role that was deactivated in the
  catalog (``is_active=False`` -- e.g. removed from Keycloak) is deleted from the
  GeoServer role service too. Idempotent + reversible (a re-sync recreates it;
  a stale ACL rule referencing it keeps working by string match).

Entry points: the "Sync with Keycloak" admin button (hop 1 + this) and the
``sync_geoserver_roles`` management command (this only), both via
:func:`reconcile_all_engines`.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class GeoServerRoleCleanupError(Exception):
    """A GeoServer role deletion could not be completed for every engine.

    Raised by :func:`mirror_org_role_deletion` when an engine is unreachable or a
    role delete fails. Callers deleting an organization treat this as a **hard
    block** -- we must not drop the Django rows while the mirrored GeoServer roles
    still exist (or while we cannot even tell), because the catalog would then
    lose the only handle it has on them.
    """


def _geoserver_levels():
    from tosca_api.apps.authentication.models import KeycloakRole

    return (KeycloakRole.Level.READER, KeycloakRole.Level.WRITER)


def reader_writer_reconcile_names() -> tuple[set[str], set[str]]:
    """Return ``(ensure_names, delete_names)`` from the catalog (reader/writer only).

    ``ensure_names`` = active reader/writer roles (must exist in GeoServer).
    ``delete_names`` = inactive reader/writer roles (must be absent from
    GeoServer). ``name`` is unique, so the two sets never overlap.
    """
    from tosca_api.apps.authentication.models import KeycloakRole

    rows = KeycloakRole.objects.filter(level__in=_geoserver_levels())
    ensure = set(rows.filter(is_active=True).values_list("name", flat=True))
    delete = set(rows.filter(is_active=False).values_list("name", flat=True))
    return ensure, delete


def org_reader_writer_names(org) -> set[str]:
    """Return one org's *active* reader/writer role names from the catalog.

    Used by the org-deletion lifecycle (``organizations.signals``): a hard
    ``CASCADE`` delete wipes the org's ``KeycloakRole`` rows, after which the
    catalog can no longer drive their removal from GeoServer -- so we capture
    the names *before* the delete and mirror the deletion explicitly.
    """
    from tosca_api.apps.authentication.models import KeycloakRole

    return set(
        KeycloakRole.objects.filter(
            organization=org, level__in=_geoserver_levels(), is_active=True
        ).values_list("name", flat=True)
    )


class GeoServerRoleSyncService:
    """Reconciles reader/writer role names into a single engine's role service."""

    def __init__(self, engine):
        self.engine = engine
        self.client = engine.get_client()

    def reconcile(
        self,
        ensure_names,
        delete_names,
        *,
        dry_run: bool = False,
    ) -> dict:
        """Create missing ``ensure_names`` and delete present ``delete_names``.

        Fetches the active role list once and only touches roles that actually
        need it (idempotent, minimal writes). Returns
        ``{"created", "existed", "deleted", "absent", "failed"}`` (name lists;
        ``failed`` holds ``(name, error)`` pairs). Raises whatever
        ``get_roles()`` raises if the role service is unreachable -- callers that
        must not fail wrap this.
        """
        existing = set(self.client.get_roles())
        summary: dict = {
            "created": [],
            "existed": [],
            "deleted": [],
            "absent": [],
            "failed": [],
        }

        for name in sorted(n for n in ensure_names if n):
            if name in existing:
                summary["existed"].append(name)
                continue
            if dry_run:
                summary["created"].append(name)
                continue
            result = self.client.create_role(name)
            if result.success:
                summary["created"].append(name)
            else:
                summary["failed"].append((name, result.error or result.message))

        for name in sorted(n for n in delete_names if n):
            if name not in existing:
                summary["absent"].append(name)
                continue
            if dry_run:
                summary["deleted"].append(name)
                continue
            result = self.client.delete_role(name)
            if result.success:
                summary["deleted"].append(name)
            else:
                summary["failed"].append((name, result.error or result.message))

        return summary


def mirror_org_role_deletion(org, *, engines=None) -> set[str]:
    """Delete one org's reader/writer roles from every engine, or raise.

    Used by the org-deletion lifecycle: a hard ``CASCADE`` is about to wipe the
    org's ``KeycloakRole`` rows, so their removal from GeoServer must happen
    *now* (afterwards the catalog can no longer drive it). Unlike the routine
    operator reconcile, this is **strict**: if any engine is unreachable or any
    role fails to delete, it raises :class:`GeoServerRoleCleanupError` so the
    caller can abort the delete rather than orphan the GeoServer roles.

    Returns the set of role names it confirmed absent/deleted (empty if the org
    had no reader/writer roles -- a no-op that never raises).
    """
    names = org_reader_writer_names(org)
    if not names:
        return set()

    results = reconcile_all_engines(ensure=set(), delete=names, engines=engines)
    problems: list[str] = []
    for engine, summary, error in results:
        label = getattr(engine, "name", engine)
        if error:
            problems.append(f"{label}: {error}")
        elif summary and summary["failed"]:
            failed = ", ".join(f"{n} ({e})" for n, e in summary["failed"])
            problems.append(f"{label}: {failed}")
    if problems:
        raise GeoServerRoleCleanupError(
            "Could not delete GeoServer roles "
            f"{sorted(names)} for organization '{getattr(org, 'slug', org)}': "
            + "; ".join(problems)
        )
    return names


def reconcile_all_engines(
    *,
    dry_run: bool = False,
    engines=None,
    ensure: set[str] | None = None,
    delete: set[str] | None = None,
) -> list[tuple]:
    """Reconcile reader/writer roles into every active engine.

    By default the ``ensure``/``delete`` name sets are derived from the whole
    catalog (:func:`reader_writer_reconcile_names`). Callers with a narrower
    scope -- e.g. the org-deletion lifecycle mirroring just one org's roles --
    may pass explicit sets; an explicitly-passed empty set is honored (only
    ``None`` falls back to the catalog).

    Returns a list of ``(engine, summary, error)`` -- exactly one of
    ``summary``/``error`` is set per engine. A single engine being unreachable is
    reported (``error``) and never aborts the others.
    """
    from .models import GeodataEngine

    if engines is None:
        engines = GeodataEngine.objects.filter(is_active=True)

    if ensure is None or delete is None:
        catalog_ensure, catalog_delete = reader_writer_reconcile_names()
        if ensure is None:
            ensure = catalog_ensure
        if delete is None:
            delete = catalog_delete
    results: list[tuple] = []
    for engine in engines:
        try:
            summary = GeoServerRoleSyncService(engine).reconcile(
                ensure, delete, dry_run=dry_run
            )
            results.append((engine, summary, None))
        except Exception as e:  # per-engine isolation
            logger.exception(
                "GeoServer role reconcile failed for engine '%s'",
                getattr(engine, "name", engine),
            )
            results.append((engine, None, str(e)))
    return results
