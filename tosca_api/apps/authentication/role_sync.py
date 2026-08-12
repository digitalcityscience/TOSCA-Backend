from dataclasses import dataclass
import logging

from django.conf import settings
from django.contrib import messages

from tosca_api.apps.core.jwt_utils import verify_and_decode_token

logger = logging.getLogger(__name__)

# Platform roles are exempt from org-membership requirements (canonical §5d).
ORG_CHECK_EXEMPT_ROLES = frozenset({"DJANGO_SUPERADMIN", "DJANGO_STAFF"})


# Org-role levels in ascending order of capability. Roles are composite in
# Keycloak (WRITER includes READER, ADMIN includes WRITER), so the effective
# level is the highest one present in the token. See canonical §2/§2b.
ORG_ROLE_LEVELS = ("READER", "WRITER", "ADMIN")


@dataclass(frozen=True)
class ExtractedRoles:
    roles: set[str]
    authoritative: bool
    sources: list[str]


@dataclass(frozen=True)
class ExtractedOrg:
    """The user's default organization slug read from a Keycloak token.

    ``present`` mirrors ``ExtractedRoles.authoritative``: it is True only when the
    ``default_organization`` claim was actually found, so callers can tell "no
    org assigned" apart from "claim missing from this payload".
    """

    default_slug: str | None
    present: bool
    sources: list[str]


def _social_login_payloads(extra_data):
    """Assemble the (source, dict) payloads carried by an allauth OIDC login.

    Shared by role and org extraction so both look in exactly the same places:
    the raw extra_data, the (verified) id_token, and userinfo.
    """
    payloads = [("extra_data", extra_data)]

    id_token = extra_data.get("id_token")
    if isinstance(id_token, str):
        try:
            payloads.append(("id_token", verify_and_decode_token(id_token)))
        except Exception as exc:
            logger.warning("Failed to decode id_token for token extraction", extra={
                "error": str(exc),
                "token_present": bool(id_token),
            })
    elif isinstance(id_token, dict):
        payloads.append(("id_token_dict", id_token))

    userinfo = extra_data.get("userinfo", {})
    if isinstance(userinfo, dict):
        payloads.append(("userinfo", userinfo))

    return payloads


def extract_roles_from_token(decoded_token):
    """Extract Keycloak realm roles from a decoded JWT access token."""
    return _extract_roles_from_payloads([("access_token", decoded_token)])


def extract_roles_from_social_data(extra_data):
    """Extract Keycloak realm roles from allauth OIDC data."""
    return _extract_roles_from_payloads(_social_login_payloads(extra_data))


def extract_org_from_token(decoded_token):
    """Extract the default_organization slug from a decoded JWT access token."""
    return _extract_org_from_payloads([("access_token", decoded_token)])


def extract_org_from_social_data(extra_data):
    """Extract the default_organization slug from allauth OIDC data."""
    return _extract_org_from_payloads(_social_login_payloads(extra_data))


def org_role_level(roles, org_slug):
    """Return the effective org-role level for ``org_slug`` given a role set.

    Maps the Keycloak convention ``ROLE_<SLUG>_<LEVEL>`` to the highest level
    present. Returns one of ``"READER" | "WRITER" | "ADMIN"`` or ``None`` when the
    user holds no role for that org (canonical §2b org-role coherence).
    """
    if not org_slug:
        return None
    prefix = f"ROLE_{org_slug.upper()}_"
    for level in reversed(ORG_ROLE_LEVELS):
        if f"{prefix}{level}" in roles:
            return level
    return None


def run_org_login_checks(user, extracted_roles, extracted_org, *, request=None):
    """Two non-blocking login coherence checks (canonical §5d).

    Never blocks login and never raises -- identity is valid regardless; these
    only surface misconfiguration. Returns the list of triggered warning codes
    (``"no_org"`` / ``"org_without_role"``) for testing/telemetry.

    * **org-presence**: no ``default_organization`` -> org-scoped access is off.
    * **org-role coherence**: member of an org but holding no ``ROLE_<SLUG>_*``
      for it (e.g. a dcs member with only gq roles).

    ``DJANGO_SUPERADMIN`` / ``DJANGO_STAFF`` are exempt. When ``request`` is provided
    (browser login) a user-facing ``messages.warning`` is added too.
    """
    warnings: list[str] = []
    roles = extracted_roles.roles

    if roles & ORG_CHECK_EXEMPT_ROLES:
        return warnings

    if not extracted_org.default_slug:
        warnings.append("no_org")
        _emit_login_warning(
            request, user, "no_org",
            "You have no organization assigned — contact your admin.",
        )
        return warnings

    if org_role_level(roles, extracted_org.default_slug) is None:
        warnings.append("org_without_role")
        _emit_login_warning(
            request, user, "org_without_role",
            f"You belong to organization '{extracted_org.default_slug}' but hold no "
            "role for it — contact your admin.",
            org_slug=extracted_org.default_slug,
        )

    return warnings


