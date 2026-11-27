"""Catalog sources: further data catalogs the assistant may search.

Each source is one JSON file in ``catalog_sources/`` with STAC-style fields
(``id``, ``title``, ``description``, ``url``, ``protocol``, ``extent``, ...)
and an optional ``<id>.md`` with short notes for the assistant, written like
a Portolan ``AGENTS.md``: only what the source does not say about itself.
Rules: ``docs/development/mcp-server.md`` ("Catalog sources").

``url`` may name an environment variable (``"${MCP_SOURCE_NGCN_URL}"``). A
source whose variable is unset is left out, so a deployment does not list
its own catalog twice.

Remote reads reuse the guarded HTTP client of the external catalog (one
allowed host per source, public addresses only, bounded time and size).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from django.core.cache import cache

from tosca_api.apps.external_catalog import remote
from tosca_api.apps.external_catalog.models import ExternalService

SOURCES_DIR = Path(__file__).with_name("catalog_sources")
CACHE_SECONDS = 60 * 60
SUMMARY_LENGTH = 300

TOSCA_CATALOG = "tosca-catalog"
OGC_API_FEATURES = "ogc-api-features"
SENSORTHINGS = "sensorthings"
PROTOCOLS = (TOSCA_CATALOG, OGC_API_FEATURES, SENSORTHINGS)

REQUIRED = ("id", "title", "description", "url", "protocol")
ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


class SourceError(Exception):
    """A source could not be read; the message is safe to show."""


@dataclass(frozen=True)
class CatalogSource:
    id: str
    title: str
    description: str
    url: str
    protocol: str
    extent: list[float] | None = None
    license: str | None = None
    keywords: list[str] = field(default_factory=list)
    providers: list[dict[str, Any]] = field(default_factory=list)
    priority: int = 100
    agents: str | None = None
    notes: str = ""

    def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "url": self.url,
            "protocol": self.protocol,
            "extent": {"bbox": self.extent} if self.extent else None,
            "license": self.license,
            "keywords": self.keywords,
            "providers": self.providers,
            "priority": self.priority,
        }
        if self.agents:
            result["agents"] = self.agents
        if self.notes:
            result["notes"] = self.notes
        return result


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _expand(value: str, environ) -> str | None:
    """Replace ``${NAME}``; ``None`` when a named variable is unset or empty."""
    missing = False

    def replace(match: re.Match) -> str:
        nonlocal missing
        found = (environ.get(match.group(1)) or "").strip()
        missing = missing or not found
        return found

    expanded = ENV_PATTERN.sub(replace, value).strip()
    return None if missing or not expanded else expanded.rstrip("/")


def _parse(path: Path, environ) -> CatalogSource | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{path.name}: not valid JSON ({exc})") from exc
    missing = [name for name in REQUIRED if not data.get(name)]
    if missing:
        raise ValueError(f"{path.name}: missing {', '.join(missing)}")
    if data["id"] != path.stem or not ID_PATTERN.match(data["id"]):
        raise ValueError(f"{path.name}: id must be lowercase-with-hyphens and equal the file name")
    if data["protocol"] not in PROTOCOLS:
        raise ValueError(f"{path.name}: protocol must be one of {', '.join(PROTOCOLS)}")
    extent = (data.get("extent") or {}).get("bbox")
    if extent is not None and (len(extent) != 4 or not all(isinstance(v, int | float) for v in extent)):
        raise ValueError(f"{path.name}: extent.bbox must be [west, south, east, north]")
    url = _expand(str(data["url"]), environ)
    if url is None:
        return None
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"{path.name}: url must be http(s)")
    notes_path = path.with_suffix(".md")
    return CatalogSource(
        id=data["id"],
        title=data["title"],
        description=data["description"],
        url=url,
        protocol=data["protocol"],
        extent=list(extent) if extent else None,
        license=data.get("license"),
        keywords=list(data.get("keywords") or []),
        providers=list(data.get("providers") or []),
        priority=int(data.get("priority", 100)),
        agents=data.get("agents"),
        notes=notes_path.read_text(encoding="utf-8").strip() if notes_path.exists() else "",
    )


def load_sources(directory: Path = SOURCES_DIR, environ=None) -> list[CatalogSource]:
    """Configured sources, in search order (``priority``, then ``id``)."""
    environ = os.environ if environ is None else environ
    sources = [source for path in sorted(directory.glob("*.json")) if (source := _parse(path, environ))]
    return sorted(sources, key=lambda source: (source.priority, source.id))


def get_source(source_id: str, sources: list[CatalogSource] | None = None) -> CatalogSource:
    for source in load_sources() if sources is None else sources:
        if source.id == source_id:
            return source
    raise SourceError(f"Unknown source '{source_id}'. Call list_sources for the ids.")


# ---------------------------------------------------------------------------
# Remote reads
# ---------------------------------------------------------------------------


def _service(source: CatalogSource) -> ExternalService:
    """Unsaved stand-in so the external catalog's guarded client can be reused."""
    service_type = {
        OGC_API_FEATURES: ExternalService.ServiceType.OGC_API_FEATURES,
        SENSORTHINGS: ExternalService.ServiceType.SENSORTHINGS,
    }.get(source.protocol, "")
    return ExternalService(slug=source.id, title=source.title, base_url=source.url, service_type=service_type)


