"""Unit tests for org/role extraction, level-map, and login coherence checks
(epic-11 ticket 06, covering role_sync.py from tickets 03/04). All fixtures
are decoded-token dicts -- no Keycloak, no network.
"""

import pytest

from tosca_api.apps.authentication.role_sync import (
    ExtractedOrg,
    ExtractedRoles,
    extract_org_from_token,
    extract_roles_from_token,
    org_role_level,
    run_org_login_checks,
)


# ---------------------------------------------------------------------------
# extract_org_from_token
# ---------------------------------------------------------------------------

def test_extract_org_from_token_reads_scalar_default_organization():
    org = extract_org_from_token({"default_organization": "dcs"})

    assert org.present is True
    assert org.default_slug == "dcs"


def test_extract_org_from_token_absent_claim_is_not_present():
    org = extract_org_from_token({"realm_access": {"roles": ["DJANGO_STAFF"]}})

    assert org.present is False
    assert org.default_slug is None


# ---------------------------------------------------------------------------
# extract_roles_from_token
# ---------------------------------------------------------------------------

def test_extract_roles_from_token_reads_realm_access_roles():
    roles = extract_roles_from_token({"realm_access": {"roles": ["ROLE_DCS_WRITER"]}})

    assert roles.authoritative is True
    assert roles.roles == {"ROLE_DCS_WRITER"}


# ---------------------------------------------------------------------------
# org_role_level
# ---------------------------------------------------------------------------

def test_org_role_level_writer():
    assert org_role_level({"ROLE_DCS_WRITER"}, "dcs") == "WRITER"


def test_org_role_level_highest_level_wins():
    assert org_role_level({"ROLE_DCS_READER", "ROLE_DCS_ADMIN"}, "dcs") == "ADMIN"


def test_org_role_level_unrelated_slug_returns_none():
    assert org_role_level({"ROLE_DCS_WRITER"}, "gq") is None


def test_org_role_level_no_org_slug_returns_none():
    assert org_role_level({"ROLE_DCS_WRITER"}, None) is None


def test_org_role_level_slug_is_atomic_not_parsed():
    """`ROLE_DCS_X_READER` is the role for org slug `dcs_x`, not a `_READER`
    suffix on org `dcs` (canonical: slug is derived into role names, never
    parsed back out of one)."""
    roles = {"ROLE_DCS_X_READER"}

    assert org_role_level(roles, "dcs") is None
    assert org_role_level(roles, "dcs_x") == "READER"


# ---------------------------------------------------------------------------
# run_org_login_checks
# ---------------------------------------------------------------------------

def _roles(*role_names, authoritative=True):
    return ExtractedRoles(roles=set(role_names), authoritative=authoritative, sources=["test"])


def _org(slug, present=True):
    return ExtractedOrg(default_slug=slug, present=present, sources=["test"] if present else [])


@pytest.mark.django_db
def test_login_check_no_org_warns_but_does_not_block(django_user_model):
    user = django_user_model.objects.create_user(username="no-org-user")

    warnings = run_org_login_checks(user, _roles("ROLE_DCS_WRITER"), _org(None, present=False))

    assert warnings == ["no_org"]


@pytest.mark.django_db
def test_login_check_org_without_role_warns(django_user_model):
    user = django_user_model.objects.create_user(username="orgless-role-user")

    warnings = run_org_login_checks(user, _roles("ROLE_GQ_WRITER"), _org("dcs"))

    assert warnings == ["org_without_role"]


@pytest.mark.django_db
def test_login_check_org_with_role_has_no_warnings(django_user_model):
    user = django_user_model.objects.create_user(username="coherent-user")

    warnings = run_org_login_checks(user, _roles("ROLE_DCS_WRITER"), _org("dcs"))

    assert warnings == []


@pytest.mark.django_db
def test_login_check_exempt_role_never_warns(django_user_model):
    user = django_user_model.objects.create_user(username="exempt-djangosuperadmin")

    warnings = run_org_login_checks(user, _roles("DJANGO_SUPERADMIN"), _org(None, present=False))

    assert warnings == []


@pytest.mark.django_db
def test_login_check_org_less_django_staff_still_warns(django_user_model):
    """2026-08-19 incident fix: DJANGO_STAFF is no longer platform-exempt, so
    an org-less DJANGO_STAFF login surfaces the same no_org warning any other
    org-less user would get."""
    user = django_user_model.objects.create_user(username="orgless-django-staff")

    warnings = run_org_login_checks(user, _roles("DJANGO_STAFF"), _org(None, present=False))

    assert warnings == ["no_org"]
