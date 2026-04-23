from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from tosca_api.apps.geodata_providers.models import GeodataEngine, Layer, Store, Workspace


class CatalogV1ApiTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="catalog-user", password="testpass123")
        self.provider = GeodataEngine.objects.create(
            name="Catalog Engine",
            description="provider",
            engine_type="geoserver",
            base_url="http://catalog.example/geoserver",
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
            base_url="http://inactive.example/geoserver",
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

    def test_workspace_list_returns_only_visible_workspaces(self):
        response = self.client.get(reverse("catalog-v1-workspace-list"))

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
                                "catalog-v1-workspace-layer-list",
                                kwargs={"workspace_name": "mobility"},
                            ),
                        }
                    ]
                }
            },
        )

    def test_layer_lists_return_only_visible_layers(self):
        response = self.client.get(reverse("catalog-v1-layer-list"))

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
                                "catalog-v1-layer-info",
                                kwargs={
                                    "workspace_name": "mobility",
                                    "layer_name": "tram_heatmap",
                                },
                            ),
                        },
                        {
                            "name": "tram_lines",
                            "href": "http://testserver"
                            + reverse(
                                "catalog-v1-layer-info",
                                kwargs={
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
                "catalog-v1-workspace-layer-list",
                kwargs={"workspace_name": "mobility"},
            )
        )
        self.assertEqual(workspace_response.status_code, 200)
        self.assertEqual(workspace_response.json(), response.json())

    def test_workspace_layer_list_returns_404_for_hidden_workspace(self):
        response = self.client.get(
            reverse(
                "catalog-v1-workspace-layer-list",
                kwargs={"workspace_name": "hidden"},
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_workspace_layer_list_returns_404_for_missing_workspace(self):
        response = self.client.get(
            reverse(
                "catalog-v1-workspace-layer-list",
                kwargs={"workspace_name": "does-not-exist"},
            )
        )

        self.assertEqual(response.status_code, 404)

    @patch("tosca_api.apps.catalog_api.views.GeoServerRemoteService.get_layer_info")
    def test_layer_info_returns_frontend_v1_shape(self, get_layer_info_mock):
        get_layer_info_mock.return_value = {
            "layer": {
                "defaultStyle": {
                    "name": "mobility-style",
                    "href": "http://ignored.example/style",
                }
            }
        }

        response = self.client.get(
            reverse(
                "catalog-v1-layer-info",
                kwargs={
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
            + reverse("catalog-v1-style-detail", kwargs={"style_name": "mobility-style"}),
        )
        self.assertEqual(payload["resource"]["@class"], "featureType")
        self.assertEqual(
            payload["resource"]["href"],
            "http://testserver"
            + reverse(
                "catalog-v1-layer-detail",
                kwargs={
                    "workspace_name": "mobility",
                    "layer_name": "tram_lines",
                },
            ),
        )

    @patch("tosca_api.apps.catalog_api.views.GeoServerRemoteService.get_layer_resource_detail")
    def test_layer_detail_falls_back_to_db_shape(self, get_layer_resource_detail_mock):
        get_layer_resource_detail_mock.return_value = None

        response = self.client.get(
            reverse(
                "catalog-v1-layer-detail",
                kwargs={
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

    @patch("tosca_api.apps.catalog_api.views.GeoServerRemoteService.get_style_detail")
    def test_style_detail_returns_remote_payload(self, get_style_detail_mock):
        get_style_detail_mock.return_value = {
            "version": 8,
            "name": "mobility-style",
            "sources": {},
            "layers": [],
        }

        response = self.client.get(
            reverse("catalog-v1-style-detail", kwargs={"style_name": "mobility-style"})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "mobility-style")

    @patch("tosca_api.apps.catalog_api.views.GeoServerRemoteService.get_style_detail")
    def test_style_detail_returns_404_when_missing(self, get_style_detail_mock):
        get_style_detail_mock.return_value = None

        response = self.client.get(
            reverse("catalog-v1-style-detail", kwargs={"style_name": "missing-style"})
        )

        self.assertEqual(response.status_code, 404)

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
                "catalog-v1-layer-detail",
                kwargs={
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
                "catalog-v1-layer-detail",
                kwargs={
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
                "catalog-v1-layer-detail",
                kwargs={
                    "workspace_name": "hidden",
                    "layer_name": "draft_layer",
                },
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_layer_info_returns_404_for_hidden_layer(self):
        response = self.client.get(
            reverse(
                "catalog-v1-layer-info",
                kwargs={
                    "workspace_name": "hidden",
                    "layer_name": "draft_layer",
                },
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_layer_info_dates_are_iso8601_strings(self):
        with patch("tosca_api.apps.catalog_api.views.GeoServerRemoteService.get_layer_info", return_value=None):
            response = self.client.get(
                reverse(
                    "catalog-v1-layer-info",
                    kwargs={
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