def _cached(source: CatalogSource, name: str, loader):
    digest = hashlib.sha256(f"{source.url}|{name}".encode()).hexdigest()[:24]
    key = f"mcp_sources:{source.id}:{digest}"
    value = cache.get(key)
    if value is None:
        value = loader()
        cache.set(key, value, CACHE_SECONDS)
    return value


def _shorten(text: str | None) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= SUMMARY_LENGTH else f"{text[:SUMMARY_LENGTH].rstrip()}…"


def _matches(words: list[str], text: str) -> bool:
    text = re.sub(r"[_\-]+", " ", text).lower()
    return all(word in text for word in words)


def _read(source: CatalogSource, function, *args):
    try:
        return function(*args)
    except remote.RemoteServiceError as exc:
        raise SourceError(f"{source.title}: {exc}") from exc


# TOSCA catalog API (/api/v1/catalog of another TOSCA instance) ---------------


def _tosca_layers(source: CatalogSource) -> list[dict[str, str]]:
    service = _service(source)

    def load():
        layers = []
        providers = remote.fetch_json(service, f"{source.url}/providers")
        for provider in providers if isinstance(providers, list) else []:
            provider_id = str(provider.get("id", "")) if isinstance(provider, dict) else ""
            if not provider_id:
                continue
            data = remote.fetch_json(service, f"{source.url}/providers/{provider_id}/layers")
            entries = ((data or {}).get("layers") or {}).get("layer") or []
            for entry in entries:
                # .../workspaces/<workspace>/layers/<name>
                parts = urlsplit(str(entry.get("href", ""))).path.split("/")
                if "workspaces" not in parts:
                    continue
                workspace = unquote(parts[parts.index("workspaces") + 1])
                layers.append({"provider_id": provider_id, "workspace": workspace, "name": str(entry["name"])})
        return layers

    return _cached(source, "tosca:layers", load)


def _tosca_search(source: CatalogSource, words: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "source": source.id,
            "item_id": f"{layer['provider_id']}/{layer['workspace']}/{layer['name']}",
            "title": layer["name"],
            "workspace": layer["workspace"],
            "show_on_map": {"tool": "add_catalog_layer", "arguments": {
                "providerId": layer["provider_id"], "workspace": layer["workspace"], "layerName": layer["name"],
            }},
        }
        for layer in _read(source, _tosca_layers, source)
        if _matches(words, f"{layer['workspace']} {layer['name']}")
    ]


def _tosca_describe(source: CatalogSource, item_id: str) -> dict[str, Any]:
    try:
        provider_id, workspace, name = item_id.split("/", 2)
    except ValueError as exc:
        raise SourceError("item_id must be '<provider_id>/<workspace>/<name>' as search_sources returns it.") from exc
    url = f"{source.url}/providers/{provider_id}/workspaces/{workspace}/resources/{name}"
    data = _read(source, remote.fetch_json, _service(source), url)
    resource = (data or {}).get("featureType") or (data or {}).get("coverage") or {}
    bbox = resource.get("latLonBoundingBox") or {}
    attributes = ((resource.get("attributes") or {}).get("attribute")) or []
    return {
        "source": source.id,
        "item_id": item_id,
        "title": resource.get("title") or name,
        "description": _shorten(resource.get("abstract")),
        "workspace": workspace,
        "kind": "raster" if "coverage" in (data or {}) else "vector",
        "crs": resource.get("srs"),
        "extent_wgs84": [bbox.get(k) for k in ("minx", "miny", "maxx", "maxy")] if bbox else None,
        "attributes": [
            {"name": item.get("name"), "type": str(item.get("binding", "")).rsplit(".", 1)[-1]}
            for item in attributes if isinstance(item, dict)
        ],
        "show_on_map": {"tool": "add_catalog_layer", "arguments": {
            "providerId": provider_id, "workspace": workspace, "layerName": name,
        }},
    }


