"""Bearer tokens map to Django users by Keycloak ``sub``, never by username alone."""

import pytest
from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory

from tosca_api.apps.authentication.backends import KeycloakTokenAuthentication
from tosca_api.apps.authentication.identity import KEYCLOAK_PROVIDER, user_for_token_claims

User = get_user_model()
pytestmark = pytest.mark.django_db


def claims(sub="sub-1", username="alice", **extra):
    return {"sub": sub, "preferred_username": username, "iss": "https://kc.test/realms/ngcn-prod", **extra}


def test_first_token_creates_a_user_without_password_and_links_it():
    user = user_for_token_claims(claims(email="alice@example.org"))
    assert user.username == "alice"
    assert not user.has_usable_password()
    assert SocialAccount.objects.filter(user=user, provider=KEYCLOAK_PROVIDER, uid="sub-1").exists()


def test_the_link_wins_over_the_username():
    alice = user_for_token_claims(claims())
    # Alice renamed herself in Keycloak: same sub, new username.
    assert user_for_token_claims(claims(username="alice2")) == alice


def test_a_local_password_account_is_never_taken_over():
    User.objects.create_superuser("admin", "admin@example.org", "s3cret-password")
    with pytest.raises(AuthenticationFailed):
        user_for_token_claims(claims(sub="attacker", username="admin"))
    assert not SocialAccount.objects.filter(uid="attacker").exists()


def test_same_username_from_another_realm_is_not_merged():
    user_for_token_claims(claims(sub="tosca-dev-kose", username="kose"))
    with pytest.raises(AuthenticationFailed):
        user_for_token_claims(claims(sub="ngcn-prod-kose", username="kose"))


def test_existing_keycloak_user_without_link_is_linked_once():
    legacy = User.objects.create(username="bob")  # created by the old bearer code: empty password
    assert user_for_token_claims(claims(sub="bob-sub", username="bob")) == legacy
    assert SocialAccount.objects.filter(user=legacy, uid="bob-sub").exists()


def test_failed_authentication_answers_401():
    request = APIRequestFactory().get("/api/v1/stories/")
    assert KeycloakTokenAuthentication().authenticate_header(request).startswith("Bearer")
