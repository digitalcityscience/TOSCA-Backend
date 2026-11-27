"""list_geostories: the same stories GET /api/v1/stories/ lists for the caller (needs the database)."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.geostories.models import GeoStory
from tosca_api.apps.mcp_server.principal import McpPrincipal
from tosca_api.apps.mcp_server.tools.geostories import list_geostories_for
from tosca_api.apps.organizations.models import Organization


def principal(org_slug: str | None = None, exempt: bool = False) -> McpPrincipal:
    return McpPrincipal(sub="s", username="u", org_slug=org_slug, level="READER", exempt=exempt)


class ListGeostoriesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create(username="author")
        own = Organization.objects.create(name="Own", slug="own")
        other = Organization.objects.create(name="Other", slug="other")
        public = Campaign.objects.create(title="Public", created_by=user, organization=other, visibility="public")
        private = Campaign.objects.create(title="Private", created_by=user, organization=other, visibility="private")
        mine = Campaign.objects.create(title="Mine", created_by=user, organization=own, visibility="private")
        cls.published = GeoStory.objects.create(author=user, title="Green Altona", summary="Parks", campaign=public, status="published")
        GeoStory.objects.create(author=user, title="Hidden", campaign=private, status="published")
        GeoStory.objects.create(author=user, title="Other draft", campaign=public, status="draft")
        cls.own_draft = GeoStory.objects.create(author=user, title="Own draft", campaign=mine, status="draft")

    def titles(self, **kwargs) -> set[str]:
        return {story["title"] for story in list_geostories_for(**kwargs)["stories"]}

    def test_without_organization_only_published_stories_of_public_campaigns(self):
        self.assertEqual(self.titles(principal=principal()), {"Green Altona"})

    def test_own_organization_adds_its_drafts(self):
        self.assertEqual(self.titles(principal=principal("own")), {"Green Altona", "Own draft"})

    def test_platform_admin_sees_everything(self):
        self.assertEqual(len(self.titles(principal=principal(exempt=True))), 4)

    def test_query_and_paging(self):
        result = list_geostories_for(principal("own"), query="altona", limit=1)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["stories"][0]["id"], str(self.published.id))
        self.assertEqual(result["stories"][0]["status"], "published")
        page = list_geostories_for(principal("own"), limit=1)
        self.assertEqual((page["total"], page["next_offset"]), (2, 1))
