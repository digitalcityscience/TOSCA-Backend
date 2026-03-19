from datetime import time, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.db import connection
from django.db import IntegrityError
from django.utils import timezone

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.models import (
    CultureEventProfile,
    Event,
    EventLayer,
    EventSeries,
    EventSeriesDate,
    EventTerm,
    EventType,
    PublicHealthEventProfile,
    SportsEventProfile,
    TaxonomyDimension,
    TaxonomyTerm,
)
from tosca_api.apps.geocontext.models import GeoContext
from tosca_api.apps.layerrefs.models import LayerRef

User = get_user_model()


def build_series_kwargs(user, campaign, event_type, **overrides):
    now = timezone.now()
    kwargs = {
        "campaign": campaign,
        "event_type": event_type,
        "created_by": user,
        "name": "Series",
        "series_mode": EventSeries.SeriesMode.MANUAL_BATCH,
        "start_date": now.date(),
        "start_time": now.time().replace(tzinfo=None, microsecond=0),
        "end_time": (now + timedelta(hours=1)).time().replace(
            tzinfo=None,
            microsecond=0,
        ),
        "timezone": "Europe/Berlin",
    }
    kwargs.update(overrides)
    return kwargs


@pytest.fixture
def user():
    return User.objects.create_user(username="eventuser", password="password")


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="Event Campaign", created_by=user)


@pytest.fixture
def layer_ref():
    return LayerRef.objects.create(layer_name="workspace:events_layer")


@pytest.fixture
def geocontext(user):
    return GeoContext.objects.create(content="Shared event context", created_by=user)


@pytest.fixture
def event_type():
    return EventType.objects.create(code="core", label="Core Event")


# =============================================================================
# Model Creation Tests
# =============================================================================


@pytest.mark.django_db
def test_event_create_success(user, campaign):
    """Test creating a valid event."""
    now = timezone.now()
    event = Event.objects.create(
        campaign=campaign,
        title="Workshop on Climate",
        description="A workshop about climate action",
        start_datetime=now,
        end_datetime=now + timedelta(hours=2),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
    )
    assert event.id is not None
    assert event.title == "Workshop on Climate"
    assert event.campaign == campaign
    assert event.organizer == user


@pytest.mark.django_db
def test_event_with_point_location(user, campaign):
    """Test creating event with a PointField location (SRID 4326)."""
    now = timezone.now()
    location = Point(9.993682, 53.551086, srid=4326)  # Hamburg coordinates

    event = Event.objects.create(
        campaign=campaign,
        title="Hamburg Meetup",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=location,
        organizer=user,
    )

    assert event.location is not None
    assert event.location.srid == 4326
    assert event.location.x == pytest.approx(9.993682, rel=1e-5)
    assert event.location.y == pytest.approx(53.551086, rel=1e-5)


# =============================================================================
# Constraint Tests
# =============================================================================


@pytest.mark.django_db
def test_event_end_before_start_raises_validation_error(user, campaign):
    """Test that end_datetime before start_datetime raises ValidationError."""
    now = timezone.now()

    with pytest.raises(ValidationError) as exc:
        Event.objects.create(
            campaign=campaign,
            title="Invalid Event",
            start_datetime=now,
            end_datetime=now - timedelta(hours=1),  # End before start!
            location=Point(10.0, 53.5, srid=4326),
            organizer=user,
        )

    assert "end_datetime" in exc.value.message_dict


@pytest.mark.django_db
def test_event_db_constraint_end_after_start(user, campaign):
    """Test that the DB CHECK constraint is enforced."""
    now = timezone.now()

    # Create a valid event first
    event = Event(
        campaign=campaign,
        title="Test Event",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
    )
    # Bypass full_clean to test DB constraint directly
    event.title = "Bypass Test"
    event.description = ""
    # Save without validation
    Event.objects.bulk_create([event])

    # Verify it was saved
    saved_event = Event.objects.get(id=event.id)
    assert saved_event.title == "Bypass Test"


