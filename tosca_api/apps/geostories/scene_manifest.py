"""MapLibre render manifest for GeoStory scenes.

A story ships one shared ``map`` block (sources, styles, sprites) and each
scene its own ordered ``render_layers`` and ``legend``. Sources are keyed by
layer (and by style for WMS images, whose tile URL embeds the style), so a
reader's map loads every source once and switching scenes only swaps the
render layers.
"""

from __future__ import annotations

from tosca_api.apps.catalog_api.services.v1.render_manifest import (
    build_legend_graphic_url,
    build_render_layers,
    build_source,
    build_sprite_entry,
    build_style_entry,
    is_raster,
    uses_vector_tiles,
)
from tosca_api.apps.geodata_providers.models import Layer

from .models import GeoStoryScene, GeoStorySceneLayer

HIGHLIGHT_COLOR = "#f59e0b"

OPACITY_PROPERTIES = {
    "fill": ("fill-opacity",),
    "line": ("line-opacity",),
    "circle": ("circle-opacity", "circle-stroke-opacity"),
    "symbol": ("icon-opacity", "text-opacity"),
    "fill-extrusion": ("fill-extrusion-opacity",),
    "heatmap": ("heatmap-opacity",),
    "raster": ("raster-opacity",),
}

INTERPOLATE_OPERATORS = {"interpolate", "interpolate-hcl", "interpolate-lab"}
LEGACY_COMPARISON_OPERATORS = {"==", "!=", "<", "<=", ">", ">=", "in", "!in", "has", "!has"}

POLYGON_TYPES = {Layer.GeometryType.POLYGON, Layer.GeometryType.MULTI_POLYGON}
LINE_TYPES = {Layer.GeometryType.LINE_STRING, Layer.GeometryType.MULTI_LINE_STRING}
POINT_TYPES = {Layer.GeometryType.POINT, Layer.GeometryType.MULTI_POINT}


# --- Visibility --------------------------------------------------------------


def is_publicly_visible(layer: Layer) -> bool:
    """Layers that lost public/published status after authoring are skipped on read."""
    return layer.is_public and layer.publishing_state == Layer.PublishingState.PUBLISHED


def visible_scene_layers(scene: GeoStoryScene) -> list[GeoStorySceneLayer]:
    return [item for item in scene.scene_layers.all() if is_publicly_visible(item.layer)]


def renderable_scene_layers(scene: GeoStoryScene) -> list[GeoStorySceneLayer]:
    # Legacy v1 rows may have been migrated without a style; nothing to draw.
    return [item for item in visible_scene_layers(scene) if item.style_assignment_id]


def source_key(scene_layer: GeoStorySceneLayer) -> str:
    if uses_vector_tiles(scene_layer.layer, scene_layer.style_assignment):
        return f"vector-{scene_layer.layer_id}"
    return f"wms-{scene_layer.layer_id}-{scene_layer.style_assignment.style_id}"


# --- Story-level map block ------------------------------------------------------


def build_story_map(*, request, scenes) -> dict:
    return build_map(
        request=request,
        scene_layers=[item for scene in scenes for item in renderable_scene_layers(scene)],
    )


def build_map(*, request, scene_layers) -> dict:
    """Sources, styles and sprites for already-filtered, renderable scene layers."""
    sources: dict[str, dict] = {}
    styles: dict[str, dict] = {}
    sprites: dict[str, dict] = {}
    for scene_layer in scene_layers:
        key = source_key(scene_layer)
        if key not in sources:
            sources[key] = build_source(
                layer=scene_layer.layer, style_assignment=scene_layer.style_assignment
            )
        style = scene_layer.style_assignment.style
        provider_id = scene_layer.layer.workspace.geodata_engine_id
        if str(style.id) not in styles:
            styles[str(style.id)] = build_style_entry(
                request=request, style=style, provider_id=provider_id
            )
        sprite_asset = style.sprite_asset
        if sprite_asset is not None and str(sprite_asset.id) not in sprites:
            sprites[str(sprite_asset.id)] = build_sprite_entry(
                request=request, sprite_asset=sprite_asset, provider_id=provider_id
            )
    return {"sources": sources, "styles": styles, "sprites": sprites}