# OGC API Features (landing page or multi-API catalog) ------------------------


def _ogc_search(source: CatalogSource, words: list[str]) -> list[dict[str, Any]]:
    datasets = _read(source, remote.ogc_datasets, _service(source))
    return [
        {
            "source": source.id,
            "item_id": dataset["id"],
            "title": dataset["title"],
            "summary": _shorten(dataset["description"]),
            "url": dataset["landing_page_url"],
        }
        for dataset in datasets
        if _matches(words, f"{dataset['id']} {dataset['title']} {dataset['description']}")
    ]


def _ogc_describe(source: CatalogSource, item_id: str) -> dict[str, Any]:
    service = _service(source)
    datasets = {dataset["id"]: dataset for dataset in _read(source, remote.ogc_datasets, service)}
    if item_id not in datasets:
        raise SourceError(f"{source.title}: no dataset '{item_id}'. Use an item_id from search_sources.")
    dataset = datasets[item_id]
    collections = _read(source, remote.ogc_collections, service, item_id)
    return {
        "source": source.id,
        "item_id": item_id,
        "title": dataset["title"],
        "description": " ".join(dataset["description"].split()),
        "url": dataset["landing_page_url"],
        "collections": [
            {
                "id": collection["id"],
                "title": collection["title"],
                "summary": _shorten(collection["description"]),
                "item_count": collection["item_count"],
                "geojson": collection["geojson"],
                "items_url": f"{dataset['landing_page_url']}/collections/{collection['id']}/items?f=json",
                # The browser shows GeoJSON collections only (OgcApiCollectionListingItem.vue).
                **({"show_on_map": {"tool": "add_ogc_collection", "arguments": {
                    "serviceId": source.id, "datasetId": item_id, "collectionId": collection["id"],
                }}} if collection["geojson"] else {}),
            }
            for collection in collections
        ],
    }


# SensorThings API (the external catalog's layer list: serviceName / layerName pairs) ---


def _sta_item(source: CatalogSource, layer: dict[str, Any]) -> dict[str, Any]:
    service_name, layer_name = layer["service_name"], layer["layer_name"]
    return {
        "source": source.id,
        "item_id": f"{service_name}/{layer_name}",
        "title": layer_name,
        "summary": f"{service_name}: {layer['datastream_count']} sensors",
        # serviceId is the id the browser knows the service by (ExternalService.slug).
        "show_on_map": {"tool": "add_sensor_things_layer", "arguments": {
            "serviceId": source.id, "serviceName": service_name, "layerName": layer_name,
        }},
    }


def _sta_search(source: CatalogSource, words: list[str]) -> list[dict[str, Any]]:
    layers = _read(source, remote.sta_layers, _service(source))
    return [
        _sta_item(source, layer)
        for layer in layers
        if _matches(words, f"{layer['service_name']} {layer['layer_name']}")
    ]


def _sta_describe(source: CatalogSource, item_id: str) -> dict[str, Any]:
    service_name, _, layer_name = item_id.partition("/")
    layers = _read(source, remote.sta_layers, _service(source))
    layer = next(
        (item for item in layers if item["service_name"] == service_name and item["layer_name"] == layer_name), None
    )
    if layer is None:
        raise SourceError(f"{source.title}: no layer '{item_id}'. Use an item_id from search_sources.")
    return {
        **_sta_item(source, layer),
        "service_name": service_name,
        "layer_name": layer_name,
        "sensors": layer["datastream_count"],
        "url": source.url,
    }


_SEARCH = {TOSCA_CATALOG: _tosca_search, OGC_API_FEATURES: _ogc_search, SENSORTHINGS: _sta_search}
_DESCRIBE = {TOSCA_CATALOG: _tosca_describe, OGC_API_FEATURES: _ogc_describe, SENSORTHINGS: _sta_describe}


def search(source: CatalogSource, query: str) -> list[dict[str, Any]]:
    return _SEARCH[source.protocol](source, query.lower().split())


def describe(source: CatalogSource, item_id: str) -> dict[str, Any]:
    return _DESCRIBE[source.protocol](source, item_id)