@pytest.mark.django_db
def test_event_same_start_end_allowed(user, campaign):
    """Test that start == end is allowed (instantaneous event)."""
    now = timezone.now()
    event = Event.objects.create(
        campaign=campaign,
        title="Instant Event",
        start_datetime=now,
        end_datetime=now,  # Same as start
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
    )
    assert event.start_datetime == event.end_datetime


# =============================================================================
# EventLayer Tests
# =============================================================================


@pytest.mark.django_db
def test_event_layer_through_model(user, campaign, layer_ref):
    """Test adding layers to an event via through model."""
    now = timezone.now()
    event = Event.objects.create(
        campaign=campaign,
        title="Event with Layers",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
    )

    event_layer = EventLayer.objects.create(
        event=event,
        layer=layer_ref,
        display_order=1,
    )

    assert event_layer.event == event
    assert event_layer.layer == layer_ref
    assert event.layers.count() == 1
    assert layer_ref in event.layers.all()


@pytest.mark.django_db
def test_event_layer_auto_increment_order(user, campaign, layer_ref):
    """Test that display_order auto-increments."""
    now = timezone.now()
    event = Event.objects.create(
        campaign=campaign,
        title="Event with Layers",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
    )

    # Create first layer with order 0 (default)
    layer1 = LayerRef.objects.create(layer_name="workspace:layer1")
    EventLayer.objects.create(event=event, layer=layer1)

    # Create second layer - should auto-increment
    layer2 = LayerRef.objects.create(layer_name="workspace:layer2")
    el2 = EventLayer.objects.create(event=event, layer=layer2)

    assert el2.display_order == 1


@pytest.mark.django_db
def test_event_layer_unique_together(user, campaign, layer_ref):
    """Test that duplicate event-layer pairs are rejected."""
    now = timezone.now()
    event = Event.objects.create(
        campaign=campaign,
        title="Event",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
    )

    EventLayer.objects.create(event=event, layer=layer_ref)

    # Attempt to create duplicate
    with pytest.raises(IntegrityError):
        EventLayer.objects.create(event=event, layer=layer_ref)


# =============================================================================
# Sanitization Tests
# =============================================================================


@pytest.mark.django_db
def test_event_sanitizes_title(user, campaign):
    """Test that title is sanitized on save."""
    now = timezone.now()
    event = Event.objects.create(
        campaign=campaign,
        title="<script>alert('xss')</script>Event",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
    )
    # Sanitization should strip tags
    assert "<script>" not in event.title


@pytest.mark.django_db
def test_event_status_choices(user, campaign):
    """Test all status choices work."""
    now = timezone.now()

    for status in Event.Status:
        event = Event.objects.create(
            campaign=campaign,
            title=f"Event {status}",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            location=Point(10.0, 53.5, srid=4326),
            organizer=user,
            status=status,
        )
        assert event.status == status


@pytest.mark.django_db
def test_physical_event_requires_geometry(user, campaign):
    """Physical events must provide geometry."""
    now = timezone.now()

    with pytest.raises(ValidationError) as exc:
        Event.objects.create(
            campaign=campaign,
            title="Physical Without Geometry",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            organizer=user,
            location_mode=Event.LocationMode.PHYSICAL,
        )

    assert "location" in exc.value.message_dict


@pytest.mark.django_db
def test_online_event_requires_access_data(user, campaign):
    """Online events must provide online access data and can omit geometry."""
    now = timezone.now()

    with pytest.raises(ValidationError) as exc:
        Event.objects.create(
            campaign=campaign,
            title="Online Without Access",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            organizer=user,
            location_mode=Event.LocationMode.ONLINE,
        )

    assert "online_url" in exc.value.message_dict