# --- Scene render layers --------------------------------------------------------


def build_scene_render_layers(scene: GeoStoryScene) -> list[dict]:
    """Ordered MapLibre layers for one scene, bottom to top."""
    return build_render_layers_for(renderable_scene_layers(scene))


def build_render_layers_for(scene_layers) -> list[dict]:
    render_layers: list[dict] = []
    for scene_layer in scene_layers:
        prefix = f"scene-layer-{scene_layer.id}"
        key = source_key(scene_layer)
        passes = build_render_layers(
            layer=scene_layer.layer,
            style_assignment=scene_layer.style_assignment,
            style_layer_ids=scene_layer.effective_style_layer_ids,
            source_key=key,
            raster_id=prefix,
            vector_id=lambda style_layer_id, prefix=prefix: f"{prefix}/{style_layer_id}",
            metadata={
                # tosca:member-id lets clients treat a scene like a layer-group
                # manifest (one member per scene layer).
                "tosca:member-id": str(scene_layer.id),
                "tosca:scene-layer-id": str(scene_layer.id),
                "tosca:layer-id": str(scene_layer.layer_id),
                "tosca:style-id": str(scene_layer.style_assignment.style_id),
            },
        )
        if scene_layer.opacity < 1:
            for render_layer in passes:
                _apply_opacity(render_layer, scene_layer.opacity)

        selection = _feature_selection(scene_layer)
        if selection is not None and scene_layer.feature_mode == GeoStorySceneLayer.FeatureMode.ONLY:
            for render_layer in passes:
                _restrict_to_features(render_layer, *selection)
        render_layers.extend(passes)

        if selection is not None and scene_layer.feature_mode == GeoStorySceneLayer.FeatureMode.HIGHLIGHT:
            render_layers.extend(_highlight_layers(scene_layer, key, prefix, *selection))
    return render_layers


def scene_bounds(scene_layers) -> list[float] | None:
    """Union of the layers' WGS84 extents, for "fit to layers"."""
    boxes = [item.layer.bounds for item in scene_layers if item.layer.bounds]
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def build_scene_legend(scene: GeoStoryScene) -> list[dict]:
    """Legend entries, top-most layer first."""
    return build_legend_for(renderable_scene_layers(scene))


def build_legend_for(scene_layers) -> list[dict]:
    return [
        {
            "scene_layer_id": str(scene_layer.id),
            "layer_id": str(scene_layer.layer_id),
            "title": scene_layer.layer.title or scene_layer.layer.name,
            "data_type": "RASTER" if is_raster(scene_layer.layer) else "VECTOR",
            "rendering": (
                "vector-tiles"
                if uses_vector_tiles(scene_layer.layer, scene_layer.style_assignment)
                else "wms"
            ),
            "style": {
                "id": str(scene_layer.style_assignment.style_id),
                "title": scene_layer.style_assignment.style.title
                or scene_layer.style_assignment.style.name,
            },
            "graphic_url": build_legend_graphic_url(
                layer=scene_layer.layer, style_assignment=scene_layer.style_assignment
            ),
        }
        for scene_layer in reversed(list(scene_layers))
    ]


# --- Opacity ----------------------------------------------------------------------


def _apply_opacity(render_layer: dict, factor: float) -> None:
    properties = OPACITY_PROPERTIES.get(render_layer.get("type"), ())
    if not properties:
        return
    paint = dict(render_layer.get("paint") or {})
    for name in properties:
        paint[name] = _scale(paint.get(name), factor)
    render_layer["paint"] = paint


def _scale(value, factor: float):
    """Multiply a paint opacity by ``factor`` without breaking zoom expressions.

    MapLibre only allows ``["zoom"]`` as the input of a top-level
    ``interpolate``/``step``, so those are scaled per output stop instead of
    being wrapped in ``["*", …]``.
    """
    if value is None:
        return factor
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value * factor
    if isinstance(value, list) and value:
        head = value[0]
        if head in INTERPOLATE_OPERATORS and len(value) >= 5:
            return value[:4] + [
                _scale(item, factor) if index % 2 == 1 else item
                for index, item in enumerate(value[4:], start=1)
            ]
        if head == "step" and len(value) >= 3:
            return value[:2] + [
                _scale(item, factor) if index % 2 == 0 else item
                for index, item in enumerate(value[2:])
            ]
        return ["*", value, factor]
    if isinstance(value, dict) and isinstance(value.get("stops"), list):
        scaled = dict(value)
        scaled["stops"] = [
            [stop[0], _scale(stop[1], factor)]
            if isinstance(stop, list) and len(stop) == 2
            else stop
            for stop in value["stops"]
        ]
        return scaled
    return value


