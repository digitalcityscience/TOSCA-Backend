"""S1 regression tests (security tickets doc, ticket 01/02).

Today ``GeoStoryViewSet.get_queryset`` restricts to ``published()`` only for
*anonymous* callers. An authenticated user's retrieve/list queryset has no
organization filter at all, so a DCS user can read another org's (QG2)
draft/archived GeoStory. These tests pin the *desired* target behavior, so
they are RED until ticket 02 rewrites ``get_queryset`` and GREEN afterwards.

Target rule:
    Anonymous:      published/public content only
    Authenticated:  published content from ANY org
                     + unpublished/private content of the caller's own org
    Cross-org draft/archived: never enters the queryset -> retrieve 404
"""

import pytest
from rest_framework.test import APIClient

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.geostories.models import GeoStory
from tosca_api.apps.organizations.models import Organization

pytestmark = pytest.mark.django_db


def _org_token(*roles, org):
    return {"realm_access": {"roles": list(roles)}, "default_organization": org}


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def dcs_org():
    org, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    return org


@pytest.fixture
def qg2_org():
    org, _ = Organization.objects.get_or_create(slug="qg2", defaults={"name": "QG2"})
    return org


@pytest.fixture
def dcs_user(django_user_model):
    return django_user_model.objects.create_user(username="dcs-reader")


@pytest.fixture
def dcs_campaign(dcs_org, dcs_user):
    return Campaign.objects.create(
        title="DCS Campaign", organization=dcs_org, created_by=dcs_user
    )


@pytest.fixture
def qg2_campaign(qg2_org, dcs_user):
    return Campaign.objects.create(
        title="QG2 Campaign", organization=qg2_org, created_by=dcs_user
    )


@pytest.fixture
def qg2_draft_story(qg2_campaign, dcs_user):
    return GeoStory.objects.create(
        title="QG2 Draft",
        status=GeoStory.Status.DRAFT,
        campaign=qg2_campaign,
        author=dcs_user,
    )


@pytest.fixture
def qg2_archived_story(qg2_campaign, dcs_user):
    return GeoStory.objects.create(
        title="QG2 Archived",
        status=GeoStory.Status.ARCHIVED,
        campaign=qg2_campaign,
        author=dcs_user,
    )


@pytest.fixture
def qg2_published_story(qg2_campaign, dcs_user):
    return GeoStory.objects.create(
        title="QG2 Published",
        status=GeoStory.Status.PUBLISHED,
        campaign=qg2_campaign,
        author=dcs_user,
    )


@pytest.fixture
def dcs_draft_story(dcs_campaign, dcs_user):
    return GeoStory.objects.create(
        title="DCS Draft",
        status=GeoStory.Status.DRAFT,
        campaign=dcs_campaign,
        author=dcs_user,
    )


def _get(api_client, user, story):
    api_client.force_authenticate(user=user, token=_org_token("ROLE_DCS_READER", org="dcs"))
    return api_client.get(f"/api/v1/stories/{story.id}/")


def test_cross_org_draft_returns_404(api_client, dcs_user, qg2_draft_story):
    response = _get(api_client, dcs_user, qg2_draft_story)
    assert response.status_code == 404


def test_cross_org_archived_returns_404(api_client, dcs_user, qg2_archived_story):
    response = _get(api_client, dcs_user, qg2_archived_story)
    assert response.status_code == 404


def test_cross_org_published_stays_200(api_client, dcs_user, qg2_published_story):
    """Regression guard: closing S1 must not break cross-org public reads."""
    response = _get(api_client, dcs_user, qg2_published_story)
    assert response.status_code == 200


def test_own_org_draft_returns_200(api_client, dcs_user, dcs_draft_story):
    response = _get(api_client, dcs_user, dcs_draft_story)
    assert response.status_code == 200


def test_list_excludes_cross_org_unpublished(
    api_client, dcs_user, qg2_draft_story, qg2_archived_story, qg2_published_story
):
    api_client.force_authenticate(user=dcs_user, token=_org_token("ROLE_DCS_READER", org="dcs"))
    response = api_client.get("/api/v1/stories/")
    assert response.status_code == 200

    titles = {r["title"] for r in response.data["results"]}
    assert "QG2 Draft" not in titles
    assert "QG2 Archived" not in titles
    assert "QG2 Published" in titles