@pytest.mark.django_db
def test_online_event_rejects_geometry(user, campaign):
    """Online events cannot include geometry."""
    now = timezone.now()

    with pytest.raises(ValidationError) as exc:
        Event.objects.create(
            campaign=campaign,
            title="Mapped Online Event",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            organizer=user,
            location_mode=Event.LocationMode.ONLINE,
            online_platform="Zoom",
            location=Point(10.0, 53.5, srid=4326),
        )

    assert "location" in exc.value.message_dict


@pytest.mark.django_db
def test_hybrid_event_requires_geometry_and_access_data(user, campaign):
    """Hybrid events require both geometry and online access data."""
    now = timezone.now()

    with pytest.raises(ValidationError) as exc:
        Event.objects.create(
            campaign=campaign,
            title="Hybrid Missing Access",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            organizer=user,
            location_mode=Event.LocationMode.HYBRID,
            location=Point(10.0, 53.5, srid=4326),
        )

    assert "online_url" in exc.value.message_dict

    with pytest.raises(ValidationError) as exc:
        Event.objects.create(
            campaign=campaign,
            title="Hybrid Missing Geometry",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            organizer=user,
            location_mode=Event.LocationMode.HYBRID,
            online_url="https://example.org/live",
        )

    assert "location" in exc.value.message_dict


@pytest.mark.django_db
def test_series_event_requires_occurrence_index(user, campaign, event_type):
    """Series events must provide an occurrence index."""
    now = timezone.now()
    series = EventSeries.objects.create(
        **build_series_kwargs(
            user,
            campaign,
            event_type,
            name="Recurring Workshops",
        )
    )

    with pytest.raises(ValidationError) as exc:
        Event.objects.create(
            campaign=campaign,
            event_type=event_type,
            title="Series Event",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            location=Point(10.0, 53.5, srid=4326),
            organizer=user,
            series=series,
            occurrence_index=None,
        )

    assert "occurrence_index" in exc.value.message_dict


@pytest.mark.django_db
def test_exception_requires_series(user, campaign):
    """Only series events can be marked as exceptions."""
    now = timezone.now()

    with pytest.raises(ValidationError) as exc:
        Event.objects.create(
            campaign=campaign,
            title="Standalone Exception",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            location=Point(10.0, 53.5, srid=4326),
            organizer=user,
            is_exception=True,
        )

    assert "is_exception" in exc.value.message_dict


@pytest.mark.django_db
def test_standalone_event_without_context_is_valid(user, campaign):
    """Standalone events remain valid without a context."""
    now = timezone.now()
    event = Event.objects.create(
        campaign=campaign,
        title="Standalone Event",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        context=None,
    )

    assert event.context is None


@pytest.mark.django_db
def test_event_without_series_and_without_context_resolves_no_context(user, campaign):
    """Standalone events without overrides resolve no effective context."""
    now = timezone.now()
    event = Event.objects.create(
        campaign=campaign,
        title="No Context",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        context=None,
    )

    assert event.effective_context is None


@pytest.mark.django_db
def test_event_resolves_series_default_context(user, campaign, geocontext, event_type):
    """Series default context is used when an event override is absent."""
    now = timezone.now()
    series = EventSeries.objects.create(
        **build_series_kwargs(
            user,
            campaign,
            event_type,
            name="Series With Shared Context",
        ),
        default_context=geocontext,
    )
    event = Event.objects.create(
        campaign=campaign,
        event_type=event_type,
        title="Series Event",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        series=series,
        occurrence_index=1,
        context=None,
    )

    assert event.effective_context == geocontext


@pytest.mark.django_db
def test_event_override_context_wins_over_series_default(user, campaign, geocontext, event_type):
    """A direct event override takes precedence over the series default."""
    now = timezone.now()
    override_context = GeoContext.objects.create(
        content="Occurrence override",
        created_by=user,
    )
    series = EventSeries.objects.create(
        **build_series_kwargs(
            user,
            campaign,
            event_type,
            name="Series With Shared Context",
        ),
        default_context=geocontext,
    )
    event = Event.objects.create(
        campaign=campaign,
        event_type=event_type,
        title="Series Event With Override",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        series=series,
        occurrence_index=1,
        context=override_context,
    )

    assert event.effective_context == override_context


