from dataclasses import dataclass
import logging

from django.conf import settings

from tosca_api.apps.core.jwt_utils import verify_and_decode_token

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractedRoles:
    roles: set[str]
    authoritative: bool
    sources: list[str]


def extract_roles_from_token(decoded_token):
    """Extract Keycloak realm roles from a decoded JWT access token."""
    return _extract_roles_from_payloads([("access_token", decoded_token)])


def extract_roles_from_social_data(extra_data):
    """Extract Keycloak realm roles from allauth OIDC data."""
    payloads = [("extra_data", extra_data)]

    id_token = extra_data.get("id_token")
    if isinstance(id_token, str):
        try:
            payloads.append(("id_token", verify_and_decode_token(id_token)))
        except Exception as exc:
            logger.warning("Failed to decode id_token for role extraction", extra={
                "error": str(exc),
                "token_present": bool(id_token),
            })
    elif isinstance(id_token, dict):
        payloads.append(("id_token_dict", id_token))

    userinfo = extra_data.get("userinfo", {})
    if isinstance(userinfo, dict):
        payloads.append(("userinfo", userinfo))

    return _extract_roles_from_payloads(payloads)


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
        ["DJANGO_STAFF", "ADMIN", "SUPERADMIN"],
    ))
    superuser_roles = set(getattr(
        settings,
        "KEYCLOAK_DJANGO_SUPERUSER_ROLES",
        ["SUPERADMIN"],
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
