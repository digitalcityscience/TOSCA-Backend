"""Remote reads behind the admin pickers (external catalog ticket 03)."""

from __future__ import annotations

import json
import socket

import pytest
import requests
from django.test import override_settings

from tosca_api.apps.external_catalog import remote
from tosca_api.apps.external_catalog.models import ExternalService
from tosca_api.apps.organizations.models import Organization


class FakeResponse:
    def __init__(self, body=None, status=200, headers=None, url="", raw: bytes | None = None):
        self.status_code = status
        self.headers = headers or {}
        self.url = url
        self._raw = raw if raw is not None else json.dumps(body).encode()

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308) and "Location" in self.headers

    def iter_content(self, chunk_size):
        for start in range(0, len(self._raw), chunk_size):
            yield self._raw[start : start + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def http(monkeypatch):
    """Route requests.get to a dict of {url: FakeResponse | callable}."""
    routes: dict = {}
    calls: list = []

    def fake_get(url, params=None, **kwargs):
        calls.append((url, params))
        key = (url, tuple(sorted((params or {}).items())))
        target = routes.get(key, routes.get(url))
        if target is None:
            raise AssertionError(f"Unexpected request: {url} {params}")
        return target(url, params) if callable(target) else target

    monkeypatch.setattr(requests, "get", fake_get)
    return routes, calls


@pytest.fixture
def org(db):
    org, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    return org


@pytest.fixture
def ogc(org, django_user_model):
    user = django_user_model.objects.create_user(username="remote-ogc")
    return ExternalService.objects.create(
        organization=org,
        name="ogc",
        slug="ogc",
        service_type=ExternalService.ServiceType.OGC_API_FEATURES,
        base_url="https://api.example.test/datasets/v1",
        title="OGC",
        created_by=user,
    )


@pytest.fixture
def sta(org, django_user_model):
    user = django_user_model.objects.create_user(username="remote-sta")
    return ExternalService.objects.create(
        organization=org,
        name="sta",
        slug="sta",
        service_type=ExternalService.ServiceType.SENSORTHINGS,
        base_url="https://iot.example.test",
        title="STA",
        created_by=user,
    )


# ---------------------------------------------------------------------------
# fetch_json guards
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_fetch_json_refuses_other_hosts_and_schemes(ogc, http):
    with pytest.raises(remote.RemoteServiceError, match="only api.example.test is allowed"):
        remote.fetch_json(ogc, "https://evil.example.test/x")
    with pytest.raises(remote.RemoteServiceError, match="http"):
        remote.fetch_json(ogc, "file:///etc/passwd")
    assert http[1] == []


@pytest.mark.django_db
@override_settings(DEBUG=False)
@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1"])
def test_fetch_json_refuses_non_public_addresses(ogc, http, monkeypatch, address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [(family, None, None, "", (address, 0))])

    with pytest.raises(remote.RemoteServiceError, match="public address"):
        remote.fetch_json(ogc, ogc.base_url)
    assert http[1] == []


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_fetch_json_allows_local_services_in_debug(ogc, http, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [(socket.AF_INET, None, None, "", ("127.0.0.1", 0))])
    http[0][ogc.base_url] = FakeResponse({"ok": True})

    assert remote.fetch_json(ogc, ogc.base_url) == {"ok": True}


@pytest.mark.django_db
def test_fetch_json_follows_same_host_redirects_only(ogc, http):
    routes, _ = http
    routes[ogc.base_url] = FakeResponse(status=301, headers={"Location": "/datasets/v1/"}, url=ogc.base_url)
    routes["https://api.example.test/datasets/v1/"] = FakeResponse({"ok": True})
    assert remote.fetch_json(ogc, ogc.base_url) == {"ok": True}

    routes[ogc.base_url] = FakeResponse(status=302, headers={"Location": "https://evil.example.test/"}, url=ogc.base_url)
    with pytest.raises(remote.RemoteServiceError, match="only api.example.test is allowed"):
        remote.fetch_json(ogc, ogc.base_url)


@pytest.mark.django_db
def test_fetch_json_errors_are_admin_friendly(ogc, http, monkeypatch):
    routes, _ = http
    routes[ogc.base_url] = FakeResponse({"x": 1}, status=503)
    with pytest.raises(remote.RemoteServiceError, match="HTTP 503"):
        remote.fetch_json(ogc, ogc.base_url)

    routes[ogc.base_url] = FakeResponse(raw=b"<html>")
    with pytest.raises(remote.RemoteServiceError, match="JSON"):
        remote.fetch_json(ogc, ogc.base_url)

    monkeypatch.setattr(remote, "MAX_RESPONSE_BYTES", 10)
    routes[ogc.base_url] = FakeResponse(raw=b"[" + b"1," * 20 + b"1]")
    with pytest.raises(remote.RemoteServiceError, match="too large"):
        remote.fetch_json(ogc, ogc.base_url)

    def timeout(url, params):
        raise requests.Timeout()

    routes[ogc.base_url] = timeout
    with pytest.raises(remote.RemoteServiceError, match="Could not reach"):
        remote.fetch_json(ogc, ogc.base_url)


# ---------------------------------------------------------------------------
# OGC API Features
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_ogc_datasets_from_multi_api_catalog_are_sorted_and_cached(ogc, http):
    routes, calls = http
    routes[ogc.base_url] = FakeResponse(
        {
            "apis": [
                {"id": "stadtrad", "title": "StadtRAD", "landingPageUri": "https://api.example.test/datasets/v1/stadtrad/"},
                {"id": "broken", "title": "No landing page"},
                {"id": "baustellen", "title": "Baustellen", "landingPageUri": "https://api.example.test/datasets/v1/baustellen"},
            ]
        }
    )

    first = remote.ogc_datasets(ogc)
    second = remote.ogc_datasets(ogc)

    assert [dataset["id"] for dataset in first] == ["baustellen", "stadtrad"]
    assert first[1]["landing_page_url"] == "https://api.example.test/datasets/v1/stadtrad"
    assert second == first
    assert len(calls) == 1
    remote.ogc_datasets(ogc, refresh=True)
    assert len(calls) == 2


@pytest.mark.django_db
def test_ogc_single_landing_page_is_one_dataset_with_empty_id(ogc, http):
    http[0][ogc.base_url] = FakeResponse({"title": "Single", "links": [{"rel": "data", "href": "x"}]})

    assert remote.ogc_datasets(ogc) == [
        {"id": "", "title": "Single", "description": "", "landing_page_url": "https://api.example.test/datasets/v1"}
    ]


@pytest.mark.django_db
def test_ogc_unrecognised_root_is_an_error(ogc, http):
    http[0][ogc.base_url] = FakeResponse({"hello": "world"})

    with pytest.raises(remote.RemoteServiceError, match="neither"):
        remote.ogc_datasets(ogc)


@pytest.mark.django_db
def test_ogc_collections_flag_geojson_support(ogc, http):
    routes, _ = http
    routes[ogc.base_url] = FakeResponse(
        {"apis": [{"id": "lod2", "title": "LoD2", "landingPageUri": "https://api.example.test/datasets/v1/lod2"}]}
    )
    routes["https://api.example.test/datasets/v1/lod2/collections"] = FakeResponse(
        {
            "collections": [
                {
                    "id": "building",
                    "itemType": "feature",
                    "itemCount": 388729,
                    "links": [{"rel": "items", "type": "application/city+json", "href": "x"}],
                },
                {"id": "roads", "title": "Roads", "links": [{"rel": "items", "type": "application/geo+json", "href": "x"}]},
            ]
        }
    )

    collections = remote.ogc_collections(ogc, "lod2")

    assert collections == [
        {
            "id": "building",
            "title": "building",
            "description": "",
            "item_type": "feature",
            "item_count": 388729,
            "geojson": False,
        },
        {"id": "roads", "title": "Roads", "description": "", "item_type": None, "item_count": None, "geojson": True},
    ]
    with pytest.raises(remote.RemoteServiceError, match="was not found"):
        remote.ogc_collections(ogc, "missing")


@pytest.mark.django_db
def test_wrong_service_type_is_rejected(ogc, sta):
    with pytest.raises(remote.RemoteServiceError):
        remote.ogc_datasets(sta)
    with pytest.raises(remote.RemoteServiceError):
        remote.sta_layers(ogc)


# ---------------------------------------------------------------------------
# SensorThings
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_sta_layers_aggregate_service_layer_pairs_across_pages(sta, http, monkeypatch):
    monkeypatch.setattr(remote, "STA_PAGE_SIZE", 2)
    routes, calls = http
    routes["https://iot.example.test/v1.1"] = FakeResponse({"value": []})
    pages = {
        "0": [
            {"properties": {"serviceName": "HH_STA_E-Ladestationen", "layerName": "Status_E-Ladepunkt"}},
            {"properties": {"serviceName": "HH_STA_StadtRad", "layerName": "Fahrraeder"}},
        ],
        "2": [
            {"properties": {"serviceName": "HH_STA_E-Ladestationen", "layerName": "Status_E-Ladepunkt"}},
            {"properties": {}},
        ],
        "4": [{"properties": None}],
    }

    def datastreams(url, params):
        if params.get("$count") == "true":
            return FakeResponse({"@iot.count": 5, "value": []})
        return FakeResponse({"value": pages[params["$skip"]]})

    routes["https://iot.example.test/v1.1/Datastreams"] = datastreams

    layers = remote.sta_layers(sta)

    assert layers == [
        {"service_name": "HH_STA_E-Ladestationen", "layer_name": "Status_E-Ladepunkt", "datastream_count": 2},
        {"service_name": "HH_STA_StadtRad", "layer_name": "Fahrraeder", "datastream_count": 1},
    ]
    assert all("$count" not in (params or {}) or params["$top"] == "0" for _, params in calls)


@pytest.mark.django_db
def test_sta_base_url_falls_back_to_v1_0(sta, http):
    routes, _ = http
    routes["https://iot.example.test/v1.1"] = FakeResponse(status=404)
    routes["https://iot.example.test/v1.0"] = FakeResponse({"value": []})

    assert remote.sta_base_url(sta) == "https://iot.example.test/v1.0"


@pytest.mark.django_db
def test_sta_layer_exists_quotes_names_and_never_counts(sta, http):
    routes, calls = http
    sta.base_url = "https://iot.example.test/v1.1"
    routes["https://iot.example.test/v1.1/Datastreams"] = FakeResponse({"value": [{"@iot.id": 1}]})

    assert remote.sta_layer_exists(sta, "O'Brien", "Layer") is True
    params = calls[-1][1]
    assert params["$filter"] == "properties/serviceName eq 'O''Brien' and properties/layerName eq 'Layer'"
    assert "$count" not in params


# ---------------------------------------------------------------------------
# Source index ("Load catalog")
# ---------------------------------------------------------------------------

CSW = "https://metaver.de/csw"
RECORD = """<gmd:MD_Metadata><gmd:descriptiveKeywords><gmd:MD_Keywords>
<gmd:keyword>
  <gco:CharacterString>TRAN</gco:CharacterString>
</gmd:keyword>
<gmd:keyword><gmx:Anchor xlink:href="http://publications.europa.eu/resource/authority/data-theme/ENVI">ENVI</gmx:Anchor></gmd:keyword>
<gmd:keyword><gco:CharacterString>ABCD</gco:CharacterString></gmd:keyword>
<gmd:keyword><gco:CharacterString>Radverkehr</gco:CharacterString></gmd:keyword>
</gmd:MD_Keywords></gmd:descriptiveKeywords></gmd:MD_Metadata>"""


def _catalog(routes, ogc, apis):
    routes[ogc.base_url] = FakeResponse({"apis": apis})


def _collections(*collections):
    return FakeResponse(
        {
            "collections": [
                {"links": [{"rel": "items", "type": "application/geo+json", "href": "x"}], **collection}
                for collection in collections
            ]
        }
    )


@pytest.mark.django_db
def test_index_ogc_sources_reads_collections_and_themes_per_dataset(ogc, http):
    routes, calls = http
    landing = "https://api.example.test/datasets/v1"
    _catalog(
        routes,
        ogc,
        [
            {
                "id": "tagged",
                "title": "Tagged",
                "description": "Dataset text",
                "landingPageUri": f"{landing}/tagged",
                "tags": ["ECON", "free text"],
            },
            {"id": "priobike", "title": "PrioBike", "landingPageUri": f"{landing}/priobike", "tags": []},
            {"id": "elsewhere", "title": "Elsewhere", "landingPageUri": f"{landing}/elsewhere"},
            {"id": "down", "title": "Down", "landingPageUri": f"{landing}/down"},
        ],
    )
    routes[f"{landing}/tagged/collections"] = _collections({"id": "t", "itemCount": 5})
    routes[f"{landing}/priobike/collections"] = _collections(
        {"id": "ampel", "title": "Ampel", "description": "Signals", "itemType": "feature"},
        {"id": "mesh", "links": [{"rel": "items", "type": "model/gltf+json", "href": "x"}]},
    )
    routes[f"{landing}/elsewhere/collections"] = _collections({"id": "e"})
    routes[f"{landing}/down/collections"] = FakeResponse(status=503)
    routes[f"{landing}/priobike"] = FakeResponse(
        {"externalDocs": {"url": "https://metaver.de/trefferanzeige?docuuid=E6EDD342-8C08"}}
    )
    routes[f"{landing}/elsewhere"] = FakeResponse({"externalDocs": {"url": "https://intranet.local/?docuuid=X"}})
    routes[CSW] = FakeResponse(raw=RECORD.encode())

    rows, problems = remote.index_ogc_sources(ogc)

    by_id = {row["source_id"]: row for row in rows}
    assert set(by_id) == {"t", "ampel", "mesh", "e"}
    assert by_id["t"] == {
        "dataset_id": "tagged",
        "dataset_title": "Tagged",
        "source_id": "t",
        "title": "t",
        "description": "Dataset text",
        "item_type": "",
        "geojson": True,
        "count": 5,
        "themes": ["ECON"],
    }
    assert (by_id["ampel"]["description"], by_id["ampel"]["themes"]) == ("Signals", ["ENVI", "TRAN"])
    assert by_id["mesh"]["geojson"] is False
    assert by_id["e"]["themes"] == []
    assert problems == ["Down: The service answered HTTP 503."]
    [(_, csw_params)] = [call for call in calls if call[0] == CSW]
    assert csw_params["id"] == "E6EDD342-8C08" and csw_params["request"] == "GetRecordById"
    assert not any("intranet" in url for url, _ in calls)


@pytest.mark.django_db
def test_index_sta_sources_uses_layer_pairs(sta, monkeypatch):
    monkeypatch.setattr(
        remote,
        "sta_layers",
        lambda service, refresh=False: [
            {"service_name": "HH_STA_StadtRad", "layer_name": "Fahrraeder", "datastream_count": 362}
        ],
    )

    rows, problems = remote.index_sta_sources(sta)

    assert problems == []
    assert rows == [
        {
            "dataset_id": "HH_STA_StadtRad",
            "dataset_title": "HH_STA_StadtRad",
            "source_id": "Fahrraeder",
            "title": "Fahrraeder",
            "description": "",
            "item_type": "datastreams",
            "geojson": True,
            "count": 362,
            "themes": [],
        }
    ]


@pytest.mark.django_db
def test_metadata_themes_only_reads_known_catalogs(http):
    assert remote.metadata_themes("https://evil.example.test/?docuuid=1") == []
    assert remote.metadata_themes("https://metaver.de/trefferanzeige") == []
    assert http[1] == []
