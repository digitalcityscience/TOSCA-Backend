from datetime import timedelta

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.forms import CheckboxSelectMultiple, ChoiceField, MultipleChoiceField
from django.test import RequestFactory
from django.utils import timezone

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.admin import EventAdmin, EventSeriesAdmin, EventTypeAdmin, TaxonomyDimensionAdmin, TaxonomyTermAdmin
from tosca_api.apps.events.forms import EventAdminForm, EventSeriesAdminForm
from tosca_api.apps.events.models import (
    Event,
    EventSeries,
    EventTerm,
    EventType,
    PublicHealthEventProfile,
    SportsEventProfile,
    TaxonomyDimension,
    TaxonomyTerm,
)

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
    assert isinstance(admin.site._registry[EventSeries], EventSeriesAdmin)
    assert isinstance(admin.site._registry[Event], EventAdmin)
    assert EventTerm in admin.site._registry
    assert PublicHealthEventProfile not in admin.site._registry
    assert SportsEventProfile not in admin.site._registry


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
def test_event_series_admin_uses_structured_recurrence_form(admin_request):
    """Series admin should use choices instead of raw JSON or free-text weekday input."""
    model_admin = admin.site._registry[EventSeries]
    form_class = model_admin.get_form(admin_request)
    form = form_class()

    assert model_admin.form is EventSeriesAdminForm
    assert "created_by" not in form_class.base_fields
    assert isinstance(form.fields["by_weekday"], MultipleChoiceField)
    assert isinstance(form.fields["by_weekday"].widget, CheckboxSelectMultiple)
    assert isinstance(form.fields["weekday_of_month"], ChoiceField)
    assert form.fields["timezone"].initial
    assert len(list(form.fields["event_type"].widget.choices)) > 1
    assert "Stop after this many occurrences" in form.fields["occurrence_count"].help_text
    assert "Repeat every N recurrence units" in form.fields["interval"].help_text
    unsaved_sections = {
        title for title, _options in model_admin.get_fieldsets(admin_request, obj=EventSeries())
    }
    assert "Metadata" not in unsaved_sections


@pytest.mark.django_db
def test_event_series_admin_assigns_creator_from_request(admin_request, admin_user):
    """Series creator should be filled automatically instead of asking editors to choose it."""
    campaign = Campaign.objects.create(title="Series Campaign", created_by=admin_user)
    event_type = EventType.objects.create(code="series-core", label="Series Core")
    model_admin = admin.site._registry[EventSeries]
    event_series = EventSeries(
        campaign=campaign,
        event_type=event_type,
        name="Spring Workshops",
        series_mode=EventSeries.SeriesMode.MANUAL_BATCH,
        start_date=timezone.localdate() + timedelta(days=10),
        start_time=timezone.now().time().replace(microsecond=0),
        end_time=(timezone.now() + timedelta(hours=1)).time().replace(microsecond=0),
        timezone="UTC",
    )

    model_admin.save_model(admin_request, event_series, form=None, change=False)

    assert event_series.created_by == admin_user


@pytest.mark.django_db
def test_event_series_admin_form_validation_uses_request_user_for_creator(admin_request, admin_user):
    """Add-form validation should not crash on missing created_by before save_model runs."""
    campaign = Campaign.objects.create(title="Series Validation Campaign", created_by=admin_user)
    event_type = EventType.objects.create(code="series-weekly", label="Series Weekly")
    model_admin = admin.site._registry[EventSeries]
    form_class = model_admin.get_form(admin_request)
    form = form_class(
        data={
            "campaign": str(campaign.id),
            "event_type": str(event_type.id),
            "name": "Weekly Sessions",
            "default_context": "",
            "series_mode": EventSeries.SeriesMode.RECURRING,
            "recurrence_type": EventSeries.RecurrenceType.WEEKLY,
            "start_date": str(timezone.localdate() + timedelta(days=5)),
            "end_date": str(timezone.localdate() + timedelta(days=20)),
            "occurrence_count": "",
            "interval": "1",
            "start_time": "09:00:00",
            "end_time": "10:00:00",
            "timezone": "UTC",
            "by_weekday": [],
            "monthly_rule_type": "",
            "day_of_month": "",
            "week_of_month": "",
            "weekday_of_month": "",
            "notes": "",
        }
    )

    assert not form.is_valid()
    assert "created_by" not in form.errors
    assert "by_weekday" in form.errors


