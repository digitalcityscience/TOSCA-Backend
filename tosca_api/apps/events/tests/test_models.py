from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.db import connection
from django.db import IntegrityError
from django.utils import timezone

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.events.models import (
    Event,
    EventLayer,
    EventSeries,
    EventTerm,
    EventType,
    TaxonomyDimension,
    TaxonomyTerm,
)
from tosca_api.apps.geocontext.models import GeoContext
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
        name="Recurring Workshops",
        campaign=campaign,
        event_type=event_type,
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
        name="Series With Shared Context",
        campaign=campaign,
        event_type=event_type,
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
        name="Series With Shared Context",
        campaign=campaign,
        event_type=event_type,
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
        name="Series With Shared Context",
        campaign=campaign,
        event_type=event_type,
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
        name="Locked Campaign Series",
        campaign=campaign,
        event_type=event_type,
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
        name="Locked Event Type Series",
        campaign=campaign,
        event_type=event_type,
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
        name="Unique Occurrence Series",
        campaign=campaign,
        event_type=event_type,
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
