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
        "realm_access": {"roles": ["SUPERADMIN"]},
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


def test_social_data_extracts_roles_from_userinfo():
    roles = extract_roles_from_social_data({
        "userinfo": {
            "realm_access": {"roles": ["DJANGO_STAFF"]},
        },
    })

    assert roles.authoritative is True
    assert roles.roles == {"DJANGO_STAFF"}
