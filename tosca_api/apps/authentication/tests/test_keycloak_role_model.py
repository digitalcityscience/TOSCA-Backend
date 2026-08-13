"""Tests for the KeycloakRole registry model (Epic 11 Phase 1)."""

import pytest
from django.db import IntegrityError

from tosca_api.apps.authentication.models import KeycloakRole
from tosca_api.apps.organizations.models import Organization

pytestmark = pytest.mark.django_db


@pytest.fixture
def dcs():
    # 'dcs' is seeded by an organizations migration; reuse it.
    return Organization.objects.get(slug="dcs")


def test_defaults_active_and_timestamps(dcs):
    role = KeycloakRole.objects.create(
        name="ROLE_DCS_READER",
        organization=dcs,
        level=KeycloakRole.Level.READER,
        source=KeycloakRole.Source.LOGIN,
    )
    assert role.is_active is True
    assert role.project == ""  # org-level role has no project sub-scope
    assert role.first_seen_at is not None
    assert role.last_seen_at is not None
    assert str(role) == "ROLE_DCS_READER"


def test_project_scoped_role(dcs):
    role = KeycloakRole.objects.create(
        name="ROLE_DCS_TOSCA_WRITER",
        organization=dcs,
        project="tosca",
        level=KeycloakRole.Level.WRITER,
        source=KeycloakRole.Source.KEYCLOAK_ADMIN,
    )
    assert role.organization == dcs
    assert role.project == "tosca"
    assert role in dcs.keycloak_roles.all()


def test_name_is_unique(dcs):
    KeycloakRole.objects.create(
        name="ROLE_DCS_WRITER",
        organization=dcs,
        level=KeycloakRole.Level.WRITER,
        source=KeycloakRole.Source.KEYCLOAK_ADMIN,
    )
    with pytest.raises(IntegrityError):
        KeycloakRole.objects.create(
            name="ROLE_DCS_WRITER",
            organization=dcs,
            level=KeycloakRole.Level.WRITER,
            source=KeycloakRole.Source.LOGIN,
        )


def test_deleting_org_cascades_to_its_roles():
    org = Organization.objects.create(name="GQ2", slug="gq2")
    KeycloakRole.objects.create(
        name="ROLE_GQ2_ADMIN",
        organization=org,
        level=KeycloakRole.Level.ADMIN,
        source=KeycloakRole.Source.KEYCLOAK_ADMIN,
    )
    org.delete()
    assert not KeycloakRole.objects.filter(name="ROLE_GQ2_ADMIN").exists()
