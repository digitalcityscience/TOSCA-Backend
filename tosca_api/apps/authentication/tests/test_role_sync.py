from unittest.mock import patch

import pytest

from tosca_api.apps.authentication.role_sync import (
    extract_roles_from_social_data,
    extract_roles_from_token,
    sync_user_permissions_from_roles,
)


@pytest.mark.django_db
def test_django_staff_role_grants_staff(django_user_model):
    user = django_user_model.objects.create_user(username="staff-user")
    roles = extract_roles_from_token({
        "realm_access": {"roles": ["DJANGO_STAFF"]},
    })

    changed = sync_user_permissions_from_roles(user, roles)

    user.refresh_from_db()
    assert changed is True
    assert user.is_staff is True
    assert user.is_superuser is False


@pytest.mark.django_db
def test_superadmin_role_grants_staff_and_superuser(django_user_model):
    user = django_user_model.objects.create_user(username="super-user")
    roles = extract_roles_from_token({
        "realm_access": {"roles": ["DJANGO_SUPERADMIN"]},
    })

    sync_user_permissions_from_roles(user, roles)

    user.refresh_from_db()
    assert user.is_staff is True
    assert user.is_superuser is True


@pytest.mark.django_db
def test_missing_role_claim_does_not_demote_existing_staff_user(django_user_model):
    user = django_user_model.objects.create_user(username="existing-staff", is_staff=True)
    roles = extract_roles_from_token({})

    changed = sync_user_permissions_from_roles(user, roles)

    user.refresh_from_db()
    assert changed is False
    assert user.is_staff is True
    assert user.is_superuser is False


@pytest.mark.django_db
def test_empty_authoritative_role_claim_demotes_staff_user(django_user_model):
    user = django_user_model.objects.create_user(
        username="demoted-staff",
        is_staff=True,
        is_superuser=True,
    )
    roles = extract_roles_from_token({
        "realm_access": {"roles": []},
    })

    changed = sync_user_permissions_from_roles(user, roles)

    user.refresh_from_db()
    assert changed is True
    assert user.is_staff is False
    assert user.is_superuser is False


@pytest.mark.django_db
def test_admin_platform_role_alone_does_not_grant_staff(django_user_model):
    """ADMIN is the GeoServer console escape valve, not a Django role
    (canonical §2 "Çakışma çözümü") -- only DJANGO_STAFF/DJANGO_SUPERADMIN do."""
    user = django_user_model.objects.create_user(username="admin-role-user")
    roles = extract_roles_from_token({
        "realm_access": {"roles": ["ADMIN"]},
    })

    changed = sync_user_permissions_from_roles(user, roles)

    user.refresh_from_db()
    assert changed is False
    assert user.is_staff is False
    assert user.is_superuser is False


def test_social_data_extracts_roles_from_userinfo():
    roles = extract_roles_from_social_data({
        "userinfo": {
            "realm_access": {"roles": ["DJANGO_STAFF"]},
        },
    })

    assert roles.authoritative is True
    assert roles.roles == {"DJANGO_STAFF"}


def test_social_data_falls_back_to_access_token_when_userinfo_and_id_token_lack_roles():
    """Regression: allauth's openid_connect provider never puts the access
    token in extra_data -- only ID token + userinfo. Keycloak's default
    "roles" client scope adds realm_access.roles to the access token, but
    "add to ID token"/"add to userinfo" mapper toggles are separate and are
    often off -- so without checking the access token too, browser login
    silently sees zero roles for everyone."""
    with patch(
        "tosca_api.apps.authentication.role_sync.verify_and_decode_token",
        return_value={"realm_access": {"roles": ["DJANGO_SUPERADMIN"]}},
    ) as mock_decode:
        roles = extract_roles_from_social_data(
            {
                "userinfo": {"sub": "abc123"},  # no realm_access
                "id_token": {"sub": "abc123"},  # no realm_access
            },
            access_token="fake.jwt.token",
        )

    mock_decode.assert_called_once_with("fake.jwt.token")
    assert roles.authoritative is True
    assert roles.roles == {"DJANGO_SUPERADMIN"}


def test_social_data_without_access_token_is_unaffected():
    roles = extract_roles_from_social_data({"userinfo": {"sub": "abc123"}})

    assert roles.authoritative is False
    assert roles.roles == set()