def _emit_login_warning(request, user, code, message, *, org_slug=None):
    logger.warning("Org login-check warning", extra={
        "check": code,
        "user_id": getattr(user, "pk", None),
        "username": getattr(user, "username", None),
        "org_slug": org_slug,
    })
    if request is not None:
        try:
            messages.warning(request, message)
        except Exception as exc:  # messages middleware may be absent (e.g. API)
            logger.debug("Could not add user-facing login warning", extra={
                "error": str(exc),
                "check": code,
            })


def _extract_org_from_payloads(payloads):
    for source, payload in payloads:
        if not isinstance(payload, dict):
            continue
        value = payload.get("default_organization")
        if isinstance(value, str) and value:
            logger.info("Extracted default organization from Keycloak data", extra={
                "default_organization": value,
                "source": source,
            })
            return ExtractedOrg(default_slug=value, present=True, sources=[source])

    logger.info("No default_organization claim found in Keycloak data", extra={
        "sources_checked": [s for s, _ in payloads],
    })
    return ExtractedOrg(default_slug=None, present=False, sources=[])


def sync_user_permissions_from_roles(user, extracted_roles, *, save=True):
    """
    Sync Django staff/superuser flags from authoritative Keycloak roles.

    Missing role claims are treated as non-authoritative because allauth/userinfo
    responses can omit realm_access. In that case we keep the existing local
    flags instead of accidentally demoting a user on login.
    """
    if not extracted_roles.authoritative:
        logger.warning("Skipping user permission sync because Keycloak roles are missing", extra={
            "user_id": user.pk,
            "username": user.username,
            "sources": extracted_roles.sources,
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
        })
        return False

    old_staff = user.is_staff
    old_superuser = user.is_superuser

    staff_roles = set(getattr(
        settings,
        "KEYCLOAK_DJANGO_STAFF_ROLES",
        # ADMIN excluded -- GeoServer console role, not Django (canonical §2).
        ["DJANGO_STAFF", "DJANGO_SUPERADMIN"],
    ))
    superuser_roles = set(getattr(
        settings,
        "KEYCLOAK_DJANGO_SUPERUSER_ROLES",
        ["DJANGO_SUPERADMIN"],
    ))

    user.is_superuser = bool(extracted_roles.roles & superuser_roles)
    user.is_staff = user.is_superuser or bool(extracted_roles.roles & staff_roles)

    changed = user.is_staff != old_staff or user.is_superuser != old_superuser
    if changed and save:
        user.save(update_fields=["is_staff", "is_superuser"])

    logger.info("User permissions synchronized from Keycloak roles", extra={
        "user_id": user.pk,
        "username": user.username,
        "roles": sorted(extracted_roles.roles),
        "sources": extracted_roles.sources,
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
        "changed": changed,
    })
    return changed


def _extract_roles_from_payloads(payloads):
    roles = set()
    sources = []
    authoritative = False

    for source, payload in payloads:
        if not isinstance(payload, dict):
            continue

        realm_access = payload.get("realm_access")
        if not isinstance(realm_access, dict):
            continue

        source_roles = realm_access.get("roles")
        if not isinstance(source_roles, list):
            continue

        authoritative = True
        roles.update(role for role in source_roles if isinstance(role, str))
        sources.append(f"{source}({len(source_roles)})")

    logger.info("Extracted roles from Keycloak data", extra={
        "roles_count": len(roles),
        "roles": sorted(roles),
        "sources": sources,
        "authoritative": authoritative,
    })
    return ExtractedRoles(roles=roles, authoritative=authoritative, sources=sources)
