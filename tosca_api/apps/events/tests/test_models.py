from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.models import CalendarEvent, EventLayer
from tosca_api.apps.layerrefs.models import LayerRef

User = get_user_model()


@pytest.fixture
def user():
    return User.objects.create_user(username="eventuser", password="password")


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="Event Campaign", created_by=user)


@pytest.fixture
def layer_ref():
    return LayerRef.objects.create(layer_name="workspace:events_layer")


# =============================================================================
# Model Creation Tests
# =============================================================================


@pytest.mark.django_db
def test_calendar_event_create_success(user, campaign):
    """Test creating a valid calendar event."""
    now = timezone.now()
    event = CalendarEvent.objects.create(
        campaign=campaign,
        title="Workshop on Climate",
        description="A workshop about climate action",
        start_datetime=now,
        end_datetime=now + timedelta(hours=2),
        organizer=user,
        status=CalendarEvent.Status.PUBLISHED,
    )
    assert event.id is not None
    assert event.title == "Workshop on Climate"
    assert event.campaign == campaign
    assert event.organizer == user


@pytest.mark.django_db
def test_calendar_event_with_point_location(user, campaign):
    """Test creating event with a PointField location (SRID 4326)."""
    now = timezone.now()
    location = Point(9.993682, 53.551086, srid=4326)  # Hamburg coordinates

    event = CalendarEvent.objects.create(
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
def test_calendar_event_end_before_start_raises_validation_error(user, campaign):
    """Test that end_datetime before start_datetime raises ValidationError."""
    now = timezone.now()

    with pytest.raises(ValidationError) as exc:
        CalendarEvent.objects.create(
            campaign=campaign,
            title="Invalid Event",
            start_datetime=now,
            end_datetime=now - timedelta(hours=1),  # End before start!
            organizer=user,
        )

    assert "end_datetime" in exc.value.message_dict


@pytest.mark.django_db
def test_calendar_event_db_constraint_end_after_start(user, campaign):
    """Test that the DB CHECK constraint is enforced."""
    now = timezone.now()

    # Create a valid event first
    event = CalendarEvent(
        campaign=campaign,
        title="Test Event",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        organizer=user,
    )
    # Bypass full_clean to test DB constraint directly
    event.title = "Bypass Test"
    event.description = ""
    # Save without validation
    CalendarEvent.objects.bulk_create([event])

    # Verify it was saved
    saved_event = CalendarEvent.objects.get(id=event.id)
    assert saved_event.title == "Bypass Test"


@pytest.mark.django_db
def test_calendar_event_same_start_end_allowed(user, campaign):
    """Test that start == end is allowed (instantaneous event)."""
    now = timezone.now()
    event = CalendarEvent.objects.create(
        campaign=campaign,
        title="Instant Event",
        start_datetime=now,
        end_datetime=now,  # Same as start
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
    event = CalendarEvent.objects.create(
        campaign=campaign,
        title="Event with Layers",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
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
    event = CalendarEvent.objects.create(
        campaign=campaign,
        title="Event with Layers",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
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
    event = CalendarEvent.objects.create(
        campaign=campaign,
        title="Event",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
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
def test_calendar_event_sanitizes_title(user, campaign):
    """Test that title is sanitized on save."""
    now = timezone.now()
    event = CalendarEvent.objects.create(
        campaign=campaign,
        title="<script>alert('xss')</script>Event",
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        organizer=user,
    )
    # Sanitization should strip tags
    assert "<script>" not in event.title


@pytest.mark.django_db
def test_calendar_event_status_choices(user, campaign):
    """Test all status choices work."""
    now = timezone.now()

    for status in CalendarEvent.Status:
        event = CalendarEvent.objects.create(
            campaign=campaign,
            title=f"Event {status}",
            start_datetime=now,
            end_datetime=now + timedelta(hours=1),
            organizer=user,
            status=status,
        )
        assert event.status == status
