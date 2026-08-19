"""Store DRF permission-matrix tests (security tickets ticket 11, A9).

Before this ticket, ``StoreViewSet`` had no org-scoping at all (just
``IsAuthenticated``) -- any authenticated user, in any org, could read or
write any other org's Store rows (which carry PostGIS connection
credentials). This closes that gap the same way ticket 08 closed it for
Campaign: ``ViewGatedModelPermissions`` (gate A, via ``has_perm()``) +
``WorkspaceOwnedScopedPermission`` (gate C, org reached through the owning
Workspace since Store has no direct ``organization`` FK).

``tosca_api/apps/geodata_providers/api/urls.py`` is not currently mounted in
``tosca_api/urls.py`` (see ``test_api_views.py``'s note), so ``StoreViewSet``
is exercised by invoking it directly via ``APIRequestFactory``, not
``reverse()``/``self.client`` -- mirrors ``test_workspace_permission_matrix.py``.
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.geodata_providers.api.views import StoreViewSet
from tosca_api.apps.geodata_providers.models import Store, Workspace
from tosca_api.apps.organizations.models import Organization, OrganizationAppEntitlement

User = get_user_model()


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.fixture
def dcs_org(db):
    org, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    return org


@pytest.fixture
def gq_org(db):
    org, _ = Organization.objects.get_or_create(slug="gq", defaults={"name": "GQ"})
    OrganizationAppEntitlement.objects.get_or_create(organization=org, app_label="geodata_providers")
    return org


def _workspace(org, name):
    creator = User.objects.create_user(username=f"{name}-owner")
    return Workspace.objects.create(organization=org, name=name, created_by=creator)


def _store(workspace, name):
    creator = User.objects.create_user(username=f"{name}-owner")
    return Store.objects.create(
        workspace=workspace,
        name=name,
        store_type=Store.StoreType.POSTGIS,
        host="localhost",
        database="db",
        username="user",
        created_by=creator,
    )


@pytest.fixture
def dcs_workspace(dcs_org):
    return _workspace(dcs_org, "dcs-workspace")


@pytest.fixture
def gq_workspace(gq_org):
    return _workspace(gq_org, "gq-workspace")


@pytest.fixture
def dcs_store(dcs_workspace):
    return _store(dcs_workspace, "dcs-store")


@pytest.fixture
def gq_store(gq_workspace):
    return _store(gq_workspace, "gq-store")


def _token(*roles, org_slug="dcs"):
    return {"realm_access": {"roles": list(roles)}, "default_organization": org_slug}


def _authenticated_request(factory, method, path, data, username, level, org_slug="dcs"):
    user = User.objects.create_user(username=username)
    roles = [f"ROLE_{org_slug.upper()}_{level}"] if level else []
    if level:
        user._auth_claims = AuthClaims(
            org_roles={org_slug: level}, default_org=org_slug, authoritative=True
        )
    request = getattr(factory, method)(path, data, format="json") if data is not None else getattr(factory, method)(path)
    force_authenticate(request, user=user, token=_token(*roles, org_slug=org_slug))
    return request


retrieve_view = StoreViewSet.as_view({"get": "retrieve"})
list_view = StoreViewSet.as_view({"get": "list"})
update_view = StoreViewSet.as_view({"patch": "partial_update"})
destroy_view = StoreViewSet.as_view({"delete": "destroy"})


# ---------------------------------------------------------------------------
# Role-matrix: READER / WRITER / ADMIN, own org
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_reader_can_read_own_org_store(factory, dcs_store):
    request = _authenticated_request(factory, "get", "/stores/", None, "reader-own", "READER")
    response = retrieve_view(request, pk=str(dcs_store.pk))
    assert response.status_code == 200


@pytest.mark.django_db
def test_reader_cannot_write_own_org_store(factory, dcs_store):
    request = _authenticated_request(
        factory, "patch", "/stores/", {"description": "hacked"}, "reader-write", "READER"
    )
    response = update_view(request, pk=str(dcs_store.pk))
    assert response.status_code == 403


@pytest.mark.django_db
def test_writer_can_change_own_org_store(factory, dcs_store):
    request = _authenticated_request(
        factory, "patch", "/stores/", {"description": "updated"}, "writer-change", "WRITER"
    )
    response = update_view(request, pk=str(dcs_store.pk))
    assert response.status_code == 200
    dcs_store.refresh_from_db()
    assert dcs_store.description == "updated"


@pytest.mark.django_db
def test_writer_cannot_delete_own_org_store(factory, dcs_store):
    request = _authenticated_request(factory, "delete", "/stores/", None, "writer-delete", "WRITER")
    response = destroy_view(request, pk=str(dcs_store.pk))
    assert response.status_code == 403
    assert Store.objects.filter(pk=dcs_store.pk).exists()


@pytest.mark.django_db
def test_admin_can_delete_own_org_store(factory, dcs_store):
    request = _authenticated_request(factory, "delete", "/stores/", None, "admin-delete", "ADMIN")
    response = destroy_view(request, pk=str(dcs_store.pk))
    assert response.status_code == 200
    assert not Store.objects.filter(pk=dcs_store.pk).exists()


@pytest.mark.django_db
def test_no_role_denied(factory, dcs_store):
    request = _authenticated_request(factory, "get", "/stores/", None, "no-role", None)
    response = retrieve_view(request, pk=str(dcs_store.pk))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Cross-org isolation (gate C, reached through Store.workspace.organization)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_cross_org_retrieve_is_404(factory, gq_store):
    request = _authenticated_request(
        factory, "get", "/stores/", None, "dcs-admin-crossorg", "ADMIN", org_slug="dcs"
    )
    response = retrieve_view(request, pk=str(gq_store.pk))
    assert response.status_code == 404


@pytest.mark.django_db
def test_cross_org_write_is_404(factory, gq_store):
    request = _authenticated_request(
        factory, "patch", "/stores/", {"description": "hacked"}, "dcs-writer-crossorg", "WRITER", org_slug="dcs"
    )
    response = update_view(request, pk=str(gq_store.pk))
    assert response.status_code == 404


@pytest.mark.django_db
def test_cross_org_delete_is_404(factory, gq_store):
    request = _authenticated_request(
        factory, "delete", "/stores/", None, "dcs-admin-crossorg-del", "ADMIN", org_slug="dcs"
    )
    response = destroy_view(request, pk=str(gq_store.pk))
    assert response.status_code == 404
    assert Store.objects.filter(pk=gq_store.pk).exists()


@pytest.mark.django_db
def test_list_excludes_other_org_stores(factory, dcs_store, gq_store):
    request = _authenticated_request(
        factory, "get", "/stores/", None, "dcs-reader-list", "READER", org_slug="dcs"
    )
    response = list_view(request)
    assert response.status_code == 200
    results = response.data["results"] if isinstance(response.data, dict) else response.data
    ids = {row["id"] for row in results}
    assert str(dcs_store.id) in ids
    assert str(gq_store.id) not in ids


# ---------------------------------------------------------------------------
# Cross-org create (workspace payload points at another org's workspace)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_cross_org_create_denied(factory, gq_workspace):
    create_view = StoreViewSet.as_view({"post": "create"})
    payload = {
        "workspace": str(gq_workspace.pk),
        "name": "cross-org-store",
        "store_type": "postgis",
        "host": "localhost",
        "database": "db",
        "username": "user",
    }
    request = _authenticated_request(
        factory, "post", "/stores/", payload, "dcs-writer-create-crossorg", "WRITER", org_slug="dcs"
    )
    response = create_view(request)
    assert response.status_code == 403
    assert not Store.objects.filter(workspace=gq_workspace, name="cross-org-store").exists()