# --- Feature selection ------------------------------------------------------


def _feature_selection(scene_layer: GeoStorySceneLayer) -> tuple[str, list] | None:
    if (
        scene_layer.feature_mode == GeoStorySceneLayer.FeatureMode.ALL
        or not uses_vector_tiles(scene_layer.layer, scene_layer.style_assignment)
        or not scene_layer.feature_id_attribute
        or not scene_layer.feature_ids
    ):
        return None
    return scene_layer.feature_id_attribute, list(scene_layer.feature_ids)


def _expression_filter(attribute: str, ids: list) -> list:
    return ["in", ["get", attribute], ["literal", ids]]


def _is_legacy_filter(value) -> bool:
    """Legacy filters (``["==", "field", 1]``) cannot be mixed with expressions."""
    if not isinstance(value, list) or not value:
        return False
    head = value[0]
    if head in {"all", "any", "none"}:
        return any(_is_legacy_filter(child) for child in value[1:])
    return head in LEGACY_COMPARISON_OPERATORS and len(value) > 1 and isinstance(value[1], str)


def _restrict_to_features(render_layer: dict, attribute: str, ids: list) -> None:
    existing = render_layer.get("filter")
    if _is_legacy_filter(existing):
        selection = ["in", attribute, *ids]
    else:
        selection = _expression_filter(attribute, ids)
    render_layer["filter"] = ["all", existing, selection] if existing else selection


def _highlight_layers(scene_layer, key: str, prefix: str, attribute: str, ids: list) -> list[dict]:
    geometry_type = scene_layer.layer.geometry_type
    mixed = geometry_type not in POLYGON_TYPES | LINE_TYPES | POINT_TYPES
    selection = _expression_filter(attribute, ids)

    def layer(suffix, layer_type, paint, geometry):
        filter_ = selection
        if mixed:
            # Newer MapLibre reports Multi* types from ["geometry-type"].
            geometry_types = ["literal", [geometry, f"Multi{geometry}"]]
            filter_ = ["all", ["in", ["geometry-type"], geometry_types], selection]
        return {
            "id": f"{prefix}/highlight-{suffix}",
            "type": layer_type,
            "source": key,
            "source-layer": scene_layer.layer.name,
            "filter": filter_,
            "paint": paint,
            "metadata": {
                "tosca:member-id": str(scene_layer.id),
                "tosca:scene-layer-id": str(scene_layer.id),
                "tosca:layer-id": str(scene_layer.layer_id),
                "tosca:role": "highlight",
            },
        }

    layers = []
    if mixed or geometry_type in POLYGON_TYPES:
        layers.append(
            layer("fill", "fill", {"fill-color": HIGHLIGHT_COLOR, "fill-opacity": 0.25}, "Polygon")
        )
        layers.append(
            layer(
                "outline",
                "line",
                {"line-color": HIGHLIGHT_COLOR, "line-width": 3},
                "Polygon",
            )
        )
    if mixed or geometry_type in LINE_TYPES:
        layers.append(
            layer(
                "line",
                "line",
                {"line-color": HIGHLIGHT_COLOR, "line-width": 5, "line-opacity": 0.9},
                "LineString",
            )
        )
    if mixed or geometry_type in POINT_TYPES:
        layers.append(
            layer(
                "point",
                "circle",
                {
                    "circle-radius": 8,
                    "circle-color": HIGHLIGHT_COLOR,
                    "circle-opacity": 0.35,
                    "circle-stroke-color": HIGHLIGHT_COLOR,
                    "circle-stroke-width": 2,
                },
                "Point",
            )
        )
    return layers
