"""fetch_ogc_features and friends: registered sources only, kept per user, summarised for the model."""

from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase
from mcp.server.mcpserver.exceptions import ToolError

from tosca_api.apps.mcp_server import sources
from tosca_api.apps.mcp_server.principal import McpPrincipal
from tosca_api.apps.mcp_server.tools import external_data

RITA = McpPrincipal(sub="sub-rita", username="rita", org_slug="dcs", level="READER", exempt=False)
OMAR = McpPrincipal(sub="sub-omar", username="omar", org_slug="gq2", level="READER", exempt=False)
SOURCE = sources.CatalogSource(
    id="hamburg-ogc", title="Hamburg OGC", description="d", url="https://api.hamburg.example/datasets/v1",
    protocol=sources.OGC_API_FEATURES,
)
LANDING = "https://api.hamburg.example/datasets/v1/bezirke"
DATASETS = [{"id": "bezirke", "title": "Bezirke", "description": "", "landing_page_url": LANDING}]
COLLECTIONS = [
    {"id": "bezirk", "title": "Bezirk", "description": "", "item_count": 7, "geojson": True},
    {"id": "raster", "title": "Raster", "description": "", "item_count": 1, "geojson": False},
]


def square(name: str, x: float, number: int) -> dict:
    ring = [[x, 53.5], [x + 0.1, 53.5], [x + 0.1, 53.6], [x, 53.6], [x, 53.5]]
    return {"type": "Feature", "properties": {"bezirk_name": name, "nr": number},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


PAGE_1 = {"type": "FeatureCollection", "numberMatched": 3, "features": [square("Altona", 9.8, 2), square("Mitte", 9.9, 1)],
          "links": [{"rel": "next", "href": f"{LANDING}/collections/bezirk/items?offset=2&f=json"}]}
PAGE_2 = {"type": "FeatureCollection", "features": [square("Nord", 10.0, 4)], "links": []}


@patch.object(sources, "get_source", return_value=SOURCE)
@patch("tosca_api.apps.external_catalog.remote.ogc_collections", return_value=COLLECTIONS)
@patch("tosca_api.apps.external_catalog.remote.ogc_datasets", return_value=DATASETS)
class FetchOgcFeaturesTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def fetch(self, fetch_json, **kwargs):
        fetch_json.side_effect = [PAGE_1, PAGE_2]
        with patch("tosca_api.apps.external_catalog.remote.fetch_json", fetch_json):
            return external_data.fetch_ogc_features_for(
                RITA, source_id="hamburg-ogc", dataset_id="bezirke", collection_id="bezirk", **kwargs
            )

    def test_follows_pages_and_answers_with_a_summary(self, *_):
        fetch_json = MagicMock()
        summary = self.fetch(fetch_json, bbox=[9.7, 53.4, 10.2, 53.7], properties={"art": "bezirk"})
        first_call, second_call = fetch_json.call_args_list
        self.assertEqual(first_call.args[1], f"{LANDING}/collections/bezirk/items")
        self.assertEqual(first_call.args[2], {"f": "json", "limit": 500, "bbox": "9.7,53.4,10.2,53.7", "art": "bezirk"})
        # The next link carries its own query.
        self.assertEqual((second_call.args[1], second_call.args[2]), (PAGE_1["links"][0]["href"], None))
        self.assertEqual((summary["returned"], summary["number_matched"], summary["complete"]), (3, 3, True))
        self.assertEqual(summary["bbox"], [9.8, 53.5, 10.1, 53.6])
        self.assertEqual(summary["geometry_types"], ["Polygon"])
        self.assertEqual(summary["attributes"], [
            {"name": "bezirk_name", "type": "text", "examples": ["Altona", "Mitte", "Nord"]},
            {"name": "nr", "type": "number", "examples": [2, 1, 4]},
        ])
        self.assertTrue(summary["ref"].startswith("fetch-"))
        self.assertNotIn("geometry", str(summary["preview"]))

    def test_limit_stops_reading(self, *_):
        fetch_json = MagicMock()
        summary = self.fetch(fetch_json, limit=2)
        self.assertEqual(fetch_json.call_count, 1)
        self.assertEqual(fetch_json.call_args.args[2]["limit"], 2)
        self.assertEqual((summary["returned"], summary["complete"]), (2, False))

    def test_only_listed_collections_with_geojson(self, *_):
        for collection, message in (("../../admin", "no collection"), ("raster", "does not offer GeoJSON")):
            with self.subTest(collection=collection), self.assertRaisesMessage(ToolError, message):
                external_data.fetch_ogc_features_for(
                    RITA, source_id="hamburg-ogc", dataset_id="bezirke", collection_id=collection
                )
        with self.assertRaisesMessage(ToolError, "cannot be used as a property filter"):
            external_data.fetch_ogc_features_for(
                RITA, source_id="hamburg-ogc", dataset_id="bezirke", collection_id="bezirk", properties={"limit": "9"}
            )

    def test_kept_data_is_paged_filtered_and_private(self, *_):
        ref = self.fetch(MagicMock())["ref"]
        page = external_data.get_fetched_features_for(RITA, ref=ref, limit=2)
        self.assertEqual((page["total"], page["next_offset"], len(page["features"])), (3, 2, 2))
        self.assertNotIn("geometry", page["features"][0])
        altona = external_data.get_fetched_features_for(RITA, ref=ref, where={"bezirk_name": "Altona"}, with_geometry=True)
        self.assertEqual(altona["total"], 1)
        self.assertEqual(altona["features"][0]["geometry"]["type"], "Polygon")
        with self.assertRaisesMessage(ToolError, "Unknown or expired reference"):
            external_data.get_fetched_features_for(OMAR, ref=ref)
        geojson = external_data.map_geojson(external_data.kept(RITA, ref))
        self.assertEqual((geojson["type"], len(geojson["features"])), ("FeatureCollection", 3))


class OnlyOgcSourcesTests(SimpleTestCase):
    def test_other_protocols_are_refused(self):
        tosca = sources.CatalogSource(id="ngcn", title="NGCN", description="d", url="https://x.example/api",
                                      protocol=sources.TOSCA_CATALOG)
        with patch.object(sources, "get_source", return_value=tosca), \
                self.assertRaisesMessage(ToolError, "not an OGC API Features source"):
            external_data.fetch_ogc_features_for(RITA, source_id="ngcn", dataset_id="a", collection_id="b")
