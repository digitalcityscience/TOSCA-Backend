from datetime import timedelta

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.test import RequestFactory
from django.utils import timezone

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.admin import EventTypeAdmin, TaxonomyDimensionAdmin, TaxonomyTermAdmin
from tosca_api.apps.events.models import Event, EventTerm, EventType, TaxonomyDimension, TaxonomyTerm

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
    assert isinstance(admin.site._registry[EventType], EventTypeAdmin)
    assert isinstance(admin.site._registry[TaxonomyDimension], TaxonomyDimensionAdmin)
    assert isinstance(admin.site._registry[TaxonomyTerm], TaxonomyTermAdmin)
    assert EventTerm in admin.site._registry


@pytest.mark.django_db
def test_event_type_admin_form_exposes_registry_fields(admin_request):
    """Event type admin should expose the full registry contract."""
    model_admin = admin.site._registry[EventType]
    form_class = model_admin.get_form(admin_request)

    assert {"code", "label", "profile_mode", "profile_key", "is_active"} <= set(
        form_class.base_fields
    )


@pytest.mark.django_db
def test_event_type_admin_can_store_inactive_custom_type(admin_request):
    """Custom inactive event types should be valid through the admin form."""
    model_admin = admin.site._registry[EventType]
    form_class = model_admin.get_form(admin_request)
    form = form_class(
        data={
            "code": "custom-admin-type",
            "label": "Custom Admin Type",
            "profile_mode": EventType.ProfileMode.CORE,
            "profile_key": "",
            "is_active": "",
        }
    )

    assert form.is_valid(), form.errors
    event_type = form.save()
    assert event_type.is_active is False


@pytest.mark.django_db
def test_taxonomy_dimension_admin_form_exposes_expected_fields(admin_request):
    """Dimension admin should expose the taxonomy configuration fields."""
    model_admin = admin.site._registry[TaxonomyDimension]
    form_class = model_admin.get_form(admin_request)

    assert {"code", "label", "description", "selection_mode", "is_active", "sort_order"} <= set(
        form_class.base_fields
    )
    assert "auto-append" in form_class.base_fields["sort_order"].help_text
    assert model_admin.inlines


@pytest.mark.django_db
def test_taxonomy_term_admin_form_exposes_expected_fields(admin_request):
    """Term admin should expose term hierarchy and dimension fields."""
    model_admin = admin.site._registry[TaxonomyTerm]
    form_class = model_admin.get_form(admin_request)

    assert {"dimension", "parent", "code", "label", "description", "is_active", "sort_order"} <= set(
        form_class.base_fields
    )
    assert "auto-append" in form_class.base_fields["sort_order"].help_text


@pytest.mark.django_db
def test_event_term_admin_form_rejects_second_term_in_single_select_dimension(
    admin_request, admin_user
):
    """Admin form should surface the single-select assignment rule."""
    campaign = Campaign.objects.create(title="Admin Campaign", created_by=admin_user)
    event = Event.objects.create(
        campaign=campaign,
        title="Admin Event",
        start_datetime=timezone.now() + timedelta(days=1),
        end_datetime=timezone.now() + timedelta(days=1, hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=admin_user,
    )
    dimension = TaxonomyDimension.objects.create(
        code="audience",
        label="Audience",
        selection_mode=TaxonomyDimension.SelectionMode.SINGLE,
    )
    first_term = TaxonomyTerm.objects.create(
        dimension=dimension,
        code="youth",
        label="Youth",
    )
    second_term = TaxonomyTerm.objects.create(
        dimension=dimension,
        code="seniors",
        label="Seniors",
    )
    EventTerm.objects.create(event=event, term=first_term)

    model_admin = admin.site._registry[EventTerm]
    form_class = model_admin.get_form(admin_request)
    form = form_class(data={"event": str(event.id), "term": str(second_term.id)})

    assert not form.is_valid()
    assert "term" in form.errors
