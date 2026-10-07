"""Server-side reads from external services (external catalog ticket 03).

Used by the admin's source index ("Load catalog", see ``harvest.py``);
never by public endpoints. Every request goes through :func:`_fetch`, which
only talks to one allowed host (the service's own, or the fixed metadata
catalog), refuses non-public addresses (SSRF guard, relaxed with ``DEBUG``
for local test services), follows redirects only on that host, and bounds
time and response size.

Known limit: the address check resolves the host before ``requests`` does,
so a DNS-rebinding host could still slip through. Acceptable here because
the endpoints are staff-only and the URL is the admin-configured service.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

import requests
from django.conf import settings
from django.core.cache import cache

from .models import ExternalService

CONNECT_TIMEOUT_SECONDS = 5
READ_TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_REDIRECTS = 3

OGC_CACHE_SECONDS = 60 * 60
STA_LAYERS_CACHE_SECONDS = 24 * 60 * 60
STA_PAGE_SIZE = 1000
STA_MAX_PAGES = 100
STA_PARALLEL_REQUESTS = 4

GEOJSON_TYPES = ("application/geo+json", "application/json")
OGC_DATA_RELS = ("data", "http://www.opengis.net/def/rel/ogc/1.0/data")

# EU data themes (DCAT-AP), as tagged in German metadata records.
THEMES = {
    "AGRI": "Agriculture, fisheries, forestry and food",
    "ECON": "Economy and finance",
    "EDUC": "Education, culture and sport",
    "ENER": "Energy",
    "ENVI": "Environment",
    "GOVE": "Government and public sector",
    "HEAL": "Health",
    "INTR": "International issues",
    "JUST": "Justice, legal system and public safety",
    "REGI": "Regions and cities",
    "SOCI": "Population and society",
    "TECH": "Science and technology",
    "TRAN": "Transport",
}
# Metadata catalogs whose records are read for themes, by the host that the
# datasets' ``externalDocs`` link points to. Fixed here, never from a service.
METADATA_CATALOGS = {"metaver.de": "https://metaver.de/csw"}
HARVEST_PARALLEL_REQUESTS = 8
_DOC_UUID = re.compile(r"docuuid=([0-9A-Za-z-]+)")
_KEYWORD = re.compile(r"<gmd:keyword>\s*<(?:gco:CharacterString|gmx:Anchor)[^>]*>\s*([A-Z]{4})\s*<")


class RemoteServiceError(Exception):
    """A remote read failed; the message is safe to show to admins.

    ``status`` is the HTTP status when the service answered with one.
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Guarded HTTP
# ---------------------------------------------------------------------------


def _assert_public_host(hostname: str) -> None:
    if settings.DEBUG:
        return
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        raise RemoteServiceError(f"Could not resolve {hostname}.") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not address.is_global:
            raise RemoteServiceError(f"{hostname} does not resolve to a public address.")


def _assert_allowed_url(allowed_host: str | None, url: str) -> None:
    target = urlsplit(url)
    if target.scheme not in ("http", "https"):
        raise RemoteServiceError("Only http(s) URLs can be read.")
    if target.hostname != allowed_host:
        raise RemoteServiceError(f"Refusing to read {target.hostname}: only {allowed_host} is allowed.")
    _assert_public_host(target.hostname or "")


def fetch_json(service: ExternalService, url: str, params: dict | None = None):
    """GET ``url`` on ``service``'s host and return the decoded JSON body."""
    body = _fetch(urlsplit(service.base_url).hostname, url, params, "application/json")
    try:
        return json.loads(body)
    except ValueError as exc:
        raise RemoteServiceError("The service did not return JSON.") from exc


