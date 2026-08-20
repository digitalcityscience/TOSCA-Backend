from dataclasses import dataclass
import logging

from django.conf import settings
from django.contrib import messages

from tosca_api.apps.core.jwt_utils import verify_and_decode_token

logger = logging.getLogger(__name__)

# Only the global superadmin role bypasses org-membership/org-scoping checks.
# DJANGO_STAFF grants admin-UI *access* only (see KEYCLOAK_DJANGO_STAFF_ROLES /
# sync_user_permissions_from_roles) -- it is still bound to ROLE_<ORG>_* for
# what it can see/touch. Conflating the two let any admin-UI user bypass org
# scoping entirely (2026-08-19 incident: HPA staff edited a DCS Workspace).
ORG_CHECK_EXEMPT_ROLES = frozenset({"DJANGO_SUPERADMIN"})


# Org-role levels in ascending order of capability. Roles are composite in
# Keycloak (WRITER includes READER, ADMIN includes WRITER), so the effective
# level is the highest one present in the token. See canonical §2/§2b.
ORG_ROLE_LEVELS = ("READER", "WRITER", "ADMIN")

# Prefix that marks a role as part of *our* system. Only these enter the
# KeycloakRole registry -- everything else (offline_access, DJANGO_*, ADMIN,
# free test roles) is Keycloak/platform noise we deliberately ignore.
ROLE_PREFIX = "ROLE_"

# Single source of truth for level ranking (security tickets ticket 05) --
# used both to pick the highest org-role level out of a raw role set
# (`normalize_org_roles`) and by DRF permission classes comparing a caller's
# level against a required one (`organizations.permissions.LEVEL_RANK`).
LEVEL_RANK = {"READER": 0, "WRITER": 1, "ADMIN": 2}


@dataclass(frozen=True)
class ParsedRole:
    """The structured identity carried by a conforming role name.

    Grammar: ``ROLE_<ORG>[_<PROJECT>]_<LEVEL>`` (canonical §2, Epic-11 project
    scoping). ``org_slug`` and ``project`` are lowercased to match
    ``Organization.slug``; ``project`` is ``""`` for org-level roles.
    """

    org_slug: str
    project: str
    level: str


def parse_role_name(name):
    """Parse a ``ROLE_<ORG>[_<PROJECT>]_<LEVEL>`` name into its parts.

    Returns a :class:`ParsedRole`, or ``None`` when the name does not conform:
    not ``ROLE_``-prefixed, an unknown trailing level, or more than one project
    segment (org/project slugs are single-segment -- underscores are the
    delimiter, so ``ROLE_DCS_X_READER`` is org ``dcs`` + project ``x``, never
    an atomic ``dcs_x``).
    """
    if not name or not name.startswith(ROLE_PREFIX):
        return None

    segments = name[len(ROLE_PREFIX):].split("_")
    # Need at least <ORG> + <LEVEL>; at most <ORG> + <PROJECT> + <LEVEL>.
    if not 2 <= len(segments) <= 3:
        return None

    level = segments[-1]
    if level not in ORG_ROLE_LEVELS:
        return None

    org_slug = segments[0].lower()
    project = segments[1].lower() if len(segments) == 3 else ""
    if not org_slug:
        return None

    return ParsedRole(org_slug=org_slug, project=project, level=level)


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
    payloads = [("extra_data", extra_data)]

    if isinstance(access_token, str) and access_token:
        try:
            decoded = verify_and_decode_token(access_token)
            payloads.append(("access_token", decoded))
        except Exception as exc:
            logger.warning("Failed to decode access_token for token extraction", extra={
                "error": str(exc),
            })

    id_token = extra_data.get("id_token")
    if isinstance(id_token, str):
        try:
            decoded = verify_and_decode_token(id_token)
            payloads.append(("id_token", decoded))
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


def extract_roles_from_social_data(extra_data, access_token=None):
    """Extract Keycloak realm roles from allauth OIDC data."""
    return _extract_roles_from_payloads(_social_login_payloads(extra_data, access_token))


def extract_org_from_token(decoded_token):
    """Extract the default_organization slug from a decoded JWT access token."""
    return _extract_org_from_payloads([("access_token", decoded_token)])


