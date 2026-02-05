import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.featurelinks.models import FeatureLink
from tosca_api.apps.geocontext.models import GeoContext
from tosca_api.apps.geostories.models import GeoStory
from tosca_api.apps.events.models import CalendarEvent

User = get_user_model()


@pytest.fixture
def user():
    return User.objects.create_user(username="linkuser", password="password")


@pytest.fixture
def campaign(user):
    return Campaign.objects.create(title="Campaign A", created_by=user)


@pytest.fixture
def campaign_b(user):
    return Campaign.objects.create(title="Campaign B", created_by=user)


@pytest.fixture
def story1(user, campaign):
    return GeoStory.objects.create(title="Story 1", campaign=campaign, author=user)


@pytest.fixture
def story2(user, campaign):
    return GeoStory.objects.create(title="Story 2", campaign=campaign, author=user)


@pytest.fixture
def story_b(user, campaign_b):
    return GeoStory.objects.create(title="Story B", campaign=campaign_b, author=user)


@pytest.fixture
def geocontext(user):
    return GeoContext.objects.create(content="Test content", created_by=user)


@pytest.fixture
def event1(user, campaign):
    """Create a calendar event in Campaign A."""
    now = timezone.now()
    return CalendarEvent.objects.create(
        title="Event 1",
        campaign=campaign,
        start_datetime=now + timedelta(days=1),
        end_datetime=now + timedelta(days=1, hours=2),
        organizer=user,
    )


@pytest.fixture
def event2(user, campaign):
    """Create another calendar event in Campaign A."""
    now = timezone.now()
    return CalendarEvent.objects.create(
        title="Event 2",
        campaign=campaign,
        start_datetime=now + timedelta(days=2),
        end_datetime=now + timedelta(days=2, hours=2),
        organizer=user,
    )


@pytest.fixture
def event_b(user, campaign_b):
    """Create a calendar event in Campaign B."""
    now = timezone.now()
    return CalendarEvent.objects.create(
        title="Event B",
        campaign=campaign_b,
        start_datetime=now + timedelta(days=3),
        end_datetime=now + timedelta(days=3, hours=2),
        organizer=user,
    )


@pytest.mark.django_db
def test_featurelink_create_success(user, story1, story2, campaign):
    """Test linking two stories in the same campaign."""
    link = FeatureLink.objects.create(
        campaign=campaign,
        source_object=story1,
        target_object=story2,
        link_type=FeatureLink.LinkType.DIRECT,
        created_by=user,
    )
    assert link.id is not None
    assert link.source_object == story1
    assert link.target_object == story2
    assert link.created_by == user


@pytest.mark.django_db
def test_featurelink_rejects_cross_campaign(user, story1, story_b, campaign):
    """Test that linking objects from different campaigns raises error."""
    with pytest.raises(ValidationError) as exc:
        FeatureLink.objects.create(
            campaign=campaign,
            source_object=story1,
            target_object=story_b,
            created_by=user,
        )
    assert "target_object_id" in exc.value.message_dict


@pytest.mark.django_db
def test_featurelink_rejects_mismatch_link_campaign(user, story1, story2, campaign_b):
    """Test that link campaign must match object campaigns."""
    with pytest.raises(ValidationError) as exc:
        FeatureLink.objects.create(
            campaign=campaign_b,
            source_object=story1,
            target_object=story2,
            created_by=user,
        )
    assert "source_object_id" in exc.value.message_dict


@pytest.mark.django_db
def test_featurelink_rejects_self_link(user, story1, campaign):
    """Test that linking an object to itself raises error."""
    with pytest.raises(ValidationError) as exc:
        FeatureLink.objects.create(
            campaign=campaign,
            source_object=story1,
            target_object=story1,
            created_by=user,
        )
    assert "target_object_id" in exc.value.message_dict


@pytest.mark.django_db
def test_featurelink_rejects_geocontext_as_source(user, geocontext, story1, campaign):
    """Test that GeoContext cannot be used as link source."""
    geocontext_ct = ContentType.objects.get_for_model(GeoContext)
    geostory_ct = ContentType.objects.get_for_model(GeoStory)
    
    with pytest.raises(ValidationError) as exc:
        FeatureLink.objects.create(
            campaign=campaign,
            source_content_type=geocontext_ct,
            source_object_id=geocontext.id,
            target_content_type=geostory_ct,
            target_object_id=story1.id,
            created_by=user,
        )
    assert "source_content_type" in exc.value.message_dict
    assert "not allowed" in str(exc.value)


@pytest.mark.django_db
def test_featurelink_rejects_geocontext_as_target(user, geocontext, story1, campaign):
    """Test that GeoContext cannot be used as link target."""
    geocontext_ct = ContentType.objects.get_for_model(GeoContext)
    geostory_ct = ContentType.objects.get_for_model(GeoStory)
    
    with pytest.raises(ValidationError) as exc:
        FeatureLink.objects.create(
            campaign=campaign,
            source_content_type=geostory_ct,
            source_object_id=story1.id,
            target_content_type=geocontext_ct,
            target_object_id=geocontext.id,
            created_by=user,
        )
    assert "target_content_type" in exc.value.message_dict
    assert "not allowed" in str(exc.value)