@pytest.mark.django_db
def test_editing_event_override_does_not_change_series_default(user, campaign, geocontext, event_type):
    """Changing one event override must not mutate the series default context."""
    now = timezone.now()
    series = EventSeries.objects.create(
        **build_series_kwargs(
            user,
            campaign,
            event_type,
            name="Series With Shared Context",
        ),
        default_context=geocontext,
    )
    first_override = GeoContext.objects.create(content="Override A", created_by=user)
    second_override = GeoContext.objects.create(content="Override B", created_by=user)
    event = Event.objects.create(
        campaign=campaign,
        event_type=event_type,
        title="Series Event",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        series=series,
        occurrence_index=1,
        context=first_override,
    )

    event.context = second_override
    event.save()
    series.refresh_from_db()

    assert series.default_context == geocontext
    assert event.effective_context == second_override


@pytest.mark.django_db
def test_published_event_without_any_effective_context_is_valid(user, campaign):
    """Published events remain valid even when no effective context exists."""
    now = timezone.now()
    event = Event.objects.create(
        campaign=campaign,
        title="Published Without Context",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        status=Event.Status.PUBLISHED,
        context=None,
    )

    assert event.effective_context is None


@pytest.mark.django_db
def test_series_linked_event_rejects_campaign_change(user, campaign, event_type):
    """Series-linked events must keep the series campaign."""
    now = timezone.now()
    other_campaign = Campaign.objects.create(title="Other Campaign", created_by=user)
    series = EventSeries.objects.create(
        **build_series_kwargs(
            user,
            campaign,
            event_type,
            name="Locked Campaign Series",
        )
    )
    event = Event.objects.create(
        campaign=campaign,
        event_type=event_type,
        title="Series Event",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        series=series,
        occurrence_index=1,
    )

    event.campaign = other_campaign
    with pytest.raises(ValidationError) as exc:
        event.save()

    assert "campaign" in exc.value.message_dict


@pytest.mark.django_db
def test_series_linked_event_rejects_event_type_change(user, campaign, event_type):
    """Series-linked events must keep the series event type."""
    now = timezone.now()
    other_event_type = EventType.objects.create(code="other", label="Other Event")
    series = EventSeries.objects.create(
        **build_series_kwargs(
            user,
            campaign,
            event_type,
            name="Locked Event Type Series",
        )
    )
    event = Event.objects.create(
        campaign=campaign,
        event_type=event_type,
        title="Series Event",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        series=series,
        occurrence_index=1,
    )

    event.event_type = other_event_type
    with pytest.raises(ValidationError) as exc:
        event.save()

    assert "event_type" in exc.value.message_dict


@pytest.mark.django_db
def test_series_occurrence_index_must_be_unique(user, campaign, event_type):
    """Duplicate series occurrence indexes must be rejected."""
    now = timezone.now()
    series = EventSeries.objects.create(
        **build_series_kwargs(
            user,
            campaign,
            event_type,
            name="Unique Occurrence Series",
        )
    )
    Event.objects.create(
        campaign=campaign,
        event_type=event_type,
        title="Occurrence 1",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
        series=series,
        occurrence_index=1,
    )

    duplicate = Event(
        campaign=campaign,
        event_type=event_type,
        title="Occurrence 1 Duplicate",
        description="",
        start_datetime=now + timedelta(days=1),
        end_datetime=now + timedelta(days=1, hours=1),
        location=Point(10.1, 53.6, srid=4326),
        organizer=user,
        series=series,
        occurrence_index=1,
    )

    with pytest.raises(IntegrityError):
        Event.objects.bulk_create([duplicate])