def extract_org_from_social_data(extra_data, access_token=None):
    """Extract the default_organization slug from allauth OIDC data."""
    return _extract_org_from_payloads(_social_login_payloads(extra_data, access_token))


def normalize_org_roles(roles: set[str]) -> dict[str, str]:
    """Normalize a flat Keycloak role set into ``{org_slug: highest_level}``.

    Org-level roles only (``parsed.project == ""``) -- project-scoped roles
    have no consumer yet (canonical §10 "no project-level roles") and are
    silently dropped here rather than half-supported. When a token carries
    more than one level for the same org (composite roles, e.g. both
    ``ROLE_DCS_WRITER`` and ``ROLE_DCS_ADMIN``), the highest rank wins.
    """
    org_roles: dict[str, str] = {}
    for role in roles:
        parsed = parse_role_name(role)
        if parsed is None or parsed.project:
            continue
        current = org_roles.get(parsed.org_slug)
        if current is None or LEVEL_RANK[parsed.level] > LEVEL_RANK[current]:
            org_roles[parsed.org_slug] = parsed.level
    return org_roles


def denormalize_org_roles(org_roles: dict[str, str]) -> set[str]:
    """Inverse of :func:`normalize_org_roles`: rebuild ``ROLE_<ORG>_<LEVEL>``
    tokens from a normalized ``{org_slug: level}`` mapping.

    Used to feed a persisted/normalized claims source (e.g.
    :class:`~tosca_api.apps.organizations.models.UserAuthorizationSnapshot`)
    back into call sites that still key off the raw Keycloak role-name shape
    (``org_role_level``, ``ORG_CHECK_EXEMPT_ROLES`` membership checks).
    """
    return {f"ROLE_{org.upper()}_{level}" for org, level in org_roles.items()}


@dataclass(frozen=True)
class AuthClaims:
    """Normalized authorization claims for one user, ready to attach
    request-locally (``user._auth_claims``) or persist as a
    :class:`~tosca_api.apps.organizations.models.UserAuthorizationSnapshot`.
    """

    org_roles: dict[str, str]
    default_org: str | None
    authoritative: bool
    platform_exempt: bool = False
    """Whether the token/login actually carried a role in
    ``ORG_CHECK_EXEMPT_ROLES`` (``DJANGO_SUPERADMIN`` only).

    Captured here -- rather than inferred later from ``user.is_staff``/
    ``user.is_superuser`` -- because those Django columns can be toggled
    independently of Keycloak (e.g. via the admin's own ``UserAdmin``
    "Permissions" fieldset); this field is the actual last-synced source of
    truth for platform-role exemption (canonical §2b), not a proxy for it.
    """


def build_auth_claims(extracted_roles: ExtractedRoles, extracted_org: ExtractedOrg) -> AuthClaims:
    """Combine role + org extraction results into normalized :class:`AuthClaims`."""
    return AuthClaims(
        org_roles=normalize_org_roles(extracted_roles.roles),
        default_org=extracted_org.default_slug,
        authoritative=extracted_roles.authoritative,
        platform_exempt=bool(extracted_roles.roles & ORG_CHECK_EXEMPT_ROLES),
    )


def attach_auth_claims(user, claims: AuthClaims) -> None:
    """Attach ``claims`` request-locally to ``user`` (never persisted here).

    The single write path both auth entry points (Bearer, browser/admin) use
    to make live claims available to :func:`organizations.policy.user_claims`
    for the duration of the current request/login, per the ticket-05
    precedence: live claims first, persisted snapshot second, fail closed.
    """
    user._auth_claims = claims


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

    Only ``DJANGO_SUPERADMIN`` is exempt. When ``request`` is provided
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
    for source, payload in payloads:
        if not isinstance(payload, dict):
            continue
        slug, claim = _org_slug_from_payload(payload)
        if slug:
            logger.info("Extracted default organization from Keycloak data", extra={
                "default_organization": slug,
                "claim": claim,
                "source": source,
            })
            return ExtractedOrg(default_slug=slug, present=True, sources=[source])

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