def _fetch(allowed_host: str | None, url: str, params: dict | None, accept: str) -> bytes:
    current_url, current_params = url, params
    for _ in range(MAX_REDIRECTS + 1):
        _assert_allowed_url(allowed_host, current_url)
        try:
            response = requests.get(
                current_url,
                params=current_params,
                headers={"Accept": accept},
                timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException as exc:
            raise RemoteServiceError(f"Could not reach the service ({exc.__class__.__name__}).") from exc

        with response:
            if response.is_redirect:
                location = response.headers.get("Location", "")
                current_url = urljoin(response.url or current_url, location)
                current_params = None  # already part of the redirect target
                continue
            if response.status_code != 200:
                raise RemoteServiceError(
                    f"The service answered HTTP {response.status_code}.", status=response.status_code
                )
            body = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise RemoteServiceError("The service response is too large.")
        return bytes(body)
    raise RemoteServiceError("Too many redirects.")


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def _cache_key(service: ExternalService, name: str) -> str:
    # The base URL is part of the key so an edited service never serves
    # lists cached for its previous URL.
    digest = hashlib.sha256(f"{service.base_url}|{name}".encode()).hexdigest()[:24]
    return f"external_catalog:{service.pk}:{digest}"


def _cached(service: ExternalService, name: str, ttl: int, loader, refresh: bool):
    key = _cache_key(service, name)
    if not refresh:
        value = cache.get(key)
        if value is not None:
            return value
    value = loader()
    cache.set(key, value, ttl)
    return value


# ---------------------------------------------------------------------------
# OGC API Features
# ---------------------------------------------------------------------------


def _require_type(service: ExternalService, service_type: str) -> None:
    if service.service_type != service_type:
        raise RemoteServiceError(f"This is not a {ExternalService.ServiceType(service_type).label} service.")


def ogc_datasets(service: ExternalService, refresh: bool = False) -> list[dict]:
    """Datasets of an OGC API service: a multi-API catalog's APIs, or the
    landing page itself (``id`` "")."""
    _require_type(service, ExternalService.ServiceType.OGC_API_FEATURES)

    def load():
        root = fetch_json(service, service.base_url, {"f": "json"})
        if not isinstance(root, dict):
            raise RemoteServiceError("Unexpected response from the service.")
        if isinstance(root.get("apis"), list):
            datasets = []
            for api in root["apis"]:
                landing_page = api.get("landingPageUri") if isinstance(api, dict) else None
                if not isinstance(landing_page, str) or not landing_page:
                    continue
                datasets.append(
                    {
                        "id": str(api.get("id") or landing_page),
                        "title": str(api.get("title") or api.get("id") or landing_page),
                        "description": str(api.get("description") or ""),
                        "landing_page_url": landing_page.rstrip("/"),
                    }
                )
            return sorted(datasets, key=lambda item: item["title"].casefold())
        links = root.get("links") if isinstance(root.get("links"), list) else []
        if any(isinstance(link, dict) and link.get("rel") in OGC_DATA_RELS for link in links):
            return [
                {
                    "id": "",
                    "title": str(root.get("title") or service.title),
                    "description": str(root.get("description") or ""),
                    "landing_page_url": service.base_url.rstrip("/"),
                }
            ]
        raise RemoteServiceError("This URL is neither an OGC API landing page nor a multi-API catalog.")

    return _cached(service, "ogc:datasets", OGC_CACHE_SECONDS, load, refresh)


def index_ogc_sources(service: ExternalService) -> tuple[list[dict], list[str]]:
    """Every collection of every dataset, with themes, for the source index.

    Returns ``(rows, problems)``: rows match :class:`ServiceSource` fields;
    a dataset whose collections cannot be read is skipped and reported in
    ``problems`` instead of failing the whole load. One request (plus one
    metadata request for themes) per dataset, so this runs in the background.
    """
    _require_type(service, ExternalService.ServiceType.OGC_API_FEATURES)
    root = fetch_json(service, service.base_url, {"f": "json"})
    tags_by_id = {}
    if isinstance(root, dict) and isinstance(root.get("apis"), list):
        for api in root["apis"]:
            if isinstance(api, dict) and isinstance(api.get("tags"), list):
                tags_by_id[str(api.get("id"))] = sorted({tag for tag in api["tags"] if tag in THEMES})

    def read(dataset: dict):
        try:
            collections = _read_collections(service, dataset["landing_page_url"])
        except RemoteServiceError as exc:
            return [], f"{dataset['title']}: {exc}"
        themes = tags_by_id.get(dataset["id"]) or _dataset_themes(service, dataset)
        rows = [
            {
                "dataset_id": dataset["id"],
                "dataset_title": dataset["title"],
                "source_id": collection["id"],
                "title": collection["title"],
                "description": collection["description"] or dataset["description"],
                "item_type": collection["item_type"] or "",
                "geojson": collection["geojson"],
                "count": collection["item_count"],
                "themes": themes,
            }
            for collection in collections
        ]
        return rows, None

    datasets = ogc_datasets(service, refresh=True)
    with ThreadPoolExecutor(max_workers=HARVEST_PARALLEL_REQUESTS) as pool:
        results = list(pool.map(read, datasets))
    rows = [row for dataset_rows, _ in results for row in dataset_rows]
    problems = [problem for _, problem in results if problem]
    return rows, problems


def _dataset_themes(service: ExternalService, dataset: dict) -> list[str]:
    try:
        landing = fetch_json(service, dataset["landing_page_url"], {"f": "json"})
        docs = landing.get("externalDocs") if isinstance(landing, dict) else None
        return metadata_themes(docs.get("url", "") if isinstance(docs, dict) else "")
    except RemoteServiceError:
        return []


def index_sta_sources(service: ExternalService) -> tuple[list[dict], list[str]]:
    """Every serviceName/layerName pair for the source index (no themes)."""
    rows = [
        {
            "dataset_id": layer["service_name"],
            "dataset_title": layer["service_name"],
            "source_id": layer["layer_name"],
            "title": layer["layer_name"],
            "description": "",
            "item_type": "datastreams",
            "geojson": True,
            "count": layer["datastream_count"],
            "themes": [],
        }
        for layer in sta_layers(service, refresh=True)
    ]
    return rows, []


def metadata_themes(record_url: str) -> list[str]:
    """EU theme codes of an ISO metadata record in a known catalog."""
    catalog = METADATA_CATALOGS.get(urlsplit(record_url).hostname or "")
    match = _DOC_UUID.search(record_url)
    if not catalog or not match:
        return []
    body = _fetch(
        urlsplit(catalog).hostname,
        catalog,
        {
            "service": "CSW",
            "version": "2.0.2",
            "request": "GetRecordById",
            "id": match.group(1),
            "outputSchema": "http://www.isotc211.org/2005/gmd",
            "elementSetName": "full",
        },
        "application/xml",
    ).decode("utf-8", "replace")
    return sorted({code for code in _KEYWORD.findall(body) if code in THEMES})


def _landing_page_url(service: ExternalService, dataset_id: str, refresh: bool) -> str:
    for dataset in ogc_datasets(service, refresh=refresh):
        if dataset["id"] == dataset_id:
            return dataset["landing_page_url"]
    raise RemoteServiceError(f"Dataset '{dataset_id}' was not found at the service.", status=404)


def _has_geojson_items(links) -> bool:
    for link in links if isinstance(links, list) else []:
        if isinstance(link, dict) and link.get("rel") == "items":
            if link.get("type") in GEOJSON_TYPES or link.get("type") is None:
                return True
    return False


def _read_collections(service: ExternalService, landing_page: str) -> list[dict]:
    data = fetch_json(service, f"{landing_page}/collections", {"f": "json"})
    collections = data.get("collections") if isinstance(data, dict) else None
    if not isinstance(collections, list):
        raise RemoteServiceError("Unexpected collections response from the service.")
    result = []
    for collection in collections:
        if not isinstance(collection, dict) or not collection.get("id"):
            continue
        item_count = collection.get("itemCount")
        result.append(
            {
                "id": str(collection["id"]),
                "title": str(collection.get("title") or collection["id"]),
                "description": str(collection.get("description") or ""),
                "item_type": collection.get("itemType"),
                "item_count": item_count if isinstance(item_count, int) else None,
                "geojson": _has_geojson_items(collection.get("links")),
            }
        )
    return result


def ogc_collections(service: ExternalService, dataset_id: str, refresh: bool = False) -> list[dict]:
    """Collections of one dataset. Only ``geojson: True`` ones can be shown
    on the map (some offer only CityJSON/glTF items)."""
    _require_type(service, ExternalService.ServiceType.OGC_API_FEATURES)

    def load():
        return _read_collections(service, _landing_page_url(service, dataset_id, refresh))

    return _cached(service, f"ogc:collections:{dataset_id}", OGC_CACHE_SECONDS, load, refresh)


# ---------------------------------------------------------------------------
# SensorThings
# ---------------------------------------------------------------------------


def sta_base_url(service: ExternalService, refresh: bool = False) -> str:
    """Versioned SensorThings root (``…/v1.1`` or ``…/v1.0``)."""
    _require_type(service, ExternalService.ServiceType.SENSORTHINGS)
    root = service.base_url.rstrip("/")
    last_segment = root.rsplit("/", 1)[-1]
    if last_segment in ("v1.0", "v1.1"):
        return root

    def load():
        for version in ("v1.1", "v1.0"):
            candidate = f"{root}/{version}"
            try:
                data = fetch_json(service, candidate)
            except RemoteServiceError:
                continue
            if isinstance(data, dict) and isinstance(data.get("value"), list):
                return candidate
        raise RemoteServiceError("No SensorThings v1.x endpoint was found at this URL.")

    return _cached(service, "sta:base", OGC_CACHE_SECONDS, load, refresh)


def sta_layers(service: ExternalService, refresh: bool = False) -> list[dict]:
    """Distinct ``serviceName``/``layerName`` pairs with datastream counts.

    SensorThings has no group-by, so every datastream's properties are read
    (Hamburg: ~23k datastreams in ~24 pages, fetched in parallel by offset)
    and the result is cached for a day.
    """
    _require_type(service, ExternalService.ServiceType.SENSORTHINGS)

    def load():
        base = sta_base_url(service, refresh=refresh)
        head = fetch_json(service, f"{base}/Datastreams", {"$count": "true", "$top": "0"})
        total = head.get("@iot.count") if isinstance(head, dict) else None
        if not isinstance(total, int):
            raise RemoteServiceError("The service did not report a datastream count.")
        pages = math.ceil(total / STA_PAGE_SIZE)
        if pages > STA_MAX_PAGES:
            raise RemoteServiceError(
                f"The service has {total} datastreams; listing more than "
                f"{STA_MAX_PAGES * STA_PAGE_SIZE} is not supported."
            )

        def read_page(index: int):
            data = fetch_json(
                service,
                f"{base}/Datastreams",
                {
                    "$select": "properties",
                    "$orderby": "@iot.id",
                    "$top": str(STA_PAGE_SIZE),
                    "$skip": str(index * STA_PAGE_SIZE),
                },
            )
            return data.get("value", []) if isinstance(data, dict) else []

        counts: dict[tuple[str, str], int] = {}
        with ThreadPoolExecutor(max_workers=STA_PARALLEL_REQUESTS) as pool:
            for page in pool.map(read_page, range(pages)):
                for datastream in page:
                    properties = datastream.get("properties") if isinstance(datastream, dict) else None
                    if not isinstance(properties, dict):
                        continue
                    service_name = properties.get("serviceName")
                    layer_name = properties.get("layerName")
                    if isinstance(service_name, str) and service_name and isinstance(layer_name, str) and layer_name:
                        counts[(service_name, layer_name)] = counts.get((service_name, layer_name), 0) + 1

        return [
            {"service_name": service_name, "layer_name": layer_name, "datastream_count": count}
            for (service_name, layer_name), count in sorted(counts.items(), key=lambda entry: entry[0])
        ]

    return _cached(service, "sta:layers", STA_LAYERS_CACHE_SECONDS, load, refresh)


def _odata_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sta_layer_exists(service: ExternalService, service_name: str, layer_name: str) -> bool:
    """Cheap existence check for one pair (no ``$count``: it takes ~28 s on
    Hamburg for property filters, versus ~0.25 s without)."""
    base = sta_base_url(service)
    data = fetch_json(
        service,
        f"{base}/Datastreams",
        {
            "$filter": (
                f"properties/serviceName eq {_odata_string(service_name)} "
                f"and properties/layerName eq {_odata_string(layer_name)}"
            ),
            "$top": "1",
            "$select": "@iot.id",
        },
    )
    return bool(isinstance(data, dict) and data.get("value"))
