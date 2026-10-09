"""MapLibre render building blocks shared by layer groups and GeoStory scenes.

Both consumers render "a layer drawn with a pinned style assignment", so the
source URLs, render-layer expansion, style and sprite descriptors live here
and the callers only decide ids, ordering and metadata.
"""

from copy import deepcopy
from urllib.parse import urlencode

from django.urls import reverse

from tosca_api.apps.geodata_providers.models import Store, Style


def is_raster(layer) -> bool:
    return layer.store.store_type == Store.StoreType.GEOTIFF


def uses_vector_tiles(layer, style_assignment) -> bool:
    """MBStyle-styled vector data is drawn client-side from WMTS vector tiles.

    Everything else (GeoTIFF coverages, and vector data with an SLD style) is
    rendered server-side by GeoServer and served as WMS images. Layer groups
    only allow the first combination per data type, so their output is the
    same either way.
    """
    return not is_raster(layer) and style_assignment.style.format == Style.StyleFormat.MBSTYLE


def _absolute(request, path: str) -> str:
    return request.build_absolute_uri(path) if request is not None else path


def build_source(*, layer, style_assignment) -> dict:
    """Vector tiles come from GeoWebCache WMTS; everything else from tiled WMS."""
    base_url = layer.workspace.geodata_engine.public_url.rstrip("/")
    qualified_name = f"{layer.workspace.name}:{layer.name}"
    if uses_vector_tiles(layer, style_assignment):
        params = urlencode(
            {
                "REQUEST": "GetTile",
                "SERVICE": "WMTS",
                "VERSION": "1.0.0",
                "LAYER": qualified_name,
                "STYLE": "",
                "TILEMATRIX": "EPSG:900913:{z}",
                "TILEMATRIXSET": "EPSG:900913",
                "TILECOL": "{x}",
                "TILEROW": "{y}",
                "FORMAT": "application/vnd.mapbox-vector-tile",
            }
        )
        for token in ("z", "x", "y"):
            params = params.replace(f"%7B{token}%7D", f"{{{token}}}")
        source = {
            "type": "vector",
            "tiles": [f"{base_url}/gwc/service/wmts?{params}"],
        }
        # MapLibre requests no tiles outside `bounds`; without it, GeoWebCache
        # answers every tile beyond the layer's extent with HTTP 400.
        bounds = source_bounds(layer)
        if bounds is not None:
            source["bounds"] = bounds
        return source

    params = urlencode(
        {
            "REQUEST": "GetMap",
            "SERVICE": "WMS",
            "VERSION": "1.3.0",
            "LAYERS": qualified_name,
            "STYLES": style_assignment.style.name,
            "CRS": "EPSG:3857",
            "WIDTH": "256",
            "HEIGHT": "256",
            "transparent": "true",
            "format": "image/png",
            "TILED": "true",
        }
    )
    return {
        "type": "raster",
        "tiles": [f"{base_url}/wms?{params}&BBOX={{bbox-epsg-3857}}"],
        "tileSize": 256,
    }


def source_bounds(layer) -> list[float] | None:
    """The layer's WGS84 extent as MapLibre source `bounds`, if known and valid."""
    bounds = getattr(layer, "bounds", None)
    if (
        not isinstance(bounds, (list, tuple))
        or len(bounds) != 4
        or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in bounds)
    ):
        return None
    west, south, east, north = (float(value) for value in bounds)
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        return None
    return [west, south, east, north]


def build_legend_graphic_url(*, layer, style_assignment) -> str:
    base_url = layer.workspace.geodata_engine.public_url.rstrip("/")
    params = urlencode(
        {
            "SERVICE": "WMS",
            "REQUEST": "GetLegendGraphic",
            "VERSION": "1.0.0",
            "FORMAT": "image/png",
            "LAYER": f"{layer.workspace.name}:{layer.name}",
            "STYLE": style_assignment.style.name,
        }
    )
    return f"{base_url}/wms?{params}"


def build_render_layers(
    *,
    layer,
    style_assignment,
    style_layer_ids,
    source_key: str,
    raster_id: str,
    metadata: dict,
    vector_id=None,
) -> list[dict]:
    """Expand one layer + style into MapLibre layers.

    WMS-rendered layers become a single ``raster`` layer named ``raster_id``.
    Vector-tile layers become one copy per selected MBStyle rule, bound to
    ``source_key`` and the layer's ``source-layer``; ``vector_id`` may rename
    those rules (scenes need ids that stay unique across every scene of a story).
    """
    if not uses_vector_tiles(layer, style_assignment):
        return [
            {
                "id": raster_id,
                "type": "raster",
                "source": source_key,
                "metadata": dict(metadata),
            }
        ]

    render_layers = []
    for style_layer in style_assignment.selected_mbstyle_layers(style_layer_ids):
        render_layer = deepcopy(style_layer)
        if vector_id is not None:
            render_layer["id"] = vector_id(style_layer.get("id"))
        render_layer["source"] = source_key
        render_layer["source-layer"] = layer.name
        existing = render_layer.get("metadata")
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged.update(metadata)
        render_layer["metadata"] = merged
        render_layers.append(render_layer)
    return render_layers


def build_style_entry(*, request, style, provider_id) -> dict:
    style_id = str(style.id)
    return {
        "id": style_id,
        "name": style.name,
        "title": style.title or style.name,
        "format": style.format,
        "content_hash": style.content_hash,
        "sprite_id": None if style.sprite_asset_id is None else str(style.sprite_asset_id),
        "href": _absolute(
            request,
            reverse(
                "catalog-v1-provider-style-detail",
                kwargs={"provider_id": provider_id, "style_ref": style.id},
            ),
        ),
    }


def build_sprite_entry(*, request, sprite_asset, provider_id) -> dict:
    sprite_id = str(sprite_asset.id)
    return {
        "id": sprite_id,
        "url": _absolute(
            request,
            reverse(
                "catalog-v1-provider-sprite-versioned-stem",
                kwargs={
                    "provider_id": provider_id,
                    "sprite_id": sprite_id,
                    "content_hash": sprite_asset.content_hash,
                },
            ),
        ),
        "content_hash": sprite_asset.content_hash,
    }
