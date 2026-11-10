"""Which Django user a Keycloak identity is.

A Keycloak user is identified by ``sub`` (stable, unique per realm), never by
``preferred_username`` or email: usernames repeat across realms (the app moved
from ``tosca-dev`` to ``ngcn-prod``) and users can change both in Keycloak.
The link is the allauth ``SocialAccount`` row (provider ``keycloak``,
``uid`` = ``sub``) that the browser/admin login already creates.

An existing Django user found by username/email is linked to a Keycloak
identity only when that is safe:

* it is not already linked to a *different* Keycloak identity, and
* it is not a local account with its own password (e.g. ``createsuperuser``
  admins); those must never be taken over by whoever owns that username or
  email in Keycloak.
"""

import logging

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from rest_framework.exceptions import AuthenticationFailed

logger = logging.getLogger(__name__)
User = get_user_model()

KEYCLOAK_PROVIDER = "keycloak"


def has_local_password(user) -> bool:
    """True for accounts that log in with a Django password. Keycloak-created
    users have an empty or unusable password."""
    return bool(user.password) and user.has_usable_password()


def link_refusal(user, sub: str) -> str | None:
    """Why ``user`` must not be linked to Keycloak identity ``sub`` (None if it may)."""
    other = SocialAccount.objects.filter(user=user, provider=KEYCLOAK_PROVIDER).exclude(uid=sub).first()
    if other is not None:
        return "already linked to another Keycloak user"
    if has_local_password(user):
        return "local account with its own password"
    return None


def user_for_token_claims(claims: dict):
    """The Django user for a verified access token; links or creates it on first use."""
    sub = claims.get("sub")
    username = claims.get("preferred_username")
    if not sub:
        raise AuthenticationFailed("Invalid or expired token.")

    account = (
        SocialAccount.objects.select_related("user")
        .filter(provider=KEYCLOAK_PROVIDER, uid=sub)
        .first()
    )
    if account is not None:
        return account.user

    if not username:
        logger.warning("Keycloak token without preferred_username", extra={"sub": sub})
        raise AuthenticationFailed("Invalid or expired token.")

    user = User.objects.filter(username=username).first()
    if user is None:
        user = User(
            username=username,
            email=claims.get("email", "") or "",
            first_name=claims.get("given_name", "") or "",
            last_name=claims.get("family_name", "") or "",
        )
        user.set_unusable_password()
        user.save()
    else:
        refusal = link_refusal(user, sub)
        if refusal is not None:
            logger.error(
                "Refused to link Keycloak identity sub=%s iss=%s to existing user %r: %s. "
                "If it is the same person: manage.py link_keycloak_identity %s %s",
                sub, claims.get("iss"), username, refusal, username, sub,
                extra={"user_id": user.pk, "username": username, "sub": sub, "reason": refusal, "security": True},
            )
            raise AuthenticationFailed("This account cannot be used with this sign-in. Contact an administrator.")

    SocialAccount.objects.create(
        user=user,
        provider=KEYCLOAK_PROVIDER,
        uid=sub,
        extra_data={"linked_by": "bearer", "iss": claims.get("iss"), "preferred_username": username},
    )
    logger.info("Linked Keycloak identity to user", extra={"user_id": user.pk, "username": username, "sub": sub})
    return user
