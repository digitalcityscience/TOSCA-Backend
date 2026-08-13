"""
Population of the ``KeycloakRole`` catalog (Epic 11 Phase 1, canonical §4).

Two entry points, one shared write path:

- :func:`register_login_roles` -- cheap, best-effort upsert of the roles carried
  by a login token. Must never break a login, so it swallows and logs errors.
- :func:`sync_realm_roles` -- authoritative reconciliation against the *full*
  realm role list (fed by the Keycloak Admin API via the ``sync_keycloak_roles``
  command): upsert conforming roles, deactivate ones that vanished.

Only roles that conform to ``ROLE_<ORG>[_<PROJECT>]_<LEVEL>`` *and* resolve to a
known :class:`~tosca_api.apps.organizations.models.Organization` are cataloged;
everything else is skipped (canonical §3.2 decision 10 -- no null-org rows, never
manufacture an org from a role name).
"""

from __future__ import annotations

import logging

from tosca_api.apps.authentication.models import KeycloakRole
from tosca_api.apps.authentication.role_sync import ParsedRole, parse_role_name
from tosca_api.apps.organizations.models import Organization

logger = logging.getLogger(__name__)


def _resolve(name: str) -> tuple[ParsedRole, Organization] | None:
    """Parse ``name`` and resolve its org, or return ``None`` (skip) with a log."""
    parsed = parse_role_name(name)
    if parsed is None:
        logger.debug("Skipping non-conforming role name", extra={"role": name})
        return None
    org = Organization.objects.filter(slug=parsed.org_slug).first()
    if org is None:
        logger.info(
            "Skipping role with no matching organization",
            extra={"role": name, "org_slug": parsed.org_slug},
        )
        return None
    return parsed, org


def _write(name: str, parsed: ParsedRole, org: Organization, source: str) -> bool:
    """Upsert one resolved role. Returns ``True`` if a new row was created.

    ``source`` (how the role was *first* seen) is only set on create; a re-seen
    role keeps its original source but is refreshed (org/project/level) and
    reactivated. ``last_seen_at`` bumps via ``auto_now`` on every save.
    """
    defaults = {
        "organization": org,
        "project": parsed.project,
        "level": parsed.level,
        "is_active": True,
    }
    _, created = KeycloakRole.objects.update_or_create(
        name=name,
        defaults=defaults,
        create_defaults={**defaults, "source": source},
    )
    return created


def upsert_role(name: str, *, source: str) -> bool | None:
    """Upsert a single role. Returns created-flag, or ``None`` if skipped."""
    resolved = _resolve(name)
    if resolved is None:
        return None
    return _write(name, *resolved, source)


def register_login_roles(extracted_roles, *, source: str = KeycloakRole.Source.LOGIN) -> None:
    """Best-effort catalog upsert of a login's token roles. Never raises."""
    try:
        for name in sorted(extracted_roles.roles):
            upsert_role(name, source=source)
    except Exception:  # pragma: no cover - defensive; login must not break
        logger.exception("KeycloakRole login upsert failed (non-blocking)")


def sync_realm_roles(
    role_names,
    *,
    source: str = KeycloakRole.Source.KEYCLOAK_ADMIN,
    deactivate_stale: bool = True,
    dry_run: bool = False,
) -> dict[str, int]:
    """Reconcile the catalog against a full realm role list.

    Returns a summary ``{"created", "updated", "skipped", "deactivated"}``.
    With ``dry_run`` no rows are written, but the counts reflect what *would*
    happen. ``deactivate_stale`` flips ``is_active=False`` on active rows whose
    name is absent from (the conforming subset of) ``role_names``.
    """
    summary = {"created": 0, "updated": 0, "skipped": 0, "deactivated": 0}
    existing = set(KeycloakRole.objects.values_list("name", flat=True))
    present: set[str] = set()

    for name in role_names:
        resolved = _resolve(name)
        if resolved is None:
            summary["skipped"] += 1
            continue
        present.add(name)
        if name in existing:
            summary["updated"] += 1
        else:
            summary["created"] += 1
        if not dry_run:
            _write(name, *resolved, source)

    if deactivate_stale:
        stale = KeycloakRole.objects.filter(is_active=True).exclude(name__in=present)
        summary["deactivated"] = stale.count()
        if not dry_run:
            stale.update(is_active=False)

    return summary