@pytest.mark.django_db
def test_event_admin_form_embeds_profile_fields(admin_request):
    """Event admin should expose extension profile inputs on the event form itself."""
    model_admin = admin.site._registry[Event]
    form_class = model_admin.get_form(admin_request)

    assert model_admin.form is EventAdminForm
    assert {"public_health_insurance_eligible", "sports_sport_name", "culture_format_label"} <= set(
        form_class.base_fields
    )
    add_sections = {
        title for title, _options in model_admin.get_fieldsets(admin_request, obj=None)
    }
    assert "Series Metadata" not in add_sections
    unsaved_sections = {
        title for title, _options in model_admin.get_fieldsets(admin_request, obj=Event())
    }
    assert "Series Metadata" not in unsaved_sections


@pytest.mark.django_db
def test_event_admin_save_model_persists_selected_extension_profile(admin_request, admin_user):
    """Saving an event through admin should create the matching profile row in place."""
    campaign = Campaign.objects.create(title="Profile Campaign", created_by=admin_user)
    event_type = EventType.objects.create(
        code="ph-admin",
        label="Public Health Admin",
        profile_mode=EventType.ProfileMode.EXTENSION,
        profile_key="public_health",
    )
    model_admin = admin.site._registry[Event]
    form = EventAdminForm(
        data={
            "campaign": str(campaign.id),
            "event_type": str(event_type.id),
            "series": "",
            "title": "Vaccination Drive",
            "description": "Community clinic event",
            "start_datetime": (timezone.now() + timedelta(days=1)).isoformat(),
            "end_datetime": (timezone.now() + timedelta(days=1, hours=2)).isoformat(),
            "location_mode": Event.LocationMode.ONLINE,
            "location": "",
            "online_url": "https://example.com/join",
            "online_platform": "Zoom",
            "access_notes": "",
            "provider_name": "",
            "provider_url": "",
            "provider_contact": "",
            "context": "",
            "status": Event.Status.DRAFT,
            "visibility": Event.Visibility.PUBLIC,
            "organizer": str(admin_user.id),
            "public_health_insurance_eligible": "on",
            "public_health_referral_required": "",
            "sports_sport_name": "",
            "sports_skill_level": "",
            "culture_format_label": "",
            "culture_age_rating": "",
        }
    )

    assert form.is_valid(), form.errors
    event = form.save(commit=False)

    model_admin.save_model(admin_request, event, form, change=False)

    event.refresh_from_db()
    assert event.public_health_profile.insurance_eligible is True
    assert event.public_health_profile.referral_required is False


@pytest.mark.django_db
def test_event_admin_save_model_replaces_old_profile_when_event_type_changes(admin_request, admin_user):
    """Switching event type should remove stale extension rows from the event."""
    campaign = Campaign.objects.create(title="Profile Switch Campaign", created_by=admin_user)
    public_health_type = EventType.objects.create(
        code="ph-switch",
        label="Public Health Switch",
        profile_mode=EventType.ProfileMode.EXTENSION,
        profile_key="public_health",
    )
    sports_type = EventType.objects.create(
        code="sports-switch",
        label="Sports Switch",
        profile_mode=EventType.ProfileMode.EXTENSION,
        profile_key="sports",
    )
    event = Event.objects.create(
        campaign=campaign,
        event_type=public_health_type,
        title="Community Run",
        description="Initial type",
        start_datetime=timezone.now() + timedelta(days=2),
        end_datetime=timezone.now() + timedelta(days=2, hours=1),
        online_url="https://example.com/run",
        online_platform="Meet",
        location_mode=Event.LocationMode.ONLINE,
        organizer=admin_user,
    )
    PublicHealthEventProfile.objects.create(event=event, insurance_eligible=True)

    form = EventAdminForm(
        data={
            "campaign": str(campaign.id),
            "event_type": str(sports_type.id),
            "series": "",
            "title": event.title,
            "description": event.description,
            "start_datetime": event.start_datetime.isoformat(),
            "end_datetime": event.end_datetime.isoformat(),
            "location_mode": Event.LocationMode.ONLINE,
            "location": "",
            "online_url": event.online_url,
            "online_platform": event.online_platform,
            "access_notes": "",
            "provider_name": "",
            "provider_url": "",
            "provider_contact": "",
            "context": "",
            "status": event.status,
            "visibility": event.visibility,
            "organizer": str(admin_user.id),
            "public_health_insurance_eligible": "",
            "public_health_referral_required": "",
            "sports_sport_name": "Running",
            "sports_skill_level": "Beginner",
            "culture_format_label": "",
            "culture_age_rating": "",
        },
        instance=event,
    )

    assert form.is_valid(), form.errors
    updated_event = form.save(commit=False)

    admin.site._registry[Event].save_model(admin_request, updated_event, form, change=True)

    updated_event.refresh_from_db()
    with pytest.raises(PublicHealthEventProfile.DoesNotExist):
        _ = updated_event.public_health_profile
    assert updated_event.sports_profile.sport_name == "Running"
    assert updated_event.sports_profile.skill_level == "Beginner"


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
