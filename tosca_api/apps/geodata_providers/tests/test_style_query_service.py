from django.contrib.auth.models import User
from django.test import TestCase

from tosca_api.apps.geodata_providers.models import GeodataEngine, Style
from tosca_api.apps.geodata_providers.services.queries import StyleQueryService


class StyleQueryServiceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="style-query", password="testpass123")
        self.provider = GeodataEngine.objects.create(
            name="Style Query Engine",
            description="test",
            engine_type="geoserver",
            base_url="http://example.com/geoserver",
            admin_username="admin",
            admin_password="secret",
            created_by=self.user,
        )
        self.style = Style.objects.create(
            geodata_engine=self.provider,
            name="roads",
            title="Roads",
            format="sld",
            remote_state="SYNCED",
            created_by=self.user,
        )

    def test_list_styles_returns_provider_catalog(self):
        result = StyleQueryService.list_styles(provider_id=self.provider.id)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "roads")
        self.assertEqual(result[0]["remote_state"], "SYNCED")

    def test_get_style_detail_returns_style_payload(self):
        result = StyleQueryService.get_style_detail(style_id=self.style.id)

        self.assertEqual(result["id"], str(self.style.id))
        self.assertEqual(result["qualified_name"], "roads")
