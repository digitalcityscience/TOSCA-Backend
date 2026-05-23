from datetime import timezone as dt_timezone
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from tosca_api.apps.geodata_providers.models import (
    GeodataEngine,
    Layer,
    LayerStyleAssignment,
    Store,
    Style,
    Workspace,
)
from tosca_api.apps.geodata_providers.sync_service import GeoServerSyncService


class CatalogV1ApiTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="catalog-user", password="testpass123")
        self.provider = GeodataEngine.objects.create(
            name="Catalog Engine",
            description="provider",
            engine_type="geoserver",
            base_url="http://catalog.internal/geoserver",
            public_url="http://catalog.example/geoserver",
            admin_username="admin",
            admin_password="secret",
            is_active=True,
            is_default=True,
            created_by=self.user,
        )
        self.inactive_provider = GeodataEngine.objects.create(
            name="Inactive Engine",
            description="inactive provider",
            engine_type="geoserver",
            base_url="http://inactive.internal/geoserver",
            public_url="http://inactive.example/geoserver",
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
        self.hidden_workspace = Workspace.objects.create(
            geodata_engine=self.provider,
            name="hidden",
            description="Hidden workspace",
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
        self.hidden_store = Store.objects.create(
            workspace=self.hidden_workspace,
            geodata_engine=self.provider,
            name="hidden_store",
            description="Hidden store",
            store_type="postgis",
            host="db",
            port=5432,
            database="gis",
            username="postgres",
            password="secret",
            schema="public",
            created_by=self.user,
        )
        self.raster_store = Store.objects.create(
            workspace=self.workspace,
            geodata_engine=self.provider,
            name="mobility_raster_store",
            description="Mobility raster store",
            store_type="geotiff",
            file_path="/tmp/tram_heatmap.tif",
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
        self.mbstyle = Style.objects.create(
            geodata_engine=self.provider,
            workspace=self.workspace,
            name="mobility-style",
            title="Mobility Style",
            format="mbstyle",
            file_name="mobility-style.mbstyle",
            file_content='{"version":8,"name":"mobility-style","layers":[]}',
            validation_state="VALID",
            remote_state="SYNCED",
            created_by=self.user,
        )
        self.sld_style = Style.objects.create(
            geodata_engine=self.provider,
            workspace=self.workspace,
            name="mobility-sld",
            title="Mobility SLD",
            format="sld",
            file_name="mobility-sld.sld",
            file_content='<StyledLayerDescriptor><NamedLayer /></StyledLayerDescriptor>',
            validation_state="VALID",
            remote_state="SYNCED",
            created_by=self.user,
        )
        self.inactive_style = Style.objects.create(
            geodata_engine=self.inactive_provider,
            workspace=self.inactive_workspace,
            name="inactive-style",
            title="Inactive Style",
            format="mbstyle",
            file_name="inactive-style.mbstyle",
            file_content='{"version":8,"name":"inactive-style","layers":[]}',
            validation_state="VALID",
            remote_state="SYNCED",
            created_by=self.user,
        )
        self.raster_layer = Layer.objects.create(
            workspace=self.workspace,
            store=self.raster_store,
            name="tram_heatmap",
            title="Tram Heatmap",
            description="Raster transit intensity",
            table_name="tram_heatmap",
            geometry_column="rast",
            geometry_type="Polygon",
            srid=3857,
            publishing_state="PUBLISHED",
            is_public=True,
            published_url="http://example.com/wms/raster",
            created_by=self.user,
        )
        Layer.objects.create(
            workspace=self.hidden_workspace,
            store=self.hidden_store,
            name="draft_layer",
            title="Draft Layer",
            description="Draft layer",
            table_name="draft_layer",
            geometry_column="geom",
            geometry_type="Polygon",
            srid=4326,
            publishing_state="DRAFT",
            is_public=True,
            created_by=self.user,
        )
        Layer.objects.create(
            workspace=self.hidden_workspace,
            store=self.hidden_store,
            name="private_layer",
            title="Private Layer",
            description="Private layer",
            table_name="private_layer",
            geometry_column="geom",
            geometry_type="Polygon",
            srid=4326,
            publishing_state="PUBLISHED",
            is_public=False,
            created_by=self.user,
        )
        Layer.objects.create(
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

    def _create_duplicate_provider_catalog(self):
        duplicate_provider = GeodataEngine.objects.create(
            name="Catalog Engine Duplicate",
            description="duplicate provider",
            engine_type="geoserver",
            base_url="http://catalog-duplicate.internal/geoserver",
            public_url="http://catalog-duplicate.example/geoserver",
            admin_username="admin",
            admin_password="secret",
            is_active=True,
            is_default=False,
            created_by=self.user,
        )
        duplicate_workspace = Workspace.objects.create(
            geodata_engine=duplicate_provider,
            name="mobility",
            description="Duplicate mobility workspace",
            created_by=self.user,
        )
        duplicate_store = Store.objects.create(
            workspace=duplicate_workspace,
            geodata_engine=duplicate_provider,
            name="mobility_store_duplicate",
            description="Duplicate mobility store",
            store_type="postgis",
            host="db",
            port=5432,
            database="gis",
            username="postgres",
            password="secret",
            schema="public",
            created_by=self.user,
        )
        duplicate_layer = Layer.objects.create(
            workspace=duplicate_workspace,
            store=duplicate_store,
            name="tram_lines",
            title="Duplicate Tram Lines",
            description="Duplicate transit layer",
            table_name="tram_lines_duplicate",
            geometry_column="geom",
            geometry_type="LineString",
            srid=4326,
            publishing_state="PUBLISHED",
            is_public=True,
            created_by=self.user,
        )
        duplicate_style = Style.objects.create(
            geodata_engine=duplicate_provider,
            workspace=duplicate_workspace,
            name="mobility-style",
            title="Duplicate Mobility Style",
            format="mbstyle",
            file_name="mobility-style-duplicate.mbstyle",
            file_content='{"version":8,"name":"duplicate-mobility-style","layers":[]}',
            validation_state="VALID",
            remote_state="SYNCED",
            created_by=self.user,
        )
        return duplicate_provider, duplicate_workspace, duplicate_layer, duplicate_style

    def test_provider_list_returns_active_provider_bootstrap_shape(self):
        response = self.client.get(reverse("catalog-v1-provider-list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [
                {
                    "id": str(self.provider.id),
                    "name": "Catalog Engine",
                    "base_url": "http://catalog.example/geoserver",
                }
            ],
        )

    def test_provider_list_does_not_expose_internal_connection_fields(self):
        response = self.client.get(reverse("catalog-v1-provider-list"))

        self.assertEqual(response.status_code, 200)
        provider = response.json()[0]
        self.assertEqual(provider["id"], str(self.provider.id))
        self.assertNotIn("admin_username", provider)
        self.assertNotIn("admin_password", provider)
        self.assertNotIn("api_key", provider)
        self.assertNotIn("internal_base_url", provider)
        self.assertNotEqual(provider["base_url"], self.provider.base_url)

    def test_provider_list_returns_empty_list_when_no_active_providers_exist(self):
        GeodataEngine.objects.update(is_active=False)

        response = self.client.get(reverse("catalog-v1-provider-list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_unscoped_catalog_routes_are_not_available(self):
        for path in (
            "/api/v1/catalog/workspaces",
            "/api/v1/catalog/layers",
            "/api/v1/catalog/workspaces/mobility/layers",
            "/api/v1/catalog/workspaces/mobility/layers/tram_lines",
            "/api/v1/catalog/workspaces/mobility/resources/tram_lines",
            "/api/v1/catalog/styles/mobility-style",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)

    def test_workspace_list_returns_only_visible_workspaces(self):
        response = self.client.get(reverse("catalog-v1-provider-workspace-list", kwargs={"provider_id": self.provider.id}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "workspaces": {
                    "workspace": [
                        {
                            "name": "mobility",
                            "href": "http://testserver"
                            + reverse(
                                "catalog-v1-provider-workspace-layer-list",
                                kwargs={"provider_id": self.provider.id, "workspace_name": "mobility"},
                            ),
                        }
                    ]
                }
            },
        )

    def test_provider_scoped_workspace_list_uses_provider_id_in_hrefs(self):
        response = self.client.get(
            reverse(
                "catalog-v1-provider-workspace-list",
                kwargs={"provider_id": self.provider.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "workspaces": {
                    "workspace": [
                        {
                            "name": "mobility",
                            "href": "http://testserver"
                            + reverse(
                                "catalog-v1-provider-workspace-layer-list",
                                kwargs={"provider_id": self.provider.id,
                                    "workspace_name": "mobility",
                                },
                            ),
                        }
                    ]
                }
            },
        )

    def test_provider_scoped_workspace_list_returns_404_for_inactive_provider(self):
        response = self.client.get(
            reverse(
                "catalog-v1-provider-workspace-list",
                kwargs={"provider_id": self.inactive_provider.id},
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_provider_scoped_workspace_lists_allow_duplicate_workspace_names(self):
        duplicate_provider, _, _, _ = self._create_duplicate_provider_catalog()

        primary_response = self.client.get(reverse(
            "catalog-v1-provider-workspace-list",
            kwargs={"provider_id": self.provider.id},
        ))
        duplicate_response = self.client.get(reverse(
            "catalog-v1-provider-workspace-list",
            kwargs={"provider_id": duplicate_provider.id},
        ))

        self.assertEqual(primary_response.status_code, 200)
        self.assertEqual(duplicate_response.status_code, 200)
        self.assertEqual(
            primary_response.json()["workspaces"]["workspace"],
            [
                {
                    "name": "mobility",
                    "href": "http://testserver"
                    + reverse(
                        "catalog-v1-provider-workspace-layer-list",
                        kwargs={"provider_id": self.provider.id, "workspace_name": "mobility"},
                    ),
                }
            ],
        )
        self.assertEqual(
            duplicate_response.json()["workspaces"]["workspace"],
            [
                {
                    "name": "mobility",
                    "href": "http://testserver"
                    + reverse(
                        "catalog-v1-provider-workspace-layer-list",
                        kwargs={
                            "provider_id": duplicate_provider.id,
                            "workspace_name": "mobility",
                        },
                    ),
                }
            ],
        )

    def test_workspace_list_keeps_workspace_when_only_one_layer_is_stale(self):
        Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name="stale_tram_lines",
            title="Stale Tram Lines",
            description="Stale transit layer",
            table_name="stale_tram_lines",
            geometry_column="geom",
            geometry_type="LineString",
            srid=4326,
            publishing_state="PUBLISHED",
            sync_state="STALE",
            is_public=True,
            created_by=self.user,
        )

        response = self.client.get(reverse("catalog-v1-provider-workspace-list", kwargs={"provider_id": self.provider.id}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["workspaces"]["workspace"],
            [
                {
                    "name": "mobility",
                    "href": "http://testserver"
                    + reverse(
                        "catalog-v1-provider-workspace-layer-list",
                        kwargs={
                            "provider_id": self.provider.id,
                            "workspace_name": "mobility",
                        },
                    ),
                }
            ],
        )

    def test_layer_lists_return_only_visible_layers(self):
        response = self.client.get(reverse("catalog-v1-provider-layer-list", kwargs={"provider_id": self.provider.id}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "layers": {
                    "layer": [
                        {
                            "name": "tram_heatmap",
                            "href": "http://testserver"
                            + reverse(
                                "catalog-v1-provider-layer-info",
                                kwargs={"provider_id": self.provider.id,
                                    "workspace_name": "mobility",
                                    "layer_name": "tram_heatmap",
                                },
                            ),
                        },
                        {
                            "name": "tram_lines",
                            "href": "http://testserver"
                            + reverse(
                                "catalog-v1-provider-layer-info",
                                kwargs={"provider_id": self.provider.id,
                                    "workspace_name": "mobility",
                                    "layer_name": "tram_lines",
                                },
                            ),
                        }
                    ]
                }
            },
        )

        workspace_response = self.client.get(
            reverse(
                "catalog-v1-provider-workspace-layer-list",
                kwargs={"provider_id": self.provider.id, "workspace_name": "mobility"},
            )
        )
        self.assertEqual(workspace_response.status_code, 200)
        self.assertEqual(workspace_response.json(), response.json())

    def test_layer_lists_hide_failed_or_stale_provider_resources(self):
        failed_store = Store.objects.create(
            workspace=self.workspace,
            geodata_engine=self.provider,
            name="failed_store",
            description="Failed store",
            store_type="postgis",
            host="db",
            port=5432,
            database="gis",
            username="postgres",
            password="secret",
            schema="public",
            sync_state="FAILED",
            created_by=self.user,
        )
        Layer.objects.create(
            workspace=self.workspace,
            store=failed_store,
            name="failed_store_layer",
            title="Failed Store Layer",
            description="Layer backed by failed store",
            table_name="failed_store_layer",
            geometry_column="geom",
            geometry_type="Polygon",
            srid=4326,
            publishing_state="PUBLISHED",
            sync_state="SYNCED",
            is_public=True,
            created_by=self.user,
        )
        Layer.objects.create(
            workspace=self.workspace,
            store=self.store,
            name="stale_layer",
            title="Stale Layer",
            description="Stale layer",
            table_name="stale_layer",
            geometry_column="geom",
            geometry_type="Polygon",
            srid=4326,
            publishing_state="PUBLISHED",
            sync_state="STALE",
            is_public=True,
            created_by=self.user,
        )

        response = self.client.get(reverse("catalog-v1-provider-layer-list", kwargs={"provider_id": self.provider.id}))

        self.assertEqual(response.status_code, 200)
        layer_names = [
            layer["name"]
            for layer in response.json()["layers"]["layer"]
        ]
        self.assertEqual(layer_names, ["tram_heatmap", "tram_lines"])

    def test_provider_scoped_layer_list_ignores_duplicate_names_from_other_provider(self):
        self._create_duplicate_provider_catalog()

        response = self.client.get(
            reverse(
                "catalog-v1-provider-workspace-layer-list",
                kwargs={"provider_id": self.provider.id, "workspace_name": "mobility"},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["layers"]["layer"],
            [
                {
                    "name": "tram_heatmap",
                    "href": "http://testserver"
                    + reverse(
                        "catalog-v1-provider-layer-info",
                        kwargs={"provider_id": self.provider.id,
                            "workspace_name": "mobility",
                            "layer_name": "tram_heatmap",
                        },
                    ),
                },
                {
                    "name": "tram_lines",
                    "href": "http://testserver"
                    + reverse(
                        "catalog-v1-provider-layer-info",
                        kwargs={"provider_id": self.provider.id,
                            "workspace_name": "mobility",
                            "layer_name": "tram_lines",
                        },
                    ),
                },
            ],
        )

    def test_provider_scoped_layer_lists_do_not_collide_on_duplicate_names(self):
        duplicate_provider, _, _, _ = self._create_duplicate_provider_catalog()

        primary_response = self.client.get(
            reverse(
                "catalog-v1-provider-workspace-layer-list",
                kwargs={"provider_id": self.provider.id, "workspace_name": "mobility"},
            )
        )
        duplicate_response = self.client.get(
            reverse(
                "catalog-v1-provider-workspace-layer-list",
                kwargs={
                    "provider_id": duplicate_provider.id,
                    "workspace_name": "mobility",
                },
            )
        )

        self.assertEqual(primary_response.status_code, 200)
        self.assertEqual(duplicate_response.status_code, 200)
        self.assertEqual(
            [layer["name"] for layer in primary_response.json()["layers"]["layer"]],
            ["tram_heatmap", "tram_lines"],
        )
        self.assertEqual(
            duplicate_response.json()["layers"]["layer"],
            [
                {
                    "name": "tram_lines",
                    "href": "http://testserver"
                    + reverse(
                        "catalog-v1-provider-layer-info",
                        kwargs={
                            "provider_id": duplicate_provider.id,
                            "workspace_name": "mobility",
                            "layer_name": "tram_lines",
                        },
                    ),
                }
            ],
        )

    def test_workspace_layer_list_returns_404_for_hidden_workspace(self):
        response = self.client.get(
            reverse(
                "catalog-v1-provider-workspace-layer-list",
                kwargs={"provider_id": self.provider.id, "workspace_name": "hidden"},
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_workspace_layer_list_returns_404_for_missing_workspace(self):
        response = self.client.get(
            reverse(
                "catalog-v1-provider-workspace-layer-list",
                kwargs={"provider_id": self.provider.id, "workspace_name": "does-not-exist"},
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_workspace_layer_list_returns_404_for_inactive_provider_workspace(self):
        response = self.client.get(
            reverse(
                "catalog-v1-provider-workspace-layer-list",
                kwargs={"provider_id": self.provider.id, "workspace_name": "inactive"},
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_layer_info_returns_frontend_v1_shape(self):
        LayerStyleAssignment.objects.create(
            layer=self.layer,
            style=self.mbstyle,
            role="default",
            is_active=True,
            created_by=self.user,
        )

        response = self.client.get(
            reverse(
                "catalog-v1-provider-layer-info",
                kwargs={"provider_id": self.provider.id,
                    "workspace_name": "mobility",
                    "layer_name": "tram_lines",
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["layer"]
        self.assertEqual(payload["name"], "tram_lines")
        self.assertEqual(payload["type"], "VECTOR")
        self.assertEqual(payload["defaultStyle"]["name"], "mobility-style")
        self.assertEqual(
            payload["defaultStyle"]["href"],
            "http://testserver"
            + reverse("catalog-v1-provider-style-detail", kwargs={"provider_id": self.provider.id, "style_ref": str(self.mbstyle.id)}),
        )
        self.assertEqual(payload["resource"]["@class"], "featureType")
        self.assertEqual(
            payload["resource"]["href"],
            "http://testserver"
            + reverse(
                "catalog-v1-provider-layer-detail",
                kwargs={"provider_id": self.provider.id,
                    "workspace_name": "mobility",
                    "layer_name": "tram_lines",
                },
            ),
        )

    def test_provider_scoped_layer_info_reads_layer_from_selected_provider(self):
        duplicate_provider, _, _, duplicate_style = self._create_duplicate_provider_catalog()
        LayerStyleAssignment.objects.create(
            layer=Layer.objects.get(workspace__geodata_engine=duplicate_provider),
            style=duplicate_style,
            role="default",
            is_active=True,
            created_by=self.user,
        )

        response = self.client.get(
            reverse(
                "catalog-v1-provider-layer-info",
                kwargs={
                    "provider_id": duplicate_provider.id,
                    "workspace_name": "mobility",
                    "layer_name": "tram_lines",
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["layer"]
        self.assertEqual(payload["name"], "tram_lines")
        self.assertEqual(payload["defaultStyle"]["name"], "mobility-style")
        self.assertEqual(
            payload["defaultStyle"]["href"],
            "http://testserver"
            + reverse(
                "catalog-v1-provider-style-detail",
                kwargs={
                    "provider_id": duplicate_provider.id,
                    "style_ref": str(duplicate_style.id),
                },
            ),
        )


    @patch("tosca_api.apps.catalog_api.views.GeoServerRemoteService.get_layer_info")
    def test_full_provider_sync_then_catalog_read_uses_synced_django_state(
        self,
        get_layer_info_mock,
    ):
        get_layer_info_mock.return_value = None
        service = GeoServerSyncService(self.provider)
        service._get_geoserver_workspaces = MagicMock(return_value=["synced_ws"])
        service._get_geoserver_stores = MagicMock(
            return_value=[
                {
                    "name": "synced_store",
                    "store_type": "postgis",
                    "host": "db",
                    "port": 5432,
                    "database": "gis",
                    "username": "postgres",
                    "schema": "public",
                }
            ]
        )
        service._get_geoserver_styles = MagicMock(
            side_effect=lambda workspace=None: (
                [{"name": "synced_style"}] if workspace is None else []
            )
        )
        service.client.get_style_content = MagicMock(
            return_value={
                "content": '{"version":8,"name":"synced_style","layers":[]}',
                "format": "mbstyle",
                "file_name": "synced_style.mbstyle",
            }
        )
        service._get_geoserver_layers = MagicMock(
            return_value=[
                {
                    "name": "synced_layer",
                    "store_name": "synced_store",
                    "title": "Synced Layer",
                    "table_name": "native_synced_table",
                    "advertised": True,
                    "default_style_name": "synced_style",
                }
            ]
        )

        sync_result = service.sync_all_resources(created_by=self.user)

        self.assertTrue(sync_result["success"])
        self.assertEqual(sync_result["workspaces"]["created"], 1)
        self.assertEqual(sync_result["stores"]["created"], 1)
        self.assertEqual(sync_result["styles"]["created"], 1)
        self.assertEqual(sync_result["layers"]["created"], 1)

        provider_response = self.client.get(reverse("catalog-v1-provider-list"))
        workspace_response = self.client.get(reverse("catalog-v1-provider-workspace-list", kwargs={"provider_id": self.provider.id}))
        layer_response = self.client.get(reverse("catalog-v1-provider-layer-info", kwargs={
            "provider_id": self.provider.id,
            "workspace_name": "synced_ws",
            "layer_name": "synced_layer",
        }))
        style = Style.objects.get(geodata_engine=self.provider, name="synced_style")
        style_response = self.client.get(
            reverse("catalog-v1-provider-style-detail", kwargs={"provider_id": self.provider.id, "style_ref": str(style.id)})
        )

        self.assertEqual(provider_response.status_code, 200)
        self.assertEqual(provider_response.json(), [
            {
                "id": str(self.provider.id),
                "name": "Catalog Engine",
                "base_url": "http://catalog.example/geoserver",
            }
        ])
        self.assertEqual(workspace_response.status_code, 200)
        self.assertEqual(
            workspace_response.json()["workspaces"]["workspace"][0]["name"],
            "synced_ws",
        )
        self.assertEqual(layer_response.status_code, 200)
        self.assertEqual(layer_response.json()["layer"]["name"], "synced_layer")
        self.assertEqual(layer_response.json()["layer"]["defaultStyle"]["name"], "synced_style")
        self.assertEqual(style_response.status_code, 200)
        self.assertEqual(style_response.json()["name"], "synced_style")

    @patch("tosca_api.apps.catalog_api.views.GeoServerRemoteService.get_layer_resource_detail")
    def test_layer_detail_falls_back_to_db_shape(self, get_layer_resource_detail_mock):
        get_layer_resource_detail_mock.return_value = None

        response = self.client.get(
            reverse(
                "catalog-v1-provider-layer-detail",
                kwargs={"provider_id": self.provider.id,
                    "workspace_name": "mobility",
                    "layer_name": "tram_lines",
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["featureType"]
        self.assertEqual(payload["name"], "tram_lines")
        self.assertEqual(payload["nativeName"], "tram_lines")
        self.assertEqual(payload["namespace"]["name"], "mobility")
        self.assertEqual(payload["title"], "Tram Lines")
        self.assertEqual(payload["abstract"], "Transit layer")
        self.assertEqual(payload["store"]["name"], "mobility_store")
        self.assertEqual(payload["attributes"], {"attribute": []})

    def test_style_detail_returns_raw_mbstyle_payload_from_db(self):
        response = self.client.get(
            reverse("catalog-v1-provider-style-detail", kwargs={"provider_id": self.provider.id, "style_ref": str(self.mbstyle.id)})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "mobility-style")
        self.assertEqual(response.json()["version"], 8)

    def test_provider_scoped_style_list_returns_only_provider_styles(self):
        duplicate_provider, _, _, duplicate_style = self._create_duplicate_provider_catalog()

        response = self.client.get(
            reverse(
                "catalog-v1-provider-style-list",
                kwargs={"provider_id": duplicate_provider.id},
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([style["id"] for style in response.json()], [str(duplicate_style.id)])

    def test_provider_scoped_style_detail_does_not_cross_provider_name_collision(self):
        duplicate_provider, _, _, _ = self._create_duplicate_provider_catalog()

        primary_response = self.client.get(
            reverse(
                "catalog-v1-provider-style-detail",
                kwargs={"provider_id": self.provider.id,
                    "style_ref": "mobility-style",
                },
            )
        )
        duplicate_response = self.client.get(
            reverse(
                "catalog-v1-provider-style-detail",
                kwargs={
                    "provider_id": duplicate_provider.id,
                    "style_ref": "mobility-style",
                },
            )
        )

        self.assertEqual(primary_response.status_code, 200)
        self.assertEqual(primary_response.json()["name"], "mobility-style")
        self.assertEqual(duplicate_response.status_code, 200)
        self.assertEqual(duplicate_response.json()["name"], "duplicate-mobility-style")

    def test_provider_scoped_style_detail_returns_404_for_other_provider_uuid(self):
        _, _, _, duplicate_style = self._create_duplicate_provider_catalog()

        response = self.client.get(
            reverse(
                "catalog-v1-provider-style-detail",
                kwargs={"provider_id": self.provider.id,
                    "style_ref": str(duplicate_style.id),
                },
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_provider_scoped_routes_return_404_for_invalid_provider_uuid(self):
        response = self.client.get("/api/v1/catalog/providers/not-a-uuid/workspaces")

        self.assertEqual(response.status_code, 404)

    def test_style_detail_returns_404_when_missing(self):
        response = self.client.get(
            reverse("catalog-v1-provider-style-detail", kwargs={"provider_id": self.provider.id, "style_ref": "missing-style"})
        )

        self.assertEqual(response.status_code, 404)

    def test_style_detail_returns_404_for_inactive_provider_style(self):
        response = self.client.get(
            reverse("catalog-v1-provider-style-detail", kwargs={"provider_id": self.provider.id, "style_ref": str(self.inactive_style.id)})
        )

        self.assertEqual(response.status_code, 404)

    def test_style_detail_returns_raw_sld_when_xml_is_accepted(self):
        response = self.client.get(
            reverse("catalog-v1-provider-style-detail", kwargs={"provider_id": self.provider.id, "style_ref": str(self.sld_style.id)}),
            HTTP_ACCEPT="application/vnd.ogc.sld+xml",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("StyledLayerDescriptor", response.content.decode("utf-8"))

    def test_style_detail_returns_406_for_sld_when_json_is_requested(self):
        response = self.client.get(
            reverse("catalog-v1-provider-style-detail", kwargs={"provider_id": self.provider.id, "style_ref": str(self.sld_style.id)}),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 406)

    @patch("tosca_api.apps.catalog_api.views.GeoServerRemoteService.get_layer_resource_detail")
    def test_layer_detail_merges_remote_feature_type_payload(self, get_layer_resource_detail_mock):
        get_layer_resource_detail_mock.return_value = {
            "featureType": {
                "name": "ignored-remote-name",
                "nativeName": "remote_native_name",
                "namespace": {
                    "name": "ignored-workspace",
                    "href": "http://remote.example/workspace",
                },
                "title": "Remote Title",
                "abstract": "Remote abstract",
                "keywords": {"string": ["tram", "mobility"]},
                "nativeCRS": "EPSG:4326",
                "srs": "EPSG:4326",
                "nativeBoundingBox": {"minx": 1, "maxx": 2, "miny": 3, "maxy": 4, "crs": "EPSG:4326"},
                "latLonBoundingBox": {"minx": 5, "maxx": 6, "miny": 7, "maxy": 8, "crs": "EPSG:4326"},
                "store": {
                    "@class": "dataStore",
                    "name": "ignored-store",
                    "href": "http://remote.example/store",
                },
                "attributes": {"attribute": [{"name": "route_id"}]},
            }
        }

        response = self.client.get(
            reverse(
                "catalog-v1-provider-layer-detail",
                kwargs={"provider_id": self.provider.id,
                    "workspace_name": "mobility",
                    "layer_name": "tram_lines",
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["featureType"]
        self.assertEqual(payload["name"], "tram_lines")
        self.assertEqual(payload["nativeName"], "remote_native_name")
        self.assertEqual(payload["namespace"]["name"], "mobility")
        self.assertEqual(payload["title"], "Remote Title")
        self.assertEqual(payload["abstract"], "Remote abstract")
        self.assertEqual(payload["store"]["name"], "mobility_store")
        self.assertEqual(payload["attributes"], {"attribute": [{"name": "route_id"}]})

    @patch("tosca_api.apps.catalog_api.views.GeoServerRemoteService.get_layer_resource_detail")
    def test_layer_detail_returns_raster_fallback_shape(self, get_layer_resource_detail_mock):
        get_layer_resource_detail_mock.return_value = None

        response = self.client.get(
            reverse(
                "catalog-v1-provider-layer-detail",
                kwargs={"provider_id": self.provider.id,
                    "workspace_name": "mobility",
                    "layer_name": "tram_heatmap",
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["coverage"]
        self.assertEqual(payload["name"], "tram_heatmap")
        self.assertEqual(payload["nativeName"], "tram_heatmap")
        self.assertEqual(payload["namespace"]["name"], "mobility")
        self.assertEqual(payload["title"], "Tram Heatmap")
        self.assertEqual(payload["description"], "Raster transit intensity")
        self.assertEqual(payload["store"]["@class"], "coverageStore")
        self.assertEqual(payload["store"]["name"], "mobility_raster_store")
        self.assertEqual(payload["nativeCRS"], "EPSG:3857")
        self.assertEqual(payload["requestSRS"], {"string": "EPSG:3857"})
        self.assertEqual(payload["nativeCoverageName"], "tram_heatmap")

    def test_layer_detail_returns_404_for_hidden_layer(self):
        response = self.client.get(
            reverse(
                "catalog-v1-provider-layer-detail",
                kwargs={"provider_id": self.provider.id,
                    "workspace_name": "hidden",
                    "layer_name": "draft_layer",
                },
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_layer_info_returns_404_for_hidden_layer(self):
        response = self.client.get(
            reverse(
                "catalog-v1-provider-layer-info",
                kwargs={"provider_id": self.provider.id,
                    "workspace_name": "hidden",
                    "layer_name": "draft_layer",
                },
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_layer_info_returns_404_for_inactive_provider_layer(self):
        response = self.client.get(
            reverse(
                "catalog-v1-provider-layer-info",
                kwargs={"provider_id": self.provider.id,
                    "workspace_name": "inactive",
                    "layer_name": "inactive_layer",
                },
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_layer_detail_returns_404_for_inactive_provider_layer(self):
        response = self.client.get(
            reverse(
                "catalog-v1-provider-layer-detail",
                kwargs={"provider_id": self.provider.id,
                    "workspace_name": "inactive",
                    "layer_name": "inactive_layer",
                },
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_layer_info_dates_are_iso8601_strings(self):
        with patch("tosca_api.apps.catalog_api.views.GeoServerRemoteService.get_layer_info", return_value=None):
            response = self.client.get(
                reverse(
                    "catalog-v1-provider-layer-info",
                    kwargs={"provider_id": self.provider.id,
                        "workspace_name": "mobility",
                        "layer_name": "tram_lines",
                    },
                )
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["layer"]
        self.assertTrue(payload["dateCreated"].endswith("Z"))
        self.assertTrue(payload["dateModified"].endswith("Z"))
        self.assertIsNotNone(self.layer.created_at.astimezone(dt_timezone.utc))
