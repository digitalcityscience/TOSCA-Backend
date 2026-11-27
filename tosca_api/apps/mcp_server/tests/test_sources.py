"""Catalog sources: the registry files and the search/describe adapters."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase
from mcp.server.mcpserver.exceptions import ToolError

from tosca_api.apps.external_catalog.remote import RemoteServiceError
from tosca_api.apps.mcp_server import sources
from tosca_api.apps.mcp_server.tools.sources import (
    describe_source_item_payload,
    search_sources_payload,
)

NGCN = "https://ngcn.example/api/v1/catalog"
PROVIDER = "01a1-prov"
LAYERS = {"layers": {"layer": [
    {"name": "hamburg_bezirke", "href": f"{NGCN}/providers/{PROVIDER}/workspaces/HAMBURG-ADMINISTRATIVE/layers/hamburg_bezirke"},
    {"name": "bike_lanes", "href": f"{NGCN}/providers/{PROVIDER}/workspaces/Hamburg-Bike-Infrastructure/layers/bike_lanes"},
]}}
RESOURCE = {"featureType": {
    "title": "Hamburg Bezirke", "abstract": "Official   Hamburg\ndistricts", "srs": "EPSG:25832",
    "latLonBoundingBox": {"minx": 8.4, "miny": 53.3, "maxx": 10.3, "maxy": 53.9},
    "attributes": {"attribute": [{"name": "bezirk_name", "binding": "java.lang.String"}]},
}}
DATASETS = [
    {"id": "lod2_hamburg", "title": "3D-Gebäudemodell LoD2-DE Hamburg", "description": "Gebäude mit Dachformen",
     "landing_page_url": "https://ogc.example/v1/lod2_hamburg"},
    {"id": "baumkataster", "title": "Straßenbaumkataster", "description": "Bäume",
     "landing_page_url": "https://ogc.example/v1/baumkataster"},
]
COLLECTIONS = [{"id": "building", "title": "Building", "description": "", "item_type": "feature",
                "item_count": 10, "geojson": True}]
STA_LAYERS = [
    {"service_name": "HH_STA_AutomatisierteVerkehrsmengenerfassung", "layer_name": "Anzahl_Fahrraeder_Zaehlfeld_15-Min",
     "datastream_count": 42},
    {"service_name": "HH_STA_E-Ladestationen", "layer_name": "Ladestationen_Status", "datastream_count": 7},
]


def fake_fetch_json(_service, url, params=None):
    if url.endswith("/providers"):
        return [{"id": PROVIDER, "name": "GeoServer", "base_url": "internal"}]
    if url.endswith("/layers"):
        return LAYERS
    if "/resources/" in url:
        return RESOURCE
    raise RemoteServiceError("unexpected")


def write(directory, name, data, notes=None):
    (Path(directory) / f"{name}.json").write_text(json.dumps(data))
    if notes:
        (Path(directory) / f"{name}.md").write_text(notes)


SOURCE = {"title": "T", "description": "D", "protocol": "ogc-api-features", "url": "https://x.example"}


class RegistryTests(SimpleTestCase):
    def test_shipped_sources_are_valid(self):
        loaded = sources.load_sources(environ={"MCP_SOURCE_NGCN_URL": NGCN + "/"})
        self.assertEqual([source.id for source in loaded], ["ngcn", "hamburg-ogc", "hamburg-sensorthings"])
        self.assertEqual(loaded[0].url, NGCN)  # trailing slash dropped
        self.assertTrue(all(source.notes and source.extent for source in loaded))

    def test_source_with_unset_url_variable_is_left_out(self):
        self.assertEqual([source.id for source in sources.load_sources(environ={})], ["hamburg-ogc", "hamburg-sensorthings"])

    def test_invalid_files_fail_loudly(self):
        cases = {
            "missing": {**SOURCE, "id": "missing", "title": ""},
            "Bad_Id": {**SOURCE, "id": "Bad_Id"},
            "proto": {**SOURCE, "id": "proto", "protocol": "wfs"},
            "bbox": {**SOURCE, "id": "bbox", "extent": {"bbox": [1, 2, 3]}},
            "scheme": {**SOURCE, "id": "scheme", "url": "file:///etc"},
        }
        for name, data in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                write(directory, name, data)
                with self.assertRaises(ValueError):
                    sources.load_sources(Path(directory), environ={})

    def test_priority_orders_and_notes_are_read(self):
        with tempfile.TemporaryDirectory() as directory:
            write(directory, "b", {**SOURCE, "id": "b", "priority": 1}, notes="Search in Dutch.")
            write(directory, "a", {**SOURCE, "id": "a"})
            loaded = sources.load_sources(Path(directory), environ={})
        self.assertEqual([source.id for source in loaded], ["b", "a"])
        self.assertEqual(loaded[0].summary()["notes"], "Search in Dutch.")
        self.assertNotIn("notes", loaded[1].summary())


@patch.dict("os.environ", {"MCP_SOURCE_NGCN_URL": NGCN})
@patch("tosca_api.apps.mcp_server.sources.remote.sta_layers", return_value=STA_LAYERS)
@patch("tosca_api.apps.mcp_server.sources.remote.ogc_collections", return_value=COLLECTIONS)
@patch("tosca_api.apps.mcp_server.sources.remote.ogc_datasets", return_value=DATASETS)
@patch("tosca_api.apps.mcp_server.sources.remote.fetch_json", side_effect=fake_fetch_json)
class SearchTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_search_goes_through_sources_in_priority_order(self, *_mocks):
        result = search_sources_payload(query="hamburg")
        self.assertEqual([group["source"] for group in result["results"]], ["ngcn", "hamburg-ogc", "hamburg-sensorthings"])
        ngcn = result["results"][0]["items"]
        self.assertEqual([item["title"] for item in ngcn], ["hamburg_bezirke", "bike_lanes"])
        self.assertEqual(ngcn[1]["show_on_map"]["arguments"],
                         {"providerId": PROVIDER, "workspace": "Hamburg-Bike-Infrastructure", "layerName": "bike_lanes"})

    def test_words_match_names_with_underscores_and_german_text(self, *_mocks):
        self.assertEqual(search_sources_payload(query="bike lanes", source="ngcn")["results"][0]["total"], 1)
        hits = search_sources_payload(query="gebäude", source="hamburg-ogc")["results"][0]["items"]
        self.assertEqual([item["item_id"] for item in hits], ["lod2_hamburg"])

    def test_unreachable_source_is_reported_not_fatal(self, _fetch, datasets, *_rest):
        datasets.side_effect = RemoteServiceError("The service answered HTTP 503.")
        result = search_sources_payload(query="hamburg")
        self.assertEqual([group["source"] for group in result["results"]], ["ngcn", "hamburg-sensorthings"])
        self.assertEqual(result["errors"][0]["source"], "hamburg-ogc")
        self.assertIn("503", result["errors"][0]["error"])

    def test_remote_lists_are_cached(self, fetch, *_mocks):
        search_sources_payload(query="x", source="ngcn")
        calls = fetch.call_count
        search_sources_payload(query="y", source="ngcn")
        self.assertEqual(fetch.call_count, calls)

    def test_describe_tosca_layer(self, *_mocks):
        detail = describe_source_item_payload(source="ngcn", item_id=f"{PROVIDER}/HAMBURG-ADMINISTRATIVE/hamburg_bezirke")
        self.assertEqual(detail["title"], "Hamburg Bezirke")
        self.assertEqual(detail["description"], "Official Hamburg districts")
        self.assertEqual(detail["extent_wgs84"], [8.4, 53.3, 10.3, 53.9])
        self.assertEqual(detail["attributes"], [{"name": "bezirk_name", "type": "String"}])

    def test_describe_ogc_dataset_lists_collections(self, *_mocks):
        detail = describe_source_item_payload(source="hamburg-ogc", item_id="lod2_hamburg")
        self.assertEqual(detail["collections"][0]["items_url"],
                         "https://ogc.example/v1/lod2_hamburg/collections/building/items?f=json")
        # GeoJSON collections say how to show them; the arguments are add_ogc_collection's.
        self.assertEqual(detail["collections"][0]["show_on_map"], {"tool": "add_ogc_collection", "arguments": {
            "serviceId": "hamburg-ogc", "datasetId": "lod2_hamburg", "collectionId": "building",
        }})

    def test_sensorthings_layers_are_found_and_say_how_to_show_them(self, *_mocks):
        hits = search_sources_payload(query="fahrraeder", source="hamburg-sensorthings")["results"][0]["items"]
        self.assertEqual([item["title"] for item in hits], ["Anzahl_Fahrraeder_Zaehlfeld_15-Min"])
        # serviceId is the browser's id of the service (its slug), the names are the datastream properties.
        self.assertEqual(hits[0]["show_on_map"], {"tool": "add_sensor_things_layer", "arguments": {
            "serviceId": "hamburg-sensorthings",
            "serviceName": "HH_STA_AutomatisierteVerkehrsmengenerfassung",
            "layerName": "Anzahl_Fahrraeder_Zaehlfeld_15-Min",
        }})
        detail = describe_source_item_payload(source="hamburg-sensorthings", item_id=hits[0]["item_id"])
        self.assertEqual(detail["sensors"], 42)
        self.assertEqual(detail["show_on_map"], hits[0]["show_on_map"])

    def test_bad_ids_are_tool_errors(self, *_mocks):
        for kwargs in ({"source": "nope", "item_id": "x"}, {"source": "hamburg-ogc", "item_id": "nope"},
                       {"source": "hamburg-sensorthings", "item_id": "HH_STA_E-Ladestationen/nope"},
                       {"source": "ngcn", "item_id": "no-slashes"}):
            with self.subTest(**kwargs), self.assertRaises(ToolError):
                describe_source_item_payload(**kwargs)
        with self.assertRaises(ToolError):
            search_sources_payload(query="x", source="nope")
