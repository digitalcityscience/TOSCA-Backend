"""Tests for the KeycloakRole population service (Epic 11 Phase 1)."""

import pytest

from tosca_api.apps.authentication.models import KeycloakRole
from tosca_api.apps.authentication.role_registry import (
    register_login_roles,
    sync_realm_roles,
    upsert_role,
)
from tosca_api.apps.authentication.role_sync import ExtractedRoles
from tosca_api.apps.organizations.models import Organization

pytestmark = pytest.mark.django_db


@pytest.fixture
def dcs():
    return Organization.objects.get(slug="dcs")  # seeded by an org migration


def _roles(*names):
    return ExtractedRoles(roles=set(names), authoritative=True, sources=["access_token"])


def test_upsert_creates_org_level_role(dcs):
    created = upsert_role("ROLE_DCS_READER", source=KeycloakRole.Source.LOGIN)
    assert created is True
    role = KeycloakRole.objects.get(name="ROLE_DCS_READER")
    assert role.organization == dcs
    assert role.project == ""
    assert role.level == "READER"
    assert role.source == KeycloakRole.Source.LOGIN


def test_upsert_parses_project(dcs):
    upsert_role("ROLE_DCS_TOSCA_WRITER", source=KeycloakRole.Source.KEYCLOAK_ADMIN)
    role = KeycloakRole.objects.get(name="ROLE_DCS_TOSCA_WRITER")
    assert role.project == "tosca"
    assert role.level == "WRITER"


def test_upsert_skips_non_conforming(dcs):
    assert upsert_role("kose-rol-test", source=KeycloakRole.Source.LOGIN) is None
    assert KeycloakRole.objects.count() == 0


def test_upsert_skips_unresolved_org():
    # No Organization with slug 'nope' -> not cataloged (never manufactured).
    assert upsert_role("ROLE_NOPE_READER", source=KeycloakRole.Source.LOGIN) is None
    assert KeycloakRole.objects.count() == 0


def test_upsert_is_idempotent_and_preserves_first_source(dcs):
    assert upsert_role("ROLE_DCS_ADMIN", source=KeycloakRole.Source.LOGIN) is True
    # Re-seen from a different source: no new row, original source preserved.
    assert upsert_role("ROLE_DCS_ADMIN", source=KeycloakRole.Source.KEYCLOAK_ADMIN) is False
    role = KeycloakRole.objects.get(name="ROLE_DCS_ADMIN")
    assert role.source == KeycloakRole.Source.LOGIN
    assert KeycloakRole.objects.count() == 1


def test_upsert_reactivates_a_deactivated_role(dcs):
    upsert_role("ROLE_DCS_READER", source=KeycloakRole.Source.LOGIN)
    KeycloakRole.objects.filter(name="ROLE_DCS_READER").update(is_active=False)
    upsert_role("ROLE_DCS_READER", source=KeycloakRole.Source.LOGIN)
    assert KeycloakRole.objects.get(name="ROLE_DCS_READER").is_active is True


def test_register_login_roles_catalogs_conforming_only(dcs):
    register_login_roles(_roles("ROLE_DCS_READER", "DJANGO_STAFF", "offline_access"))
    assert list(KeycloakRole.objects.values_list("name", flat=True)) == ["ROLE_DCS_READER"]


def test_register_login_roles_never_raises(monkeypatch):
    # Force the write path to blow up; login must survive.
    monkeypatch.setattr(
        "tosca_api.apps.authentication.role_registry.upsert_role",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    register_login_roles(_roles("ROLE_DCS_READER"))  # no exception propagates


def test_sync_realm_roles_counts_and_deactivates(dcs):
    # Pre-existing catalog row that will go stale.
    upsert_role("ROLE_DCS_OLD", source=KeycloakRole.Source.LOGIN)  # skipped: OLD not a level
    upsert_role("ROLE_DCS_READER", source=KeycloakRole.Source.LOGIN)

    summary = sync_realm_roles(
        ["ROLE_DCS_READER", "ROLE_DCS_TOSCA_WRITER", "kose-rol-test", "ROLE_NOPE_READER"],
    )
    assert summary["created"] == 1  # ROLE_DCS_TOSCA_WRITER
    assert summary["updated"] == 1  # ROLE_DCS_READER
    assert summary["skipped"] == 2  # kose-rol-test + ROLE_NOPE_READER
    assert summary["deactivated"] == 0  # only ROLE_DCS_READER pre-existed and is present
    assert KeycloakRole.objects.get(name="ROLE_DCS_TOSCA_WRITER").source == (
        KeycloakRole.Source.KEYCLOAK_ADMIN
    )


def test_sync_realm_roles_deactivates_missing(dcs):
    upsert_role("ROLE_DCS_READER", source=KeycloakRole.Source.LOGIN)
    summary = sync_realm_roles(["ROLE_DCS_WRITER"])
    assert summary["deactivated"] == 1
    assert KeycloakRole.objects.get(name="ROLE_DCS_READER").is_active is False


def test_sync_realm_roles_dry_run_writes_nothing(dcs):
    summary = sync_realm_roles(["ROLE_DCS_READER"], dry_run=True)
    assert summary["created"] == 1
    assert KeycloakRole.objects.count() == 0
