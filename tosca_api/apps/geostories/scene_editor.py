"""Admin scene editor: live preview of unsaved scene layers."""

from __future__ import annotations

import json
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import JsonResponse

from tosca_api.apps.geodata_providers.models import Layer, LayerStyleAssignment

from .models import GeoStorySceneLayer
from .scene_manifest import build_legend_for, build_map, build_render_layers_for, scene_bounds

MAX_PREVIEW_LAYERS = 50

# OpenStreetMap raster tiles are fine for low-volume admin previews; deployments
# can point GEOSTORY_SCENE_EDITOR_BASEMAP at their own MapLibre style instead.
DEFAULT_BASEMAP = {
    "version": 8,
    "glyphs": "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
    "sources": {
        "osm": {
            "type": "raster",
            "tiles": ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            "tileSize": 256,
            "maxzoom": 19,
            "attribution": "© OpenStreetMap contributors",
        }
    },
    "layers": [{"id": "basemap-osm", "type": "raster", "source": "osm"}],
}

PREVIEW_FIELDS = (
    "render_layer_ids",
    "display_order",
    "opacity",
    "feature_mode",
    "feature_id_attribute",
    "feature_ids",
)


def basemap_style():
    return getattr(settings, "GEOSTORY_SCENE_EDITOR_BASEMAP", DEFAULT_BASEMAP)


def _uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def build_preview(request, rows) -> dict:
    """Validate unsaved scene layer rows and render the ones that pass.

    ``rows`` mirror the scene layer inline: ``layer`` and ``style_assignment``
    ids plus the editable fields. Rows without a layer are ignored (empty
    inline forms); invalid rows are reported under ``errors`` by row index.
    """
    layer_ids = {_uuid(row.get("layer")) for row in rows} - {None}
    assignment_ids = {_uuid(row.get("style_assignment")) for row in rows} - {None}
    layers = Layer.objects.select_related("store", "workspace__geodata_engine").in_bulk(layer_ids)
    assignments = LayerStyleAssignment.objects.select_related(
        "style__sprite_asset", "style__workspace"
    ).in_bulk(assignment_ids)

    scene_layers = []
    errors: dict[str, dict] = {}
    for index, row in enumerate(rows):
        if not row.get("layer"):
            continue
        layer = layers.get(_uuid(row.get("layer")))
        if layer is None:
            errors[str(index)] = {"layer": ["Unknown layer."]}
            continue
        scene_layer = GeoStorySceneLayer(
            layer=layer,
            style_assignment=assignments.get(_uuid(row.get("style_assignment"))),
            **{field: row[field] for field in PREVIEW_FIELDS if field in row},
        )
        try:
            scene_layer.clean_fields(exclude=["scene", "layer", "style_assignment"])
            scene_layer.clean()
        except ValidationError as exc:
            errors[str(index)] = exc.message_dict
            continue
        scene_layers.append((scene_layer.display_order, index, scene_layer))

    renderable = [item for _, _, item in sorted(scene_layers, key=lambda entry: entry[:2])]
    renderable = [item for item in renderable if item.style_assignment_id]
    return {
        "map": build_map(request=request, scene_layers=renderable),
        "render_layers": build_render_layers_for(renderable),
        "legend": build_legend_for(renderable),
        "bounds": scene_bounds(renderable),
        "errors": errors,
    }


def preview_view(request, model_admin):
    if request.method != "POST":
        return JsonResponse({"error": "POST required."}, status=405)
    if not (model_admin.has_add_permission(request) or model_admin.has_change_permission(request)):
        return JsonResponse({"error": "Permission denied."}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    rows = payload.get("layers") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return JsonResponse({"error": "Expected {\"layers\": [...]}."}, status=400)
    if len(rows) > MAX_PREVIEW_LAYERS:
        return JsonResponse(
            {"error": f"At most {MAX_PREVIEW_LAYERS} layers can be previewed."}, status=400
        )
    return JsonResponse(build_preview(request, rows))