@pytest.mark.django_db
def test_featurelink_prevents_duplicates(user, story1, story2, campaign):
    """Test that duplicate links are prevented."""
    FeatureLink.objects.create(
        campaign=campaign,
        source_object=story1,
        target_object=story2,
        created_by=user,
    )
    
    # Attempt to create duplicate - caught at validation level
    with pytest.raises(ValidationError) as exc:
        FeatureLink.objects.create(
            campaign=campaign,
            source_object=story1,
            target_object=story2,
            created_by=user,
        )
    assert "already exists" in str(exc.value)


# =============================================================================
# CalendarEvent Linking Tests (Task 2.3)
# =============================================================================


@pytest.mark.django_db
def test_featurelink_story_to_event(user, story1, event1, campaign):
    """Test linking a GeoStory to a CalendarEvent."""
    link = FeatureLink.objects.create(
        campaign=campaign,
        source_object=story1,
        target_object=event1,
        link_type=FeatureLink.LinkType.READ_MORE,
        created_by=user,
    )
    assert link.id is not None
    assert link.source_object == story1
    assert link.target_object == event1


@pytest.mark.django_db
def test_featurelink_event_to_story(user, event1, story1, campaign):
    """Test linking a CalendarEvent to a GeoStory."""
    link = FeatureLink.objects.create(
        campaign=campaign,
        source_object=event1,
        target_object=story1,
        link_type=FeatureLink.LinkType.DIRECT,
        created_by=user,
    )
    assert link.id is not None
    assert link.source_object == event1
    assert link.target_object == story1


@pytest.mark.django_db
def test_featurelink_event_to_event(user, event1, event2, campaign):
    """Test linking a CalendarEvent to another CalendarEvent."""
    link = FeatureLink.objects.create(
        campaign=campaign,
        source_object=event1,
        target_object=event2,
        link_type=FeatureLink.LinkType.DIRECT,
        created_by=user,
    )
    assert link.id is not None
    assert link.source_object == event1
    assert link.target_object == event2


@pytest.mark.django_db
def test_featurelink_event_rejects_self_link(user, event1, campaign):
    """Test that CalendarEvent cannot link to itself."""
    with pytest.raises(ValidationError) as exc:
        FeatureLink.objects.create(
            campaign=campaign,
            source_object=event1,
            target_object=event1,
            created_by=user,
        )
    assert "target_object_id" in exc.value.message_dict
    assert "Cannot link" in str(exc.value)


@pytest.mark.django_db
def test_featurelink_event_rejects_cross_campaign(user, event1, event_b, campaign):
    """Test that CalendarEvents from different campaigns cannot be linked."""
    with pytest.raises(ValidationError) as exc:
        FeatureLink.objects.create(
            campaign=campaign,
            source_object=event1,
            target_object=event_b,
            created_by=user,
        )
    assert "target_object_id" in exc.value.message_dict


@pytest.mark.django_db
def test_featurelink_story_event_cross_campaign_rejected(user, story1, event_b, campaign):
    """Test that Story and Event from different campaigns cannot be linked."""
    with pytest.raises(ValidationError) as exc:
        FeatureLink.objects.create(
            campaign=campaign,
            source_object=story1,
            target_object=event_b,
            created_by=user,
        )
    assert "target_object_id" in exc.value.message_dict


# =============================================================================
# Object Existence Validation Tests
# =============================================================================

@pytest.mark.django_db
def test_featurelink_rejects_nonexistent_source(user, story1, campaign):
    """Test that a non-existent source object ID is rejected."""
    import uuid
    fake_uuid = uuid.uuid4()
    geostory_ct = ContentType.objects.get_for_model(GeoStory)
    
    with pytest.raises(ValidationError) as exc:
        FeatureLink.objects.create(
            campaign=campaign,
            source_content_type=geostory_ct,
            source_object_id=fake_uuid,  # Non-existent
            target_object=story1,
            created_by=user,
        )
    assert "source_object_id" in exc.value.message_dict
    assert "found with id" in str(exc.value).lower()


@pytest.mark.django_db
def test_featurelink_rejects_nonexistent_target(user, story1, campaign):
    """Test that a non-existent target object ID is rejected."""
    import uuid
    fake_uuid = uuid.uuid4()
    event_ct = ContentType.objects.get_for_model(CalendarEvent)
    
    with pytest.raises(ValidationError) as exc:
        FeatureLink.objects.create(
            campaign=campaign,
            source_object=story1,
            target_content_type=event_ct,
            target_object_id=fake_uuid,  # Non-existent
            created_by=user,
        )
    assert "target_object_id" in exc.value.message_dict
    assert "found with id" in str(exc.value).lower()


@pytest.mark.django_db
def test_featurelink_rejects_wrong_type_uuid(user, campaign, geocontext):
    """Test that using a GeoContext UUID for GeoStory content type is rejected."""
    geostory_ct = ContentType.objects.get_for_model(GeoStory)
    event_ct = ContentType.objects.get_for_model(CalendarEvent)
    
    # Use a GeoContext ID but claim it's a GeoStory
    with pytest.raises(ValidationError) as exc:
        FeatureLink.objects.create(
            campaign=campaign,
            source_content_type=geostory_ct,
            source_object_id=geocontext.id,  # GeoContext ID, not GeoStory
            target_content_type=event_ct,
            target_object_id=geocontext.id,  # Also wrong
            created_by=user,
        )
    assert "source_object_id" in exc.value.message_dict or "target_object_id" in exc.value.message_dict
