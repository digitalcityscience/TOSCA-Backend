"""Tests for the two auth entry points' claims attachment (security tickets
ticket 05, Q11): Bearer/API attaches request-local claims and never persists
them; browser/admin attaches the same request-local claims *and* persists a
``UserAuthorizationSnapshot`` per the authoritative write rule.
"""

from __future__ import annotations

import pytest

from tosca_api.apps.authentication.backends import KeycloakAdapter, KeycloakTokenAuthentication
from tosca_api.apps.authentication.role_sync import ExtractedOrg, ExtractedRoles
from tosca_api.apps.organizations.models import UserAuthorizationSnapshot


def _roles(*role_names, authoritative=True):
    return ExtractedRoles(roles=set(role_names), authoritative=authoritative, sources=["test"])


def _org(slug, present=True):
    return ExtractedOrg(default_slug=slug, present=present, sources=["test"] if present else [])


@pytest.mark.django_db
def test_bearer_apply_permissions_attaches_live_claims_without_persisting(django_user_model):
    user = django_user_model.objects.create_user(username="bearer-claims-user")
    backend = KeycloakTokenAuthentication()

    backend._apply_permissions(user, _roles("ROLE_DCS_ADMIN"), _org("dcs"))

    assert user._auth_claims.org_roles == {"dcs": "ADMIN"}
    assert user._auth_claims.default_org == "dcs"
    assert not UserAuthorizationSnapshot.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_browser_apply_permissions_attaches_claims_and_persists_snapshot(django_user_model):
    user = django_user_model.objects.create_user(username="browser-claims-user")
    adapter = KeycloakAdapter()

    adapter._apply_permissions(user, _roles("ROLE_QG2_WRITER"), _org("qg2"))

    assert user._auth_claims.org_roles == {"qg2": "WRITER"}
    snapshot = UserAuthorizationSnapshot.objects.get(user=user)
    assert snapshot.org_roles == {"qg2": "WRITER"}
    assert snapshot.default_org == "qg2"


@pytest.mark.django_db
def test_browser_apply_permissions_non_authoritative_does_not_clobber_snapshot(django_user_model):
    user = django_user_model.objects.create_user(username="browser-stale-user")
    adapter = KeycloakAdapter()
    adapter._apply_permissions(user, _roles("ROLE_DCS_ADMIN"), _org("dcs"))

    # A later login where the roles mapper failed to populate realm_access.
    adapter._apply_permissions(user, _roles(authoritative=False), _org(None, present=False))

    snapshot = UserAuthorizationSnapshot.objects.get(user=user)
    assert snapshot.org_roles == {"dcs": "ADMIN"}
    assert snapshot.default_org == "dcs"
