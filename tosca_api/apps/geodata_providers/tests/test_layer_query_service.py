from django.contrib.auth.models import User
from django.test import TestCase

from tosca_api.apps.geodata_providers.models import GeodataEngine, Layer, Store, Workspace
from tosca_api.apps.geodata_providers.services.queries import LayerQueryService


class LayerQueryServiceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="layer-query-user", password="testpass123")
        self.provider = GeodataEngine.objects.create(
            name="Layer Query Engine",
            description="provider",
            engine_type="geoserver",
            base_url="http://layer.example/geoserver",
            admin_username="admin",
            admin_password="secret",
            created_by=self.user,
        )
        self.inactive_provider = GeodataEngine.objects.create(
            name="Inactive Layer Engine",
            description="inactive provider",
            engine_type="geoserver",
            base_url="http://inactive-layer.example/geoserver",
            admin_username="admin",
            admin_password="secret",
            is_active=False,
            created_by=self.user,
        )
        self.workspace = Workspace.objects.create(
            geodata_engine=self.provider,
            name="mobility",
            description="Mobility workspace",
            created_by=self.user,
        )
        self.other_workspace = Workspace.objects.create(
            geodata_engine=self.provider,
            name="environment",
            description="Environment workspace",
            created_by=self.user,
        )
        self.inactive_workspace = Workspace.objects.create(
            geodata_engine=self.inactive_provider,
            name="inactive",
            description="Inactive workspace",
            created_by=self.user,
        )
        self.store = Store.objects.create(
            workspace=self.workspace,
            geodata_engine=self.provider,
            name="mobility_store",
            description="Mobility store",
            store_type="postgis",
            host="db",
            port=5432,
            database="gis",
            username="postgres",
            password="secret",
            schema="public",
            created_by=self.user,
        )
        self.other_store = Store.objects.create(
            workspace=self.other_workspace,
            geodata_engine=self.provider,
            name="environment_store",
            description="Environment store",
            store_type="postgis",
            host="db",
            port=5432,
            database="gis",
            username="postgres",
            password="secret",
            schema="public",
            created_by=self.user,
        )
        self.inactive_store = Store.objects.create(
            workspace=self.inactive_workspace,
            geodata_engine=self.inactive_provider,
            name="inactive_store",
            description="Inactive store",
            store_type="postgis",
            host="db",
            port=5432,
            database="gis",
            username="postgres",
            password="secret",
            schema="public",
            created_by=self.user,
        )
        self.layer = Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name="tram_lines",
            title="Tram Lines",
            description="Transit layer",
            table_name="tram_lines",
            geometry_column="geom",
            geometry_type="LineString",
            srid=4326,
            publishing_state="PUBLISHED",
            is_public=True,
            published_url="http://example.com/wms",
            created_by=self.user,
        )
        self.other_layer = Layer.objects.create(
            workspace=self.other_workspace,
            store=self.other_store,
            name="green_areas",
            title="Green Areas",
            description="Park layer",
            table_name="green_areas",
            geometry_column="geom",
            geometry_type="Polygon",
            srid=3857,
            publishing_state="DRAFT",
            is_public=False,
            published_url="",
            created_by=self.user,
        )
        self.inactive_layer = Layer.objects.create(
            workspace=self.inactive_workspace,
            store=self.inactive_store,
            name="inactive_layer",
            title="Inactive Layer",
            description="Inactive provider layer",
            table_name="inactive_layer",
            geometry_column="geom",
            geometry_type="Polygon",
            srid=4326,
            publishing_state="PUBLISHED",
            is_public=True,
            created_by=self.user,
        )

    def test_get_layer_detail_returns_normalized_shape(self):
        result = LayerQueryService.get_layer_detail(layer_id=self.layer.id)

        self.assertEqual(
            set(result.keys()),
            {
                "id",
                "name",
                "title",
                "description",
                "table_name",
                "geometry_column",
                "geometry_type",
                "srid",
                "publishing_state",
                "sync_state",
                "is_public",
                "published_url",
                "layer_settings",
                "provider",
                "workspace",
                "store",
            },
        )
        self.assertEqual(result["id"], str(self.layer.id))
        self.assertEqual(result["name"], "tram_lines")
        self.assertEqual(result["geometry_column"], "geom")
        self.assertEqual(result["geometry_type"], "LineString")
        self.assertEqual(result["srid"], 4326)
        self.assertEqual(result["publishing_state"], "PUBLISHED")
        self.assertEqual(result["sync_state"], "LOCAL_ONLY")
        self.assertTrue(result["is_public"])
        self.assertEqual(
            result["layer_settings"],
            {
                "queryable": True,
                "opaque": False,
                "default_style": None,
                "additional_styles": [],
                "selected_styles": [],
            },
        )
        self.assertEqual(
            result["provider"],
            {
                "id": str(self.provider.id),
                "name": "Layer Query Engine",
                "engine_type": "geoserver",
            },
        )
        self.assertEqual(
            result["workspace"],
            {
                "id": str(self.workspace.id),
                "name": "mobility",
                "description": "Mobility workspace",
            },
        )
        self.assertEqual(
            result["store"],
            {
                "id": str(self.store.id),
                "name": "mobility_store",
                "description": "Mobility store",
                "store_type": "postgis",
            },
        )

    def test_get_layer_detail_raises_for_missing_layer(self):
        with self.assertRaises(Layer.DoesNotExist):
            LayerQueryService.get_layer_detail(layer_id="00000000-0000-0000-0000-000000000000")

    def test_get_layer_detail_excludes_inactive_provider_by_default(self):
        with self.assertRaises(Layer.DoesNotExist):
            LayerQueryService.get_layer_detail(layer_id=self.inactive_layer.id)

    def test_get_layer_detail_can_include_inactive_provider_explicitly(self):
        result = LayerQueryService.get_layer_detail(
            layer_id=self.inactive_layer.id,
            include_inactive=True,
        )

        self.assertEqual(result["id"], str(self.inactive_layer.id))
        self.assertEqual(result["provider"]["id"], str(self.inactive_provider.id))

    def test_list_workspace_layers_returns_only_selected_workspace_layers(self):
        results = LayerQueryService.list_workspace_layers(workspace_id=self.workspace.id)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], str(self.layer.id))
        self.assertEqual(results[0]["name"], "tram_lines")
        self.assertEqual(results[0]["workspace"]["id"], str(self.workspace.id))
        self.assertNotEqual(results[0]["id"], str(self.other_layer.id))

    def test_list_workspace_layers_keeps_private_layers_when_no_visibility_filter_exists(self):
        Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name="private_routes",
            title="Private Routes",
            description="Restricted routes",
            table_name="private_routes",
            geometry_column="geom",
            geometry_type="LineString",
            srid=4326,
            publishing_state="DRAFT",
            is_public=False,
            published_url="",
            created_by=self.user,
        )

        results = LayerQueryService.list_workspace_layers(workspace_id=self.workspace.id)

        self.assertEqual([layer["name"] for layer in results], ["private_routes", "tram_lines"])
        self.assertEqual([layer["is_public"] for layer in results], [False, True])

    def test_list_workspace_layers_excludes_inactive_provider_by_default(self):
        results = LayerQueryService.list_workspace_layers(
            workspace_id=self.inactive_workspace.id,
        )

        self.assertEqual(results, [])

    def test_list_workspace_layers_can_include_inactive_provider_explicitly(self):
        results = LayerQueryService.list_workspace_layers(
            workspace_id=self.inactive_workspace.id,
            include_inactive=True,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], str(self.inactive_layer.id))
