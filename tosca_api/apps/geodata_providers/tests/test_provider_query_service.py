from django.contrib.auth.models import User
from django.test import TestCase

from tosca_api.apps.geodata_providers.models import GeodataEngine, Layer, Store, Workspace
from tosca_api.apps.geodata_providers.services.queries import ProviderQueryService


class ProviderQueryServiceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="provider-query-user", password="testpass123")
        self.active_engine = GeodataEngine.objects.create(
            name="Active Engine",
            description="active provider",
            engine_type="geoserver",
            base_url="http://active.example/geoserver",
            admin_username="admin",
            admin_password="secret",
            is_active=True,
            is_default=True,
            created_by=self.user,
        )
        self.inactive_engine = GeodataEngine.objects.create(
            name="Inactive Engine",
            description="inactive provider",
            engine_type="martin",
            base_url="http://inactive.example",
            admin_username="admin",
            admin_password="secret",
            is_active=False,
            is_default=False,
            created_by=self.user,
        )

    def test_list_providers_returns_active_only_by_default(self):
        providers = ProviderQueryService.list_providers()

        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0]["id"], str(self.active_engine.id))
        self.assertEqual(providers[0]["name"], "Active Engine")
        self.assertTrue(providers[0]["is_active"])

    def test_list_providers_returns_normalized_shape_with_counts(self):
        workspace_one = Workspace.objects.create(
            geodata_engine=self.active_engine,
            name="workspace_one",
            description="first workspace",
            created_by=self.user,
        )
        workspace_two = Workspace.objects.create(
            geodata_engine=self.active_engine,
            name="workspace_two",
            description="second workspace",
            created_by=self.user,
        )
        store_one = Store.objects.create(
            workspace=workspace_one,
            geodata_engine=self.active_engine,
            name="store_one",
            description="store one",
            store_type="postgis",
            host="db",
            port=5432,
            database="gis",
            username="postgres",
            password="secret",
            schema="public",
            created_by=self.user,
        )
        store_two = Store.objects.create(
            workspace=workspace_two,
            geodata_engine=self.active_engine,
            name="store_two",
            description="store two",
            store_type="postgis",
            host="db",
            port=5432,
            database="gis",
            username="postgres",
            password="secret",
            schema="public",
            created_by=self.user,
        )
        Layer.objects.create(
            workspace=workspace_one,
            store=store_one,
            name="layer_one",
            title="Layer One",
            description="first layer",
            table_name="layer_one",
            geometry_column="geom",
            geometry_type="Point",
            srid=4326,
            created_by=self.user,
        )
        Layer.objects.create(
            workspace=workspace_two,
            store=store_two,
            name="layer_two",
            title="Layer Two",
            description="second layer",
            table_name="layer_two",
            geometry_column="geom",
            geometry_type="Polygon",
            srid=3857,
            created_by=self.user,
        )

        providers = ProviderQueryService.list_providers(include_inactive=True)
        active_provider = next(provider for provider in providers if provider["id"] == str(self.active_engine.id))
        inactive_provider = next(provider for provider in providers if provider["id"] == str(self.inactive_engine.id))

        self.assertEqual(
            set(active_provider.keys()),
            {
                "id",
                "name",
                "engine_type",
                "description",
                "is_active",
                "is_default",
                "workspace_count",
                "layer_count",
            },
        )
        self.assertEqual(active_provider["workspace_count"], 2)
        self.assertEqual(active_provider["layer_count"], 2)
        self.assertEqual(active_provider["engine_type"], "geoserver")
        self.assertTrue(active_provider["is_default"])
        self.assertEqual(inactive_provider["workspace_count"], 0)
        self.assertEqual(inactive_provider["layer_count"], 0)
        self.assertFalse(inactive_provider["is_active"])
