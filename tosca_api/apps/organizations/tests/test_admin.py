"""OrganizationAdmin must never allow hand-creating a row (canonical §5):
Organization mirrors a native Keycloak org, and the only legitimate writer
is `get_or_create_organization` firing on a login carrying a
`default_organization` claim Django hasn't seen yet. A manually-created row
has a `slug` with no matching Keycloak org, desyncing the `ROLE_<SLUG>_*`
role convention.
"""

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from tosca_api.apps.organizations.admin import OrganizationAdmin
from tosca_api.apps.organizations.models import Organization


@pytest.fixture
def organization_admin():
    return OrganizationAdmin(Organization, AdminSite())


@pytest.mark.django_db
def test_organization_admin_add_permission_always_denied(django_user_model, organization_admin):
    superuser = django_user_model.objects.create_superuser(
        username="root-org", email="root@example.com", password="x"
    )
    request = RequestFactory().get("/fake/")
    request.user = superuser

    assert organization_admin.has_add_permission(request) is False


@pytest.mark.django_db
def test_organization_admin_add_view_is_blocked(client, django_user_model):
    superuser = django_user_model.objects.create_superuser(
        username="root-org-http", email="root@example.com", password="x"
    )
    client.force_login(superuser)

    response = client.get("/admin/organizations/organization/add/")

    assert response.status_code == 403
