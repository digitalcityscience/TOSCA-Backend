"""Builders for layers that can be placed in a GeoStory scene."""

import json

from tosca_api.apps.geodata_providers.models import LayerStyleAssignment, Store, Style
from tosca_api.apps.geodata_providers.test_helpers import make_layer

MBSTYLE = json.dumps(
    {
        "version": 8,
        "layers": [
            {"id": "parks-fill", "type": "fill", "source": "parks", "source-layer": "parks"},
            {"id": "parks-line", "type": "line", "source": "parks", "source-layer": "parks"},
            {"id": "bg", "type": "background"},
        ],
    }
)


def assign_style(layer, user, *, fmt, name, style_layer_ids=None, role="default"):
    style = Style.objects.create(
        geodata_engine=layer.workspace.geodata_engine,
        workspace=layer.workspace,
        name=name,
        format=fmt,
        file_content=MBSTYLE if fmt == Style.StyleFormat.MBSTYLE else "",
        validation_state=Style.ValidationState.VALID,
        created_by=user,
    )
    return LayerStyleAssignment.objects.create(
        layer=layer,
        style=style,
        role=role,
        style_layer_ids=style_layer_ids or [],
        created_by=user,
    )


def make_vector_layer(layer_name, *, user, **kwargs):
    """A WMTS vector layer with an MBStyle default style."""
    layer = make_layer(layer_name, user=user, **kwargs)
    assign_style(
        layer,
        user,
        fmt=Style.StyleFormat.MBSTYLE,
        name=f"{layer.name}-style",
        style_layer_ids=["parks-fill", "parks-line"],
    )
    return layer


def make_raster_layer(layer_name, *, user, **kwargs):
    """A WMS GeoTIFF layer with an SLD default style."""
    layer = make_layer(layer_name, user=user, **kwargs)
    Store.objects.filter(pk=layer.store_id).update(
        store_type=Store.StoreType.GEOTIFF, file_path=f"/data/{layer.name}.tif"
    )
    layer.refresh_from_db()
    assign_style(layer, user, fmt=Style.StyleFormat.SLD, name=f"{layer.name}-style")
    return layer
