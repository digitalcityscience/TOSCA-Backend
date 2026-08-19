"""Org-ownership enforcement for the geodata admin AJAX side-channel
endpoints (security tickets 2026-08-19 ticket 03).

Before this ticket these endpoints only checked ``request.user.is_staff`` --
a staff user scoped to one org could sync/clone/publish against another
org's workspace/store/layer purely by knowing its id. Each test below
proves the fix: an owning-org WRITER succeeds (or at least clears the
permission gate), a cross-org staff user is denied with 403/PermissionDenied
before any GeoServer/PostGIS work happens.
"""
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.geodata_providers.admin_views.engine import (
    engine_deactivate_view,
    engine_reactivate_view,
    engine_sync_view,
)
from tosca_api.apps.geodata_providers.admin_views.layer import (
    stores_for_workspace_view,
    tables_for_store_view,
)
from tosca_api.apps.geodata_providers.admin_views.store import store_postgis_tables_view
from tosca_api.apps.geodata_providers.admin_views.workspace import workspace_sync_view
from tosca_api.apps.geodata_providers.models import GeodataEngine, Store, Workspace
from tosca_api.apps.organizations.models import Organization

User = get_user_model()


@pytest.fixture
def factory():
    return RequestFactory()


@pytest.fixture
def dcs_org(db):
    org, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    return org


@pytest.fixture
def hpa_org(db):
    org, _ = Organization.objects.get_or_create(slug="hpa", defaults={"name": "HPA"})
    return org


@pytest.fixture
def engine(db):
    creator = User.objects.create_user(username="engine-owner")
    return GeodataEngine.objects.create(
        name="test-engine",
        engine_type="geoserver",
        base_url="http://example.com/geoserver",
        public_url="http://example.com/geoserver",
        admin_username="admin",
        admin_password="secret",
        created_by=creator,
    )


@pytest.fixture
def dcs_workspace(dcs_org, engine):
    creator = User.objects.create_user(username="dcs-ws-owner")
    return Workspace.objects.create(
        organization=dcs_org, geodata_engine=engine, name="dcs_ws", created_by=creator,
    )


@pytest.fixture
def dcs_store(dcs_workspace):
    creator = User.objects.create_user(username="dcs-store-owner")
    return Store.objects.create(
        workspace=dcs_workspace,
        name="dcs_store",
        store_type=Store.StoreType.POSTGIS,
        host="db",
        port=5432,
        database="gis",
        username="postgres",
        password="secret",
        schema="public",
        created_by=creator,
    )


def _staff_user(username, *, org=None, level=None, superuser=False):
    user = User.objects.create_user(username=username, is_staff=True, is_superuser=superuser)
    if org and level:
        user._auth_claims = AuthClaims(
            org_roles={org: level}, default_org=org, authoritative=True,
        )
    return user


# ---------------------------------------------------------------------------
# workspace_sync_view
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_workspace_sync_denies_cross_org_staff(factory, dcs_workspace):
    hpa_staff = _staff_user("hpa-staff", org="hpa", level="WRITER")
    request = factory.post(f"/admin/geodata_providers/workspace/{dcs_workspace.pk}/sync/")
    request.user = hpa_staff

    response = workspace_sync_view(request, dcs_workspace.pk)

    assert response.status_code == 403


@pytest.mark.django_db
@patch("tosca_api.apps.geodata_providers.admin_views.workspace.EngineClientFactory.create_sync_service")
def test_workspace_sync_allows_owning_org_writer(mock_create_sync_service, factory, dcs_workspace):
    mock_service = mock_create_sync_service.return_value
    mock_service.sync_workspace_resources.return_value = {
        "stores": {"errors": []},
        "styles": {"errors": []},
        "layers": {"errors": []},
    }
    dcs_staff = _staff_user("dcs-staff", org="dcs", level="WRITER")
    request = factory.post(f"/admin/geodata_providers/workspace/{dcs_workspace.pk}/sync/")
    request.user = dcs_staff

    response = workspace_sync_view(request, dcs_workspace.pk)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# store_postgis_tables_view
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_store_postgis_tables_denies_cross_org_staff(factory, dcs_store):
    hpa_staff = _staff_user("hpa-staff-2", org="hpa", level="READER")
    request = factory.get(f"/admin/geodata_providers/store/{dcs_store.pk}/postgis-tables/")
    request.user = hpa_staff

    response = store_postgis_tables_view(request, dcs_store.pk)

    assert response.status_code == 403


@pytest.mark.django_db
def test_store_postgis_tables_allows_owning_org_reader(factory, dcs_store):
    dcs_staff = _staff_user("dcs-staff-2", org="dcs", level="READER")
    request = factory.get(f"/admin/geodata_providers/store/{dcs_store.pk}/postgis-tables/")
    request.user = dcs_staff

    response = store_postgis_tables_view(request, dcs_store.pk)

    # Clears the org gate; fails later on missing DB credentials (400), not 403.
    assert response.status_code != 403


# ---------------------------------------------------------------------------
# stores_for_workspace_view / tables_for_store_view
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_stores_for_workspace_denies_cross_org_staff(factory, dcs_workspace):
    hpa_staff = _staff_user("hpa-staff-3", org="hpa", level="READER")
    request = factory.get(
        "/admin/geodata_providers/layer/stores-for-workspace/",
        {"workspace_id": str(dcs_workspace.pk)},
    )
    request.user = hpa_staff

    response = stores_for_workspace_view(request)

    assert response.status_code == 403


@pytest.mark.django_db
def test_tables_for_store_denies_cross_org_staff(factory, dcs_store):
    hpa_staff = _staff_user("hpa-staff-4", org="hpa", level="READER")
    request = factory.get(
        "/admin/geodata_providers/layer/tables-for-store/",
        {"store_id": str(dcs_store.pk)},
    )
    request.user = hpa_staff

    response = tables_for_store_view(request)

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Shared-engine actions -- superuser only (no single owning org)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_engine_sync_denies_non_superuser_staff(factory, engine, dcs_org):
    dcs_staff = _staff_user("dcs-staff-3", org="dcs", level="ADMIN")
    request = factory.post(f"/admin/geodata_providers/geodataengine/{engine.pk}/sync/")
    request.user = dcs_staff

    response = engine_sync_view(request, engine.pk)

    assert response.status_code == 403


@pytest.mark.django_db
def test_engine_deactivate_denies_non_superuser_staff(factory, engine, dcs_org):
    dcs_staff = _staff_user("dcs-staff-4", org="dcs", level="ADMIN")
    request = factory.post(f"/admin/geodata_providers/geodataengine/{engine.pk}/deactivate/")
    request.user = dcs_staff

    with pytest.raises(PermissionDenied):
        engine_deactivate_view(request, engine.pk)


@pytest.mark.django_db
def test_engine_reactivate_denies_non_superuser_staff(factory, engine, dcs_org):
    dcs_staff = _staff_user("dcs-staff-5", org="dcs", level="ADMIN")
    request = factory.post(f"/admin/geodata_providers/geodataengine/{engine.pk}/reactivate/")
    request.user = dcs_staff

    with pytest.raises(PermissionDenied):
        engine_reactivate_view(request, engine.pk)
