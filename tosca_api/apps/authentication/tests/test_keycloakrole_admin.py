"""Tests for the 'Sync with Keycloak' admin button (epic-11 Phase 2).

The button runs both hops: Keycloak -> catalog, then catalog reader/writer ->
GeoServer. Both hops are patched at their source modules (the admin view imports
them lazily inside the handler).
"""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from tosca_api.apps.authentication.keycloak_admin import KeycloakAdminError

pytestmark = pytest.mark.django_db

_URL = reverse("admin:tosca_authentication_keycloakrole_sync_with_keycloak")
_LIST = "tosca_api.apps.authentication.keycloak_admin.list_realm_roles"
_RECON = "tosca_api.apps.geodata_providers.role_sync.reconcile_all_engines"
_CHANGELIST = reverse("admin:tosca_authentication_keycloakrole_changelist")


@pytest.fixture
def admin_client():
    User.objects.create_superuser(username="root", email="r@x.io", password="pw")
    c = Client()
    c.force_login(User.objects.get(username="root"))
    return c


def test_button_runs_both_hops(admin_client):
    with patch(_LIST, return_value=["ROLE_DCS_READER"]) as mock_list, \
         patch(_RECON, return_value=[]) as mock_recon:
        resp = admin_client.post(_URL)

    assert resp.status_code == 302
    assert resp.url == _CHANGELIST
    mock_list.assert_called_once()          # hop 1
    mock_recon.assert_called_once()         # hop 2


def test_keycloak_failure_aborts_before_geoserver(admin_client):
    with patch(_LIST, side_effect=KeycloakAdminError("no token")), \
         patch(_RECON) as mock_recon:
        resp = admin_client.post(_URL)

    assert resp.status_code == 302
    mock_recon.assert_not_called()          # hop 2 never runs if hop 1 fails


def test_button_requires_auth():
    resp = Client().post(_URL)
    assert resp.status_code in (302, 403)   # redirected to admin login / denied


def test_changelist_is_viewable_but_readonly(admin_client):
    # Visible (view permission) + the sync button is rendered...
    resp = admin_client.get(_CHANGELIST)
    assert resp.status_code == 200
    assert b"Sync with Keycloak" in resp.content


def test_catalog_is_not_editable():
    from django.contrib import admin as dj_admin

    from tosca_api.apps.authentication.models import KeycloakRole

    # ...but the catalog is a read-only mirror: no add / change / delete.
    inst = dj_admin.site._registry[KeycloakRole]
    assert inst.has_add_permission(None) is False
    assert inst.has_change_permission(None) is False
    assert inst.has_delete_permission(None) is False
