from django.contrib.auth.models import User
from django.test import TestCase

from tosca_api.apps.geodata_providers.models import GeodataEngine, Layer, Store, Workspace
from tosca_api.apps.geodata_providers.services.queries import WorkspaceQueryService


class WorkspaceQueryServiceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="workspace-query-user", password="testpass123")
        self.provider = GeodataEngine.objects.create(
            name="Workspace Query Engine",
            description="provider",
            engine_type="geoserver",
            base_url="http://workspace.example/geoserver",
            admin_username="admin",
            admin_password="secret",
            created_by=self.user,
        )
        self.other_provider = GeodataEngine.objects.create(
            name="Other Engine",
            description="other provider",
            engine_type="martin",
            base_url="http://other.example",
            admin_username="admin",
            admin_password="secret",
            created_by=self.user,
        )
        self.inactive_provider = GeodataEngine.objects.create(
            name="Inactive Workspace Engine",
            description="inactive provider",
            engine_type="geoserver",
            base_url="http://inactive-workspace.example/geoserver",
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

    def test_get_workspace_detail_returns_normalized_shape(self):
        store = Store.objects.create(
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
        Layer.objects.create(
            workspace=self.workspace,
            store=store,
            name="tram_lines",
            title="Tram Lines",
            description="Transit layer",
            table_name="tram_lines",
            geometry_column="geom",
            geometry_type="LineString",
            srid=4326,
            publishing_state="PUBLISHED",
            is_public=True,
            created_by=self.user,
        )

        result = WorkspaceQueryService.get_workspace_detail(
            provider_id=self.provider.id,
            workspace_id=self.workspace.id,
        )

        self.assertEqual(
            set(result.keys()),
            {"id", "name", "description", "provider", "stores", "layers"},
        )
        self.assertEqual(result["id"], str(self.workspace.id))
        self.assertEqual(result["name"], "mobility")
        self.assertEqual(
            result["provider"],
            {
                "id": str(self.provider.id),
                "name": "Workspace Query Engine",
                "engine_type": "geoserver",
            },
        )
        self.assertEqual(len(result["stores"]), 1)
        self.assertEqual(result["stores"][0]["name"], "mobility_store")
        self.assertEqual(result["stores"][0]["store_type"], "postgis")
        self.assertEqual(len(result["layers"]), 1)
        self.assertEqual(result["layers"][0]["name"], "tram_lines")
        self.assertEqual(result["layers"][0]["publishing_state"], "PUBLISHED")
        self.assertTrue(result["layers"][0]["is_public"])

    def test_get_workspace_detail_excludes_non_public_or_unpublished_layers_by_default(self):
        store = Store.objects.create(
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
        Layer.objects.create(
            workspace=self.workspace,
            store=store,
            name="public_layer",
            title="Public Layer",
            description="Visible layer",
            table_name="public_layer",
            geometry_column="geom",
            geometry_type="LineString",
            srid=4326,
            publishing_state="PUBLISHED",
            is_public=True,
            created_by=self.user,
        )
        Layer.objects.create(
            workspace=self.workspace,
            store=store,
            name="private_layer",
            title="Private Layer",
            description="Restricted layer",
            table_name="private_layer",
            geometry_column="geom",
            geometry_type="Polygon",
            srid=4326,
            publishing_state="PUBLISHED",
            is_public=False,
            created_by=self.user,
        )
        draft_store = Store.objects.create(
            workspace=self.workspace,
            geodata_engine=self.provider,
            name="draft_store",
            description="Draft store",
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
            workspace=self.workspace,
            store=draft_store,
            name="draft_layer",
            title="Draft Layer",
            description="Unpublished layer",
            table_name="draft_layer",
            geometry_column="geom",
            geometry_type="Polygon",
            srid=4326,
            publishing_state="DRAFT",
            is_public=True,
            created_by=self.user,
        )

        result = WorkspaceQueryService.get_workspace_detail(
            provider_id=self.provider.id,
            workspace_id=self.workspace.id,
        )

        self.assertEqual([layer["name"] for layer in result["layers"]], ["public_layer"])
        self.assertEqual([store["name"] for store in result["stores"]], ["mobility_store"])

    def test_get_workspace_detail_raises_when_workspace_is_not_in_provider_scope(self):
        with self.assertRaises(Workspace.DoesNotExist):
            WorkspaceQueryService.get_workspace_detail(
                provider_id=self.other_provider.id,
                workspace_id=self.workspace.id,
            )

    def test_get_workspace_detail_excludes_inactive_provider_by_default(self):
        with self.assertRaises(Workspace.DoesNotExist):
            WorkspaceQueryService.get_workspace_detail(
                provider_id=self.inactive_provider.id,
                workspace_id=self.inactive_workspace.id,
            )

    def test_get_workspace_detail_can_include_inactive_provider_explicitly(self):
        result = WorkspaceQueryService.get_workspace_detail(
            provider_id=self.inactive_provider.id,
            workspace_id=self.inactive_workspace.id,
            include_inactive=True,
        )

        self.assertEqual(result["id"], str(self.inactive_workspace.id))
        self.assertEqual(result["provider"]["id"], str(self.inactive_provider.id))

    def test_list_provider_workspaces_returns_workspace_summaries(self):
        store = Store.objects.create(
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
        Layer.objects.create(
            workspace=self.workspace,
            store=store,
            name="tram_lines",
            title="Tram Lines",
            description="Transit layer",
            table_name="tram_lines",
            geometry_column="geom",
            geometry_type="LineString",
            srid=4326,
            publishing_state="PUBLISHED",
            is_public=True,
            created_by=self.user,
        )
        Layer.objects.create(
            workspace=self.workspace,
            store=store,
            name="draft_layer",
            title="Draft Layer",
            description="Transit draft layer",
            table_name="draft_layer",
            geometry_column="geom",
            geometry_type="LineString",
            srid=4326,
            publishing_state="DRAFT",
            is_public=True,
            created_by=self.user,
        )

        results = WorkspaceQueryService.list_provider_workspaces(provider_id=self.provider.id)

        self.assertEqual(len(results), 2)
        mobility = next(item for item in results if item["id"] == str(self.workspace.id))
        environment = next(item for item in results if item["id"] == str(self.other_workspace.id))
        self.assertEqual(
            set(mobility.keys()),
            {"id", "name", "description", "provider", "store_count", "layer_count"},
        )
        self.assertEqual(mobility["store_count"], 1)
        self.assertEqual(mobility["layer_count"], 1)
        self.assertEqual(environment["store_count"], 0)
        self.assertEqual(environment["layer_count"], 0)

    def test_list_provider_workspaces_excludes_inactive_provider_by_default(self):
        results = WorkspaceQueryService.list_provider_workspaces(
            provider_id=self.inactive_provider.id,
        )

        self.assertEqual(results, [])

    def test_list_provider_workspaces_can_include_inactive_provider_explicitly(self):
        results = WorkspaceQueryService.list_provider_workspaces(
            provider_id=self.inactive_provider.id,
            include_inactive=True,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], str(self.inactive_workspace.id))
