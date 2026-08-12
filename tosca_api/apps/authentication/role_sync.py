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


def _social_login_payloads(extra_data, access_token=None):
    """Assemble the (source, dict) payloads carried by an allauth OIDC login.

    Shared by role and org extraction so both look in exactly the same places:
    the raw extra_data, the (verified) id_token, userinfo, and -- if the
    caller has it -- the actual OAuth access token.

    ``access_token`` matters because allauth's generic openid_connect
    provider (``complete_login``) only ever stores the ID token + userinfo
    response in ``extra_data``; it never puts the access token there. Keycloak's
    default "roles" client scope adds ``realm_access.roles`` to the access
    token by default, but "add to ID token"/"add to userinfo" are separate,
    often-off mapper toggles -- so without this, browser login can silently
    see zero roles for every user regardless of what they actually hold.
    """
    print(f"[ORG-DEBUG] _social_login_payloads: extra_data keys={sorted(extra_data.keys())}, access_token_present={bool(access_token)}")

    payloads = [("extra_data", extra_data)]

    if isinstance(access_token, str) and access_token:
        try:
            decoded = verify_and_decode_token(access_token)
            print(f"[ORG-DEBUG] decoded access_token keys={sorted(decoded.keys())}")
            payloads.append(("access_token", decoded))
        except Exception as exc:
            print(f"[ORG-DEBUG] FAILED to decode access_token: {exc}")
            logger.warning("Failed to decode access_token for token extraction", extra={
                "error": str(exc),
            })

    id_token = extra_data.get("id_token")
    if isinstance(id_token, str):
        try:
            decoded = verify_and_decode_token(id_token)
            print(f"[ORG-DEBUG] decoded id_token keys={sorted(decoded.keys())}")
            payloads.append(("id_token", decoded))
        except Exception as exc:
            print(f"[ORG-DEBUG] FAILED to decode id_token: {exc}")
            logger.warning("Failed to decode id_token for token extraction", extra={
                "error": str(exc),
                "token_present": bool(id_token),
            })
    elif isinstance(id_token, dict):
        print(f"[ORG-DEBUG] id_token already a dict, keys={sorted(id_token.keys())}")
        payloads.append(("id_token_dict", id_token))

    userinfo = extra_data.get("userinfo", {})
    if isinstance(userinfo, dict):
        print(f"[ORG-DEBUG] userinfo keys={sorted(userinfo.keys())}")
        payloads.append(("userinfo", userinfo))

    return payloads


def extract_roles_from_token(decoded_token):
    """Extract Keycloak realm roles from a decoded JWT access token."""
    return _extract_roles_from_payloads([("access_token", decoded_token)])


def extract_roles_from_social_data(extra_data, access_token=None):
    """Extract Keycloak realm roles from allauth OIDC data."""
    return _extract_roles_from_payloads(_social_login_payloads(extra_data, access_token))


def extract_org_from_token(decoded_token):
    """Extract the default_organization slug from a decoded JWT access token."""
    return _extract_org_from_payloads([("access_token", decoded_token)])


def extract_org_from_social_data(extra_data, access_token=None):
    """Extract the default_organization slug from allauth OIDC data."""
    return _extract_org_from_payloads(_social_login_payloads(extra_data, access_token))


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


def _org_slug_from_payload(payload):
    """Pull an org slug out of one payload, trying both claim shapes.

    Keycloak's mapper config has shipped two different shapes over the
    course of this project: the originally-specced scalar
    ``default_organization`` claim, and (currently, live-verified 2026-08-12)
    an ``organization`` claim carrying a list of slugs (e.g. ``["gq2"]``).
    There is no multi-org UI/logic yet (canonical §4, ticket 14 backlog), so
    when it's a list we just take the first slug as the user's org.
    """
    value = payload.get("default_organization")
    if isinstance(value, str) and value:
        return value, "default_organization"

    value = payload.get("organization")
    if isinstance(value, list) and value and isinstance(value[0], str):
        return value[0], "organization[0]"
    if isinstance(value, str) and value:
        return value, "organization"

    return None, None


def _extract_org_from_payloads(payloads):
    print(f"[ORG-DEBUG] checking {len(payloads)} payload(s) for org claim (default_organization or organization)")
    for source, payload in payloads:
        if not isinstance(payload, dict):
            print(f"[ORG-DEBUG]   source={source!r} -> not a dict, skipping ({type(payload)})")
            continue
        print(f"[ORG-DEBUG]   source={source!r} keys={sorted(payload.keys())}")
        slug, claim = _org_slug_from_payload(payload)
        print(f"[ORG-DEBUG]   source={source!r} org_slug={slug!r} claim={claim!r}")
        if slug:
            print(f"[ORG-DEBUG] FOUND org_slug={slug!r} via claim={claim!r} in source={source!r}")
            logger.info("Extracted default organization from Keycloak data", extra={
                "default_organization": slug,
                "claim": claim,
                "source": source,
            })
            return ExtractedOrg(default_slug=slug, present=True, sources=[source])

    print(f"[ORG-DEBUG] NOT FOUND -- no org claim in any of {[s for s, _ in payloads]}")
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