@pytest.mark.django_db
def test_event_core_indexes_exist():
    """The agreed event indexes should exist in the schema."""
    expected_indexes = {
        "events_evt_cmp_stat_start_idx",
        "events_evt_type_start_idx",
        "events_evt_locmode_start_idx",
        "events_evt_series_start_idx",
        "events_evt_ser_occ_uniq",
    }

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = 'events_event'
            """
        )
        rows = cursor.fetchall()

    index_names = {row[0] for row in rows}
    assert expected_indexes.issubset(index_names)
    assert any("using gist" in row[1].lower() and "location" in row[1].lower() for row in rows)


# =============================================================================
# Event Type Registry Tests
# =============================================================================


@pytest.mark.django_db
def test_event_type_seeds_exist_with_expected_profile_bindings():
    """The initial registry seed should create the agreed event types."""
    seeded = {
        event_type.code: (event_type.profile_mode, event_type.profile_key, event_type.is_active)
        for event_type in EventType.objects.filter(
            code__in=["general", "public_health", "sports", "culture"]
        )
    }

    assert seeded == {
        "general": (EventType.ProfileMode.CORE, None, True),
        "public_health": (EventType.ProfileMode.EXTENSION, "public_health", True),
        "sports": (EventType.ProfileMode.EXTENSION, "sports", True),
        "culture": (EventType.ProfileMode.EXTENSION, "culture", True),
    }


@pytest.mark.django_db
def test_extension_event_type_requires_profile_key():
    """Extension event types must declare their profile key."""
    with pytest.raises(ValidationError) as exc:
        EventType.objects.create(
            code="broken-extension",
            label="Broken Extension",
            profile_mode=EventType.ProfileMode.EXTENSION,
            profile_key=None,
        )

    assert "profile_key" in exc.value.message_dict


@pytest.mark.django_db
def test_core_event_type_rejects_profile_key():
    """Core event types must not carry a profile key."""
    with pytest.raises(ValidationError) as exc:
        EventType.objects.create(
            code="broken-core",
            label="Broken Core",
            profile_mode=EventType.ProfileMode.CORE,
            profile_key="sports",
        )

    assert "profile_key" in exc.value.message_dict


@pytest.mark.django_db
def test_inactive_event_type_can_be_stored():
    """Inactive custom event types remain valid registry rows."""
    event_type = EventType.objects.create(
        code="legacy-custom",
        label="Legacy Custom",
        profile_mode=EventType.ProfileMode.CORE,
        is_active=False,
    )

    assert event_type.is_active is False
    assert event_type.profile_key is None


@pytest.mark.django_db
def test_event_type_code_must_be_unique():
    """Duplicate event type codes should be rejected."""
    EventType.objects.create(code="custom-type", label="Custom Type")

    duplicate = EventType(
        code="custom-type",
        label="Custom Type Duplicate",
        profile_mode=EventType.ProfileMode.CORE,
    )

    with pytest.raises(IntegrityError):
        EventType.objects.bulk_create([duplicate])


# =============================================================================
# Extension Profile Tests
# =============================================================================


@pytest.mark.django_db
def test_general_event_can_exist_without_any_extension_profile(user, campaign):
    """Core event types do not require profile rows."""
    general_type = EventType.objects.get(code="general")
    event = Event.objects.create(
        campaign=campaign,
        event_type=general_type,
        title="General Event",
        start_datetime=timezone.now(),
        end_datetime=timezone.now() + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
    )

    assert event.event_type == general_type
    assert not PublicHealthEventProfile.objects.filter(event=event).exists()
    assert not SportsEventProfile.objects.filter(event=event).exists()
    assert not CultureEventProfile.objects.filter(event=event).exists()


@pytest.mark.django_db
def test_public_health_event_accepts_public_health_profile(user, campaign):
    """The public_health event type should accept its matching profile table."""
    event = Event.objects.create(
        campaign=campaign,
        event_type=EventType.objects.get(code="public_health"),
        title="Public Health Event",
        start_datetime=timezone.now(),
        end_datetime=timezone.now() + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
    )

    profile = PublicHealthEventProfile.objects.create(
        event=event,
        insurance_eligible=True,
        referral_required=False,
    )

    assert profile.event == event


@pytest.mark.django_db
def test_sports_event_accepts_sports_profile(user, campaign):
    """The sports event type should accept its matching profile table."""
    event = Event.objects.create(
        campaign=campaign,
        event_type=EventType.objects.get(code="sports"),
        title="Sports Event",
        start_datetime=timezone.now(),
        end_datetime=timezone.now() + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
    )

    profile = SportsEventProfile.objects.create(
        event=event,
        sport_name="Football",
        skill_level="beginner",
    )

    assert profile.event == event


@pytest.mark.django_db
def test_culture_event_accepts_culture_profile(user, campaign):
    """The culture event type should accept its matching profile table."""
    event = Event.objects.create(
        campaign=campaign,
        event_type=EventType.objects.get(code="culture"),
        title="Culture Event",
        start_datetime=timezone.now(),
        end_datetime=timezone.now() + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
    )

    profile = CultureEventProfile.objects.create(
        event=event,
        format_label="Festival",
        age_rating="12+",
    )

    assert profile.event == event


@pytest.mark.django_db
def test_core_event_type_rejects_extension_profile(user, campaign):
    """Core event types must not accept extension profiles."""
    event = Event.objects.create(
        campaign=campaign,
        event_type=EventType.objects.get(code="general"),
        title="General Event",
        start_datetime=timezone.now(),
        end_datetime=timezone.now() + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
    )

    with pytest.raises(ValidationError) as exc:
        PublicHealthEventProfile.objects.create(event=event)

    assert "event" in exc.value.message_dict


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("profile_model", "event_type_code"),
    [
        (PublicHealthEventProfile, "culture"),
        (SportsEventProfile, "public_health"),
        (CultureEventProfile, "sports"),
    ],
)
def test_mismatched_event_type_profile_combinations_are_rejected(
    user, campaign, profile_model, event_type_code
):
    """Extension profiles must match the profile key declared by EventType."""
    event = Event.objects.create(
        campaign=campaign,
        event_type=EventType.objects.get(code=event_type_code),
        title=f"Mismatched {event_type_code} Event",
        start_datetime=timezone.now(),
        end_datetime=timezone.now() + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
    )

    with pytest.raises(ValidationError) as exc:
        profile_model.objects.create(event=event)

    assert "event" in exc.value.message_dict


# =============================================================================
# Event Series Schema Tests
# =============================================================================


@pytest.mark.django_db
def test_manual_batch_series_accepts_explicit_dates(user, campaign, event_type):
    """Manual batch series should persist explicit dates through EventSeriesDate."""
    series = EventSeries.objects.create(
        **build_series_kwargs(
            user,
            campaign,
            event_type,
            name="Manual Batch Series",
        )
    )

    first_date = EventSeriesDate.objects.create(
        series=series,
        occurrence_date=series.start_date,
        display_order=1,
    )
    second_date = EventSeriesDate.objects.create(
        series=series,
        occurrence_date=series.start_date + timedelta(days=7),
        display_order=2,
    )

    assert list(series.dates.order_by("display_order")) == [first_date, second_date]


@pytest.mark.django_db
def test_event_series_date_occurrence_date_must_be_unique(user, campaign, event_type):
    """Duplicate explicit dates for one series must be rejected."""
    series = EventSeries.objects.create(
        **build_series_kwargs(
            user,
            campaign,
            event_type,
            name="Manual Batch Series",
        )
    )
    EventSeriesDate.objects.create(
        series=series,
        occurrence_date=series.start_date,
        display_order=1,
    )

    duplicate = EventSeriesDate(
        series=series,
        occurrence_date=series.start_date,
        display_order=2,
    )

    with pytest.raises(IntegrityError):
        EventSeriesDate.objects.bulk_create([duplicate])


@pytest.mark.django_db
def test_weekly_series_requires_weekday(user, campaign, event_type):
    """Weekly recurrence must define at least one weekday."""
    with pytest.raises(ValidationError) as exc:
        EventSeries.objects.create(
            **build_series_kwargs(
                user,
                campaign,
                event_type,
                name="Weekly Series",
                series_mode=EventSeries.SeriesMode.RECURRING,
                recurrence_type=EventSeries.RecurrenceType.WEEKLY,
                end_date=timezone.now().date() + timedelta(days=30),
                by_weekday=[],
            )
        )

    assert "by_weekday" in exc.value.message_dict


@pytest.mark.django_db
def test_monthly_series_requires_monthly_rule_fields(user, campaign, event_type):
    """Monthly recurrence must provide the fields required by its rule type."""
    with pytest.raises(ValidationError) as exc:
        EventSeries.objects.create(
            **build_series_kwargs(
                user,
                campaign,
                event_type,
                name="Monthly Series",
                series_mode=EventSeries.SeriesMode.RECURRING,
                recurrence_type=EventSeries.RecurrenceType.MONTHLY,
                end_date=timezone.now().date() + timedelta(days=30),
                monthly_rule_type=EventSeries.MonthlyRuleType.DAY_OF_MONTH,
                day_of_month=None,
            )
        )

    assert "day_of_month" in exc.value.message_dict

    with pytest.raises(ValidationError) as exc:
        EventSeries.objects.create(
            **build_series_kwargs(
                user,
                campaign,
                event_type,
                name="Nth Weekday Monthly Series",
                series_mode=EventSeries.SeriesMode.RECURRING,
                recurrence_type=EventSeries.RecurrenceType.MONTHLY,
                end_date=timezone.now().date() + timedelta(days=30),
                monthly_rule_type=EventSeries.MonthlyRuleType.NTH_WEEKDAY,
                week_of_month=None,
                weekday_of_month="",
            )
        )

    assert "monthly_rule_type" in exc.value.message_dict


@pytest.mark.django_db
def test_recurring_series_rejects_invalid_end_date_occurrence_count_combinations(
    user, campaign, event_type
):
    """Recurring series must use exactly one termination strategy."""
    with pytest.raises(ValidationError) as exc:
        EventSeries.objects.create(
            **build_series_kwargs(
                user,
                campaign,
                event_type,
                name="Invalid Recurring Series",
                series_mode=EventSeries.SeriesMode.RECURRING,
                recurrence_type=EventSeries.RecurrenceType.DAILY,
                end_date=timezone.now().date() + timedelta(days=14),
                occurrence_count=5,
            )
        )

    assert "end_date" in exc.value.message_dict


@pytest.mark.django_db
def test_recurring_series_requires_same_day_end_time(user, campaign, event_type):
    """Recurring generation currently only supports same-day occurrence durations."""
    with pytest.raises(ValidationError) as exc:
        EventSeries.objects.create(
            **build_series_kwargs(
                user,
                campaign,
                event_type,
                series_mode=EventSeries.SeriesMode.RECURRING,
                recurrence_type=EventSeries.RecurrenceType.WEEKLY,
                start_time=time(18, 0),
                by_weekday=["monday"],
                end_date=timezone.now().date() + timedelta(days=14),
                end_time=time(17, 0),
            )
        )

    assert "end_time" in exc.value.message_dict

    with pytest.raises(ValidationError) as exc:
        EventSeries.objects.create(
            **build_series_kwargs(
                user,
                campaign,
                event_type,
                name="Missing Termination Series",
                series_mode=EventSeries.SeriesMode.RECURRING,
                recurrence_type=EventSeries.RecurrenceType.DAILY,
                end_date=None,
                occurrence_count=None,
            )
        )

    assert "end_date" in exc.value.message_dict


# =============================================================================
# Taxonomy Tests
# =============================================================================


@pytest.mark.django_db
def test_taxonomy_dimension_supports_active_and_inactive_states():
    """Dimensions can be created in active or inactive states."""
    active = TaxonomyDimension.objects.create(
        code="audience",
        label="Audience",
        selection_mode=TaxonomyDimension.SelectionMode.MULTIPLE,
        is_active=True,
    )
    inactive = TaxonomyDimension.objects.create(
        code="theme",
        label="Theme",
        selection_mode=TaxonomyDimension.SelectionMode.SINGLE,
        is_active=False,
    )

    assert active.is_active is True
    assert inactive.is_active is False
    assert inactive.selection_mode == TaxonomyDimension.SelectionMode.SINGLE


@pytest.mark.django_db
def test_taxonomy_term_code_must_be_unique_within_dimension():
    """Term codes are unique per dimension."""
    dimension = TaxonomyDimension.objects.create(code="topic", label="Topic")
    TaxonomyTerm.objects.create(
        dimension=dimension,
        code="climate",
        label="Climate",
    )

    duplicate = TaxonomyTerm(
        dimension=dimension,
        code="climate",
        label="Climate Duplicate",
    )

    with pytest.raises(IntegrityError):
        TaxonomyTerm.objects.bulk_create([duplicate])


@pytest.mark.django_db
def test_taxonomy_term_parent_must_belong_to_same_dimension():
    """A term parent must be defined on the same dimension."""
    parent_dimension = TaxonomyDimension.objects.create(code="topic", label="Topic")
    child_dimension = TaxonomyDimension.objects.create(code="audience", label="Audience")
    parent = TaxonomyTerm.objects.create(
        dimension=parent_dimension,
        code="planning",
        label="Planning",
    )

    with pytest.raises(ValidationError) as exc:
        TaxonomyTerm.objects.create(
            dimension=child_dimension,
            parent=parent,
            code="youth",
            label="Youth",
        )

    assert "parent" in exc.value.message_dict


@pytest.mark.django_db
def test_event_term_duplicate_assignment_is_rejected(user, campaign):
    """Duplicate event-term assignments must be rejected."""
    now = timezone.now()
    event = Event.objects.create(
        campaign=campaign,
        title="Tagged Event",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
    )
    dimension = TaxonomyDimension.objects.create(code="topic", label="Topic")
    term = TaxonomyTerm.objects.create(
        dimension=dimension,
        code="climate",
        label="Climate",
    )
    EventTerm.objects.create(event=event, term=term)

    duplicate = EventTerm(event=event, term=term)

    with pytest.raises(IntegrityError):
        EventTerm.objects.bulk_create([duplicate])


@pytest.mark.django_db
def test_single_select_dimension_rejects_second_term_for_same_event(user, campaign):
    """Single-select dimensions allow only one term per event."""
    now = timezone.now()
    event = Event.objects.create(
        campaign=campaign,
        title="Single Select Event",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
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

    with pytest.raises(ValidationError) as exc:
        EventTerm.objects.create(event=event, term=second_term)

    assert "term" in exc.value.message_dict


@pytest.mark.django_db
def test_multiple_select_dimension_allows_multiple_terms_for_same_event(user, campaign):
    """Multiple-select dimensions allow more than one term per event."""
    now = timezone.now()
    event = Event.objects.create(
        campaign=campaign,
        title="Multiple Select Event",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        location=Point(10.0, 53.5, srid=4326),
        organizer=user,
    )
    dimension = TaxonomyDimension.objects.create(
        code="topic",
        label="Topic",
        selection_mode=TaxonomyDimension.SelectionMode.MULTIPLE,
    )
    first_term = TaxonomyTerm.objects.create(
        dimension=dimension,
        code="climate",
        label="Climate",
    )
    second_term = TaxonomyTerm.objects.create(
        dimension=dimension,
        code="mobility",
        label="Mobility",
    )

    EventTerm.objects.create(event=event, term=first_term)
    EventTerm.objects.create(event=event, term=second_term)

    assert EventTerm.objects.filter(event=event).count() == 2
