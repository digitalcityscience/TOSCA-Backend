"""Layer DRF permission-matrix tests (security tickets ticket 11, A9).

Unlike Store, ``LayerViewSet.list``/``retrieve`` deliberately stay
``AllowAny`` and unscoped -- that mirrors the public catalog's anonymous-read
behavior on this management endpoint and was an explicit decision to *not*
change (see ``get_permissions``'s comment). Only the write actions
(create/update/destroy/``publish_postgis``) previously had no org-scoping at
all (``IsAuthenticated`` only) -- any authenticated user, in any org, could
create/edit/delete any other org's Layer. This test suite covers the write
side only; read behavior is unchanged and not re-tested here.

``tosca_api/apps/geodata_providers/api/urls.py`` is not currently mounted in
``tosca_api/urls.py`` (see ``test_api_views.py``'s note), so ``LayerViewSet``
is exercised by invoking it directly via ``APIRequestFactory``, not
``reverse()``/``self.client`` -- mirrors ``test_workspace_permission_matrix.py``.
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.geodata_providers.api.views import LayerViewSet
from tosca_api.apps.geodata_providers.models import Layer, Store, Workspace
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


def _layer(workspace, store, name):
    creator = User.objects.create_user(username=f"{name}-owner")
    return Layer.objects.create(
        workspace=workspace,
        store=store,
        name=name,
        table_name=name,
        geometry_type=Layer.GeometryType.POINT,
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


@pytest.fixture
def dcs_layer(dcs_workspace, dcs_store):
    return _layer(dcs_workspace, dcs_store, "dcs-layer")


@pytest.fixture
def gq_layer(gq_workspace, gq_store):
    return _layer(gq_workspace, gq_store, "gq-layer")


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


update_view = LayerViewSet.as_view({"patch": "partial_update"})
destroy_view = LayerViewSet.as_view({"delete": "destroy"})
create_view = LayerViewSet.as_view({"post": "create"})
publish_view = LayerViewSet.as_view({"post": "publish"})
unpublish_view = LayerViewSet.as_view({"post": "unpublish"})
preview_view = LayerViewSet.as_view({"post": "preview"})


# ---------------------------------------------------------------------------
# Role-matrix: READER / WRITER / ADMIN, own org
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_reader_cannot_write_own_org_layer(factory, dcs_layer):
    request = _authenticated_request(
        factory, "patch", "/layers/", {"title": "hacked"}, "reader-write", "READER"
    )
    response = update_view(request, pk=str(dcs_layer.pk))
    assert response.status_code == 403


@pytest.mark.django_db
def test_writer_can_change_own_org_layer(factory, dcs_layer):
    request = _authenticated_request(
        factory, "patch", "/layers/", {"title": "updated"}, "writer-change", "WRITER"
    )
    response = update_view(request, pk=str(dcs_layer.pk))
    assert response.status_code == 200
    dcs_layer.refresh_from_db()
    assert dcs_layer.title == "updated"


@pytest.mark.django_db
def test_writer_cannot_delete_own_org_layer(factory, dcs_layer):
    request = _authenticated_request(factory, "delete", "/layers/", None, "writer-delete", "WRITER")
    response = destroy_view(request, pk=str(dcs_layer.pk))
    assert response.status_code == 403
    assert Layer.objects.filter(pk=dcs_layer.pk).exists()


@pytest.mark.django_db
def test_admin_can_delete_own_org_layer(factory, dcs_layer):
    request = _authenticated_request(factory, "delete", "/layers/", None, "admin-delete", "ADMIN")
    response = destroy_view(request, pk=str(dcs_layer.pk))
    assert response.status_code == 200
    assert not Layer.objects.filter(pk=dcs_layer.pk).exists()


# ---------------------------------------------------------------------------
# publish/unpublish: security tickets ticket 12 -- previously gated on Django
# `is_staff` (IsAdminUser) only, with no org scope at all. Now the same
# gate A (capability, via ViewGatedModelPermissions) + gate C (org ownership,
# via WorkspaceOwnedScopedPermission) matrix as create/update/destroy.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_reader_cannot_publish_own_org_layer(factory, dcs_layer):
    request = _authenticated_request(factory, "post", "/layers/", None, "reader-publish", "READER")
    response = publish_view(request, pk=str(dcs_layer.pk))
    assert response.status_code == 403


@pytest.mark.django_db
def test_reader_cannot_unpublish_own_org_layer(factory, dcs_layer):
    request = _authenticated_request(factory, "post", "/layers/", None, "reader-unpublish", "READER")
    response = unpublish_view(request, pk=str(dcs_layer.pk))
    assert response.status_code == 403


@pytest.mark.django_db
def test_writer_can_publish_own_org_layer_without_is_staff(factory, dcs_layer):
    # Idempotent path (already PUBLISHED) avoids needing a real GeoServer
    # engine attached to the workspace -- this test is about the permission
    # gate, not the publish orchestration itself.
    dcs_layer.publishing_state = Layer.PublishingState.PUBLISHED
    dcs_layer.save(update_fields=["publishing_state"])
    request = _authenticated_request(factory, "post", "/layers/", None, "writer-publish", "WRITER")
    response = publish_view(request, pk=str(dcs_layer.pk))
    assert response.status_code == 200
    assert response.data["success"] is True


@pytest.mark.django_db
def test_writer_can_unpublish_own_org_layer_without_is_staff(factory, dcs_layer):
    # dcs_layer defaults to DRAFT, so unpublish takes the idempotent path.
    request = _authenticated_request(factory, "post", "/layers/", None, "writer-unpublish", "WRITER")
    response = unpublish_view(request, pk=str(dcs_layer.pk))
    assert response.status_code == 200
    assert response.data["success"] is True


@pytest.mark.django_db
def test_preview_only_requires_authentication(factory):
    # Stateless utility, no Layer/Workspace object -- org scope doesn't
    # apply; any authenticated user may call it (previously required
    # Django is_staff).
    request = _authenticated_request(
        factory, "post", "/layers/", {"file_name": "buildings.geojson"}, "preview-user", None
    )
    response = preview_view(request)
    assert response.status_code == 200
    assert response.data["success"] is True


# ---------------------------------------------------------------------------
# Cross-org isolation on writes (gate C, reached through Layer.workspace.organization)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_cross_org_write_denied(factory, gq_layer):
    request = _authenticated_request(
        factory, "patch", "/layers/", {"title": "hacked"}, "dcs-writer-crossorg", "WRITER", org_slug="dcs"
    )
    response = update_view(request, pk=str(gq_layer.pk))
    assert response.status_code == 403
    gq_layer.refresh_from_db()
    assert gq_layer.title != "hacked"


@pytest.mark.django_db
def test_cross_org_delete_denied(factory, gq_layer):
    request = _authenticated_request(
        factory, "delete", "/layers/", None, "dcs-admin-crossorg-del", "ADMIN", org_slug="dcs"
    )
    response = destroy_view(request, pk=str(gq_layer.pk))
    assert response.status_code == 403
    assert Layer.objects.filter(pk=gq_layer.pk).exists()


@pytest.mark.django_db
def test_cross_org_create_denied(factory, gq_workspace, gq_store):
    payload = {
        "workspace": str(gq_workspace.pk),
        "store": str(gq_store.pk),
        "name": "cross-org-layer",
        "table_name": "cross_org_layer",
        "geometry_type": "Point",
    }
    request = _authenticated_request(
        factory, "post", "/layers/", payload, "dcs-writer-create-crossorg", "WRITER", org_slug="dcs"
    )
    response = create_view(request)
    assert response.status_code == 403
    assert not Layer.objects.filter(workspace=gq_workspace, name="cross-org-layer").exists()


# ---------------------------------------------------------------------------
# Read behavior is unchanged: still AllowAny / unscoped (regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_cross_org_publish_denied(factory, gq_layer):
    request = _authenticated_request(
        factory, "post", "/layers/", None, "dcs-writer-crossorg-publish", "WRITER", org_slug="dcs"
    )
    response = publish_view(request, pk=str(gq_layer.pk))
    assert response.status_code == 403


@pytest.mark.django_db
def test_cross_org_unpublish_denied(factory, gq_layer):
    request = _authenticated_request(
        factory, "post", "/layers/", None, "dcs-writer-crossorg-unpublish", "WRITER", org_slug="dcs"
    )
    response = unpublish_view(request, pk=str(gq_layer.pk))
    assert response.status_code == 403


@pytest.mark.django_db
def test_anonymous_can_still_list_and_retrieve_public_layer(factory, dcs_workspace, dcs_store):
    public_layer = _layer(dcs_workspace, dcs_store, "dcs-public-layer")
    public_layer.is_public = True
    public_layer.save()

    list_view = LayerViewSet.as_view({"get": "list"})
    retrieve_view = LayerViewSet.as_view({"get": "retrieve"})

    list_response = list_view(factory.get("/layers/"))
    assert list_response.status_code == 200

    retrieve_response = retrieve_view(factory.get("/layers/"), pk=str(public_layer.pk))
    assert retrieve_response.status_code == 200
