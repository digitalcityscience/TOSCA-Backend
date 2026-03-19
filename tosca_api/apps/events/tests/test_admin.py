import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from tosca_api.apps.events.admin import TaxonomyDimensionAdmin, TaxonomyTermAdmin
from tosca_api.apps.events.models import EventTerm, TaxonomyDimension, TaxonomyTerm

User = get_user_model()


@pytest.fixture
def admin_user():
    return User.objects.create_superuser(
        username="events-admin",
        email="events-admin@example.com",
        password="password",
    )


@pytest.fixture
def admin_request(admin_user):
    request = RequestFactory().get("/admin/events/")
    request.user = admin_user
    return request


@pytest.mark.django_db
def test_taxonomy_models_are_registered_in_admin():
    """Taxonomy models should be available in Django admin."""
    assert isinstance(admin.site._registry[TaxonomyDimension], TaxonomyDimensionAdmin)
    assert isinstance(admin.site._registry[TaxonomyTerm], TaxonomyTermAdmin)
    assert EventTerm in admin.site._registry


@pytest.mark.django_db
def test_taxonomy_dimension_admin_form_exposes_expected_fields(admin_request):
    """Dimension admin should expose the taxonomy configuration fields."""
    model_admin = admin.site._registry[TaxonomyDimension]
    form_class = model_admin.get_form(admin_request)

    assert {"code", "label", "description", "selection_mode", "is_active", "sort_order"} <= set(
        form_class.base_fields
    )
    assert model_admin.inlines


@pytest.mark.django_db
def test_taxonomy_term_admin_form_exposes_expected_fields(admin_request):
    """Term admin should expose term hierarchy and dimension fields."""
    model_admin = admin.site._registry[TaxonomyTerm]
    form_class = model_admin.get_form(admin_request)

    assert {"dimension", "parent", "code", "label", "description", "is_active", "sort_order"} <= set(
        form_class.base_fields
    )
