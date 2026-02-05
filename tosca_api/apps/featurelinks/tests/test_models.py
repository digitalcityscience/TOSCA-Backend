import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.featurelinks.models import FeatureLink
from tosca_api.apps.geocontext.models import GeoContext
from tosca_api.apps.geostories.models import GeoStory

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
