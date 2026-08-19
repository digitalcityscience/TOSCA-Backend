"""EventType/TaxonomyDimension/TaxonomyTerm admin change/delete lockdown
(security tickets 2026-08-19 ticket 05).

These are shared, un-owned reference-data models with no org FK -- before
this ticket a plain org WRITER could ``change`` and an org ADMIN could
``delete`` rows other orgs' content depends on. They're now locked to
superuser for change/delete, same as ``GeodataEngineAdmin``.
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from tosca_api.apps.authentication.role_sync import AuthClaims
from tosca_api.apps.events.admin import EventTypeAdmin, TaxonomyDimensionAdmin, TaxonomyTermAdmin
from tosca_api.apps.events.models import EventType, TaxonomyDimension, TaxonomyTerm
from tosca_api.apps.organizations.models import Organization

User = get_user_model()


@pytest.fixture
def factory():
    return RequestFactory()


@pytest.fixture
def dcs_org(db):
    org, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    return org


def _admin_request(factory, level):
    """A non-superuser DJANGO_STAFF user holding ``level`` in the dcs org."""
    request = factory.get("/admin/events/")
    user = User.objects.create_user(username=f"dcs-{level.lower()}-shared", is_staff=True)
    user._auth_claims = AuthClaims(
        org_roles={"dcs": level}, default_org="dcs", authoritative=True,
    )
    request.user = user
    return request


@pytest.fixture
def event_type(db):
    return EventType.objects.create(code="lockdown", label="Lockdown Type")


@pytest.fixture
def taxonomy_dimension(db):
    return TaxonomyDimension.objects.create(code="lockdown-dim", label="Lockdown Dimension")


@pytest.fixture
def taxonomy_term(taxonomy_dimension):
    return TaxonomyTerm.objects.create(
        dimension=taxonomy_dimension, code="lockdown-term", label="Lockdown Term",
    )


@pytest.mark.django_db
def test_org_admin_cannot_change_event_type(factory, dcs_org, event_type):
    request = _admin_request(factory, "ADMIN")
    model_admin = EventTypeAdmin(EventType, admin_site=None)
    assert model_admin.has_change_permission(request, event_type) is False


@pytest.mark.django_db
def test_org_admin_cannot_delete_event_type(factory, dcs_org, event_type):
    request = _admin_request(factory, "ADMIN")
    model_admin = EventTypeAdmin(EventType, admin_site=None)
    assert model_admin.has_delete_permission(request, event_type) is False


@pytest.mark.django_db
def test_org_writer_cannot_change_taxonomy_dimension(factory, dcs_org, taxonomy_dimension):
    request = _admin_request(factory, "WRITER")
    model_admin = TaxonomyDimensionAdmin(TaxonomyDimension, admin_site=None)
    assert model_admin.has_change_permission(request, taxonomy_dimension) is False


@pytest.mark.django_db
def test_org_admin_cannot_delete_taxonomy_dimension(factory, dcs_org, taxonomy_dimension):
    request = _admin_request(factory, "ADMIN")
    model_admin = TaxonomyDimensionAdmin(TaxonomyDimension, admin_site=None)
    assert model_admin.has_delete_permission(request, taxonomy_dimension) is False


@pytest.mark.django_db
def test_org_writer_cannot_change_taxonomy_term(factory, dcs_org, taxonomy_term):
    request = _admin_request(factory, "WRITER")
    model_admin = TaxonomyTermAdmin(TaxonomyTerm, admin_site=None)
    assert model_admin.has_change_permission(request, taxonomy_term) is False


@pytest.mark.django_db
def test_org_admin_cannot_delete_taxonomy_term(factory, dcs_org, taxonomy_term):
    request = _admin_request(factory, "ADMIN")
    model_admin = TaxonomyTermAdmin(TaxonomyTerm, admin_site=None)
    assert model_admin.has_delete_permission(request, taxonomy_term) is False


@pytest.mark.django_db
def test_superuser_can_change_and_delete_event_type(factory, event_type):
    request = factory.get("/admin/events/")
    request.user = User.objects.create_superuser(
        username="platform-admin-shared", email="p@example.com", password="pw",
    )
    model_admin = EventTypeAdmin(EventType, admin_site=None)
    assert model_admin.has_change_permission(request, event_type) is True
    assert model_admin.has_delete_permission(request, event_type) is True
