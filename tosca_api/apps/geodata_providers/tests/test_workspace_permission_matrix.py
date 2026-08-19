"""Workspace DRF permission-matrix regression tests.

Closes the regression introduced when security tickets ticket 08 stripped
the action->level capability ladder out of the *shared*
``OrgScopedPermission`` class (moving it to ``has_perm()`` via
``ViewGatedModelPermissions``) for Campaign only. ``WorkspaceViewSet`` reuses
``OrgScopedPermission`` too, so it silently lost write/delete protection
until it got the same ``ViewGatedModelPermissions`` treatment here -- a
plain READER could otherwise PATCH/DELETE a Workspace. The broader ticket 12
geodata authorization cleanup (Store/Layer, publish/unpublish actions, etc.)
is intentionally out of scope for this fix.

``tosca_api/apps/geodata_providers/api/urls.py`` is not currently mounted in
``tosca_api/urls.py`` (see ``test_api_views.py``'s note), so ``WorkspaceViewSet``
is exercised by invoking it directly via ``APIRequestFactory``, not
``reverse()``/``self.client``.
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.geodata_providers.api.views import WorkspaceViewSet
from tosca_api.apps.geodata_providers.models import Workspace
from tosca_api.apps.organizations.models import Organization, OrganizationAppEntitlement

User = get_user_model()


@pytest.fixture
def factory():
    return APIRequestFactory()


@pytest.fixture
def dcs_org(db):
    # Pre-seeded by organizations migration 0002 + entitled to every
    # TOSCA_ENTITLEABLE_APPS app (incl. "geodata_providers") by migration 0005.
    org, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    return org


@pytest.fixture
def gq_org(db):
    org, _ = Organization.objects.get_or_create(slug="gq", defaults={"name": "GQ"})
    OrganizationAppEntitlement.objects.get_or_create(organization=org, app_label="geodata_providers")
    return org


@pytest.fixture
def unentitled_org(db):
    """An org whose members hold a real role but "geodata_providers" isn't
    entitled (gate B failure) -- deliberately **no**
    ``OrganizationAppEntitlement`` row."""
    org, _ = Organization.objects.get_or_create(slug="noent-gd", defaults={"name": "No Entitlement GD"})
    return org


def _workspace(org, name):
    # No geodata_engine, no dependent stores/layers -- WorkspaceService.
    # delete_workspace_safe() then does a pure DB delete with no GeoServer
    # round-trip, which is all a permission-layer regression test needs.
    creator = User.objects.create_user(username=f"{name}-owner")
    return Workspace.objects.create(organization=org, name=name, created_by=creator)


@pytest.fixture
def dcs_workspace(dcs_org):
    return _workspace(dcs_org, "dcs-workspace")


@pytest.fixture
def gq_workspace(gq_org):
    return _workspace(gq_org, "gq-workspace")


def _token(*roles, org_slug="dcs"):
    return {"realm_access": {"roles": list(roles)}, "default_organization": org_slug}


def _authenticated_request(factory, method, path, data, username, level, org_slug="dcs"):
    """Build a request authenticated for both gate C (``request.auth`` token)
    and gate A (``user._auth_claims``) -- see
    ``campaigns/tests/test_permission_matrix.py`` for why both are needed.
    """
    user = User.objects.create_user(username=username)
    roles = [f"ROLE_{org_slug.upper()}_{level}"] if level else []
    if level:
        user._auth_claims = AuthClaims(
            org_roles={org_slug: level}, default_org=org_slug, authoritative=True
        )
    request = getattr(factory, method)(path, data, format="json") if data is not None else getattr(factory, method)(path)
    force_authenticate(request, user=user, token=_token(*roles, org_slug=org_slug))
    return request


retrieve_view = WorkspaceViewSet.as_view({"get": "retrieve"})
list_view = WorkspaceViewSet.as_view({"get": "list"})
update_view = WorkspaceViewSet.as_view({"patch": "partial_update"})
destroy_view = WorkspaceViewSet.as_view({"delete": "destroy"})


# ---------------------------------------------------------------------------
# Role-matrix: READER / WRITER / ADMIN, own org
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_reader_can_read_own_org_workspace(factory, dcs_workspace):
    request = _authenticated_request(
        factory, "get", "/workspaces/", None, "reader-own", "READER"
    )
    response = retrieve_view(request, pk=str(dcs_workspace.pk))
    assert response.status_code == 200


@pytest.mark.django_db
def test_reader_cannot_write_own_org_workspace(factory, dcs_workspace):
    request = _authenticated_request(
        factory, "patch", "/workspaces/", {"description": "hacked"}, "reader-write", "READER"
    )
    response = update_view(request, pk=str(dcs_workspace.pk))
    assert response.status_code == 403


@pytest.mark.django_db
def test_writer_can_change_own_org_workspace(factory, dcs_workspace):
    request = _authenticated_request(
        factory, "patch", "/workspaces/", {"description": "updated"}, "writer-change", "WRITER"
    )
    response = update_view(request, pk=str(dcs_workspace.pk))
    assert response.status_code == 200
    dcs_workspace.refresh_from_db()
    assert dcs_workspace.description == "updated"


@pytest.mark.django_db
def test_writer_cannot_delete_own_org_workspace(factory, dcs_workspace):
    request = _authenticated_request(
        factory, "delete", "/workspaces/", None, "writer-delete", "WRITER"
    )
    response = destroy_view(request, pk=str(dcs_workspace.pk))
    assert response.status_code == 403
    assert Workspace.objects.filter(pk=dcs_workspace.pk).exists()


@pytest.mark.django_db
def test_admin_can_delete_own_org_workspace(factory, dcs_workspace):
    request = _authenticated_request(
        factory, "delete", "/workspaces/", None, "admin-delete", "ADMIN"
    )
    response = destroy_view(request, pk=str(dcs_workspace.pk))
    assert response.status_code == 200
    assert not Workspace.objects.filter(pk=dcs_workspace.pk).exists()


@pytest.mark.django_db
def test_no_role_denied(factory, dcs_workspace):
    request = _authenticated_request(factory, "get", "/workspaces/", None, "no-role", None)
    response = retrieve_view(request, pk=str(dcs_workspace.pk))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Cross-org isolation (gate C)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_cross_org_retrieve_is_404(factory, gq_workspace):
    request = _authenticated_request(
        factory, "get", "/workspaces/", None, "dcs-admin-crossorg", "ADMIN", org_slug="dcs"
    )
    response = retrieve_view(request, pk=str(gq_workspace.pk))
    assert response.status_code == 404


@pytest.mark.django_db
def test_cross_org_write_is_404(factory, gq_workspace):
    request = _authenticated_request(
        factory, "patch", "/workspaces/", {"description": "hacked"}, "dcs-writer-crossorg", "WRITER", org_slug="dcs"
    )
    response = update_view(request, pk=str(gq_workspace.pk))
    assert response.status_code == 404


@pytest.mark.django_db
def test_cross_org_delete_is_404(factory, gq_workspace):
    request = _authenticated_request(
        factory, "delete", "/workspaces/", None, "dcs-admin-crossorg-del", "ADMIN", org_slug="dcs"
    )
    response = destroy_view(request, pk=str(gq_workspace.pk))
    assert response.status_code == 404
    assert Workspace.objects.filter(pk=gq_workspace.pk).exists()


@pytest.mark.django_db
def test_list_excludes_other_org_workspaces(factory, dcs_workspace, gq_workspace):
    request = _authenticated_request(
        factory, "get", "/workspaces/", None, "dcs-reader-list", "READER", org_slug="dcs"
    )
    response = list_view(request)
    assert response.status_code == 200
    results = response.data["results"] if isinstance(response.data, dict) else response.data
    ids = {row["id"] for row in results}
    assert str(dcs_workspace.id) in ids
    assert str(gq_workspace.id) not in ids


# ---------------------------------------------------------------------------
# Entitlement (gate B) -- role present, app not entitled to the org
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_entitlement_missing_denies_read(factory, unentitled_org):
    workspace = _workspace(unentitled_org, "noent-workspace")
    request = _authenticated_request(
        factory, "get", "/workspaces/", None, "noent-admin", "ADMIN", org_slug="noent-gd"
    )
    response = retrieve_view(request, pk=str(workspace.pk))
    assert response.status_code == 403


@pytest.mark.django_db
def test_entitlement_missing_denies_write(factory, unentitled_org):
    workspace = _workspace(unentitled_org, "noent-workspace-2")
    request = _authenticated_request(
        factory, "patch", "/workspaces/", {"description": "hacked"}, "noent-writer", "WRITER", org_slug="noent-gd"
    )
    response = update_view(request, pk=str(workspace.pk))
    assert response.status_code == 403
