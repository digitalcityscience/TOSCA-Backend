"""Tests for the ticket-05 unified authorization resolver.

Precedence: request-local live claims (``user._auth_claims``) -> persisted
``UserAuthorizationSnapshot`` -> fail closed. Covers ``policy.user_claims``,
``policy.sync_snapshot``'s authoritative write rule, and the
``get_request_org_context`` browser/admin fallback repointed at the
snapshot instead of ``SocialAccount.extra_data``.
"""

from __future__ import annotations

import pytest
from django.test import RequestFactory
from django.utils import timezone

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.organizations.models import UserAuthorizationSnapshot
from tosca_api.apps.organizations.permissions import get_request_org_context
from tosca_api.apps.organizations.policy import sync_snapshot, user_claims


@pytest.fixture
def user(django_user_model, db):
    return django_user_model.objects.create_user(username="resolver-user")


def _request(user, auth=None):
    request = RequestFactory().get("/fake/")
    request.user = user
    request.auth = auth
    return request


# ---------------------------------------------------------------------------
# policy.user_claims precedence
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_user_claims_fail_closed_when_no_live_claims_and_no_snapshot(user):
    assert user_claims(user) == ({}, None)


@pytest.mark.django_db
def test_user_claims_falls_back_to_snapshot_when_no_live_claims(user):
    """A new browser request after login has no request-local claims of its
    own -- it must read back what the login persisted."""
    UserAuthorizationSnapshot.objects.create(
        user=user, org_roles={"dcs": "ADMIN"}, default_org="dcs", synced_at=timezone.now()
    )

    assert user_claims(user) == ({"dcs": "ADMIN"}, "dcs")


@pytest.mark.django_db
def test_user_claims_prefers_live_claims_over_snapshot(user):
    UserAuthorizationSnapshot.objects.create(
        user=user, org_roles={"dcs": "READER"}, default_org="dcs", synced_at=timezone.now()
    )
    user._auth_claims = AuthClaims(
        org_roles={"qg2": "ADMIN"}, default_org="qg2", authoritative=True
    )

    assert user_claims(user) == ({"qg2": "ADMIN"}, "qg2")


@pytest.mark.django_db
def test_user_claims_no_snapshot_default_org_normalizes_to_none(user):
    UserAuthorizationSnapshot.objects.create(
        user=user, org_roles={}, default_org="", synced_at=timezone.now()
    )

    assert user_claims(user) == ({}, None)


# ---------------------------------------------------------------------------
# policy.sync_snapshot authoritative write rule
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_sync_snapshot_writes_when_authoritative_non_empty(user):
    claims = AuthClaims(org_roles={"dcs": "WRITER"}, default_org="dcs", authoritative=True)

    sync_snapshot(user, claims)

    snapshot = UserAuthorizationSnapshot.objects.get(user=user)
    assert snapshot.org_roles == {"dcs": "WRITER"}
    assert snapshot.default_org == "dcs"


@pytest.mark.django_db
def test_sync_snapshot_writes_empty_when_authoritative_empty(user):
    UserAuthorizationSnapshot.objects.create(
        user=user, org_roles={"dcs": "ADMIN"}, default_org="dcs", synced_at=timezone.now()
    )
    claims = AuthClaims(org_roles={}, default_org=None, authoritative=True)

    sync_snapshot(user, claims)

    snapshot = UserAuthorizationSnapshot.objects.get(user=user)
    assert snapshot.org_roles == {}
    assert snapshot.default_org == ""


@pytest.mark.django_db
def test_sync_snapshot_does_not_overwrite_when_non_authoritative(user):
    UserAuthorizationSnapshot.objects.create(
        user=user, org_roles={"dcs": "ADMIN"}, default_org="dcs", synced_at=timezone.now()
    )
    claims = AuthClaims(org_roles={}, default_org=None, authoritative=False)

    sync_snapshot(user, claims)

    snapshot = UserAuthorizationSnapshot.objects.get(user=user)
    assert snapshot.org_roles == {"dcs": "ADMIN"}
    assert snapshot.default_org == "dcs"


@pytest.mark.django_db
def test_sync_snapshot_non_authoritative_does_not_create_row(user):
    claims = AuthClaims(org_roles={}, default_org=None, authoritative=False)

    sync_snapshot(user, claims)

    assert not UserAuthorizationSnapshot.objects.filter(user=user).exists()


# ---------------------------------------------------------------------------
# get_request_org_context: browser/admin fallback repointed at the snapshot
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_get_request_org_context_browser_uses_snapshot_when_no_live_claims(user):
    """New browser request after login: no request.auth, no request-local
    claims -- must read the persisted snapshot, not SocialAccount.extra_data."""
    UserAuthorizationSnapshot.objects.create(
        user=user, org_roles={"dcs": "WRITER"}, default_org="dcs", synced_at=timezone.now()
    )

    roles, org_slug, exempt = get_request_org_context(_request(user))

    assert roles == {"ROLE_DCS_WRITER"}
    assert org_slug == "dcs"
    assert exempt is False


@pytest.mark.django_db
def test_get_request_org_context_browser_fail_closed_without_snapshot(user):
    roles, org_slug, exempt = get_request_org_context(_request(user))

    assert roles == set()
    assert org_slug is None
    assert exempt is False


@pytest.mark.django_db
def test_get_request_org_context_browser_exempt_for_superuser_without_org_role(django_user_model, db):
    superuser = django_user_model.objects.create_superuser(
        username="resolver-superuser", email="root@example.com", password="x"
    )

    _roles, _org_slug, exempt = get_request_org_context(_request(superuser))

    assert exempt is True


@pytest.mark.django_db
def test_get_request_org_context_bearer_overrides_stored_snapshot(user):
    """A fresh Bearer token is always the freshest source and must win over
    whatever a stale persisted snapshot says (Q11)."""
    UserAuthorizationSnapshot.objects.create(
        user=user, org_roles={"dcs": "READER"}, default_org="dcs", synced_at=timezone.now()
    )
    auth = {
        "realm_access": {"roles": ["ROLE_DCS_ADMIN"]},
        "default_organization": "dcs",
    }

    roles, org_slug, exempt = get_request_org_context(_request(user, auth=auth))

    assert roles == {"ROLE_DCS_ADMIN"}
    assert org_slug == "dcs"
    assert exempt is False
