from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.geostories.models import GeoStory

pytestmark = pytest.mark.django_db


def _run(command: str = "content_preflight") -> str:
    output = StringIO()
    call_command(command, stdout=output)
    return output.getvalue()


def test_preflight_scans_feature_owned_content_without_mutating_it():
    user = get_user_model().objects.create_user(username="preflight-owner")
    campaign = Campaign.objects.create(title="Preflight", created_by=user)
    story = GeoStory.objects.create(
        title="Story",
        campaign=campaign,
        author=user,
        content={"blocks": [{"type": "paragraph", "data": {"text": "Keep"}}]},
    )
    before_updated_at = story.updated_at

    first = _run()
    second = _run()
    story.refresh_from_db()

    assert first == second
    assert "feature content document(s)" in first
    assert "0 fail canonical validation" in first
    assert story.updated_at == before_updated_at


def test_preflight_reports_invalid_feature_content():
    user = get_user_model().objects.create_user(username="invalid-content-owner")
    campaign = Campaign.objects.create(title="Invalid", created_by=user)
    story = GeoStory.objects.create(title="Story", campaign=campaign, author=user)
    GeoStory.objects.filter(pk=story.pk).update(content={"blocks": [{"type": "paragraph"}]})

    output = _run()

    assert f"geostories.GeoStory:{story.id}" in output
    assert "1 fail canonical validation" in output


def test_legacy_preflight_command_remains_an_alias():
    assert _run("geocontext_preflight") == _run("content_preflight")
