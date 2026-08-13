"""Tests for the KeycloakRole registry model (Epic 11 Phase 1)."""

import pytest
from django.db import IntegrityError

from tosca_api.apps.authentication.models import KeycloakRole
from tosca_api.apps.organizations.models import Organization

pytestmark = pytest.mark.django_db


def test_defaults_active_and_timestamps():
    role = KeycloakRole.objects.create(
        name="ROLE_DCS_READER", source=KeycloakRole.Source.LOGIN
    )
    assert role.is_active is True
    assert role.first_seen_at is not None
    assert role.last_seen_at is not None
    assert str(role) == "ROLE_DCS_READER"


def test_name_is_unique():
    KeycloakRole.objects.create(
        name="ROLE_DCS_WRITER", source=KeycloakRole.Source.KEYCLOAK_ADMIN
    )
    with pytest.raises(IntegrityError):
        KeycloakRole.objects.create(
            name="ROLE_DCS_WRITER", source=KeycloakRole.Source.LOGIN
        )


def test_organization_link_is_optional_and_nullable_on_delete():
    org = Organization.objects.create(name="GQ2", slug="gq2")
    role = KeycloakRole.objects.create(
        name="ROLE_GQ2_ADMIN",
        source=KeycloakRole.Source.KEYCLOAK_ADMIN,
        organization=org,
    )
    assert role in org.keycloak_roles.all()

    org.delete()
    role.refresh_from_db()
    assert role.organization is None  # SET_NULL keeps the catalog row
