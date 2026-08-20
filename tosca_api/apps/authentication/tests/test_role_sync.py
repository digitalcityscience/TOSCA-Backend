from unittest.mock import patch

import pytest

from tosca_api.apps.authentication.role_sync import (
    ExtractedOrg,
    ExtractedRoles,
    build_auth_claims,
    denormalize_org_roles,
    extract_roles_from_social_data,
    extract_roles_from_token,
    normalize_org_roles,
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


# ---------------------------------------------------------------------------
# normalize_org_roles / denormalize_org_roles (security tickets ticket 05)
# ---------------------------------------------------------------------------

def test_normalize_org_roles_multiple_orgs():
    org_roles = normalize_org_roles({"ROLE_DCS_READER", "ROLE_QG2_ADMIN"})

    assert org_roles == {"dcs": "READER", "qg2": "ADMIN"}


def test_normalize_org_roles_picks_highest_level_per_org():
    """A composite Keycloak grant can carry more than one level for the same
    org (e.g. both WRITER and ADMIN roles); the highest rank wins."""
    org_roles = normalize_org_roles({"ROLE_DCS_WRITER", "ROLE_DCS_ADMIN"})

    assert org_roles == {"dcs": "ADMIN"}


def test_normalize_org_roles_ignores_project_scoped_and_noise_roles():
    org_roles = normalize_org_roles({
        "ROLE_DCS_TOSCA_WRITER",  # project-scoped, out of scope
        "DJANGO_STAFF",  # platform noise, not ROLE_-conforming
        "offline_access",
        "ROLE_DCS_READER",
    })

    assert org_roles == {"dcs": "READER"}


def test_denormalize_org_roles_roundtrips_normalize():
    original = {"ROLE_DCS_ADMIN", "ROLE_QG2_READER"}

    assert denormalize_org_roles(normalize_org_roles(original)) == original


# ---------------------------------------------------------------------------
# build_auth_claims
# ---------------------------------------------------------------------------

def test_build_auth_claims_combines_roles_and_default_org():
    roles = ExtractedRoles(roles={"ROLE_DCS_WRITER"}, authoritative=True, sources=["access_token"])
    org = ExtractedOrg(default_slug="dcs", present=True, sources=["access_token"])

    claims = build_auth_claims(roles, org)

    assert claims.org_roles == {"dcs": "WRITER"}
    assert claims.default_org == "dcs"
    assert claims.authoritative is True
    assert claims.platform_exempt is False


def test_build_auth_claims_non_authoritative_when_roles_missing():
    roles = ExtractedRoles(roles=set(), authoritative=False, sources=[])
    org = ExtractedOrg(default_slug=None, present=False, sources=[])

    claims = build_auth_claims(roles, org)

    assert claims.org_roles == {}
    assert claims.default_org is None
    assert claims.authoritative is False


def test_build_auth_claims_captures_platform_exempt_role():
    """Security tickets ticket 07 fix: platform-role membership is captured
    as real claims data, not left to be inferred later from
    user.is_staff/is_superuser (which can drift from Keycloak)."""
    roles = ExtractedRoles(roles={"ROLE_DCS_WRITER", "DJANGO_SUPERADMIN"}, authoritative=True, sources=["access_token"])
    org = ExtractedOrg(default_slug="dcs", present=True, sources=["access_token"])

    claims = build_auth_claims(roles, org)

    assert claims.platform_exempt is True


def test_build_auth_claims_django_staff_alone_is_not_platform_exempt():
    """2026-08-19 incident fix: DJANGO_STAFF grants admin-UI access only, not
    a global org-scoping bypass -- only DJANGO_SUPERADMIN is exempt now."""
    roles = ExtractedRoles(roles={"ROLE_HPA_WRITER", "DJANGO_STAFF"}, authoritative=True, sources=["access_token"])
    org = ExtractedOrg(default_slug="hpa", present=True, sources=["access_token"])

    claims = build_auth_claims(roles, org)

    assert claims.platform_exempt is False


def test_build_auth_claims_platform_exempt_false_without_the_role():
    roles = ExtractedRoles(roles={"ROLE_DCS_ADMIN"}, authoritative=True, sources=["access_token"])
    org = ExtractedOrg(default_slug="dcs", present=True, sources=["access_token"])

    claims = build_auth_claims(roles, org)

    assert claims.platform_exempt is False
