"""Tests for the sync_keycloak_roles management command (Epic 11 Phase 1)."""

from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tosca_api.apps.authentication.keycloak_admin import KeycloakAdminError
from tosca_api.apps.authentication.models import KeycloakRole
from tosca_api.apps.organizations.models import Organization

pytestmark = pytest.mark.django_db

_LIST = "tosca_api.apps.authentication.management.commands.sync_keycloak_roles.list_realm_roles"


@pytest.fixture(autouse=True)
def dcs():
    return Organization.objects.get(slug="dcs")


def test_command_applies_sync():
    with patch(_LIST, return_value=["ROLE_DCS_READER", "ROLE_DCS_TOSCA_WRITER", "offline_access"]):
        call_command("sync_keycloak_roles")
    assert set(KeycloakRole.objects.values_list("name", flat=True)) == {
        "ROLE_DCS_READER",
        "ROLE_DCS_TOSCA_WRITER",
    }
    assert KeycloakRole.objects.get(name="ROLE_DCS_READER").source == (
        KeycloakRole.Source.KEYCLOAK_ADMIN
    )


def test_command_dry_run_writes_nothing():
    with patch(_LIST, return_value=["ROLE_DCS_READER"]):
        call_command("sync_keycloak_roles", "--dry-run")
    assert KeycloakRole.objects.count() == 0


def test_command_no_deactivate_keeps_stale_active():
    KeycloakRole.objects.create(
        name="ROLE_DCS_READER",
        organization=Organization.objects.get(slug="dcs"),
        level=KeycloakRole.Level.READER,
        source=KeycloakRole.Source.LOGIN,
    )
    with patch(_LIST, return_value=["ROLE_DCS_WRITER"]):
        call_command("sync_keycloak_roles", "--no-deactivate")
    assert KeycloakRole.objects.get(name="ROLE_DCS_READER").is_active is True


def test_command_surfaces_admin_errors():
    with patch(_LIST, side_effect=KeycloakAdminError("no token")):
        with pytest.raises(CommandError):
            call_command("sync_keycloak_roles")
