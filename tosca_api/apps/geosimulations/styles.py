"""MapLibre styles for simulation map layers.

A style is a list of MapLibre style layers *without* ``source``/``source-layer``;
the frontend adds the PMTiles source and sets ``source-layer`` to the output role.
Task 12.4 adds ``style_hint`` templates (graduated, categorical, choropleth,
heatmap); until then every layer gets a plain default for its geometry type.
"""

from __future__ import annotations

DEFAULT_COLOR = "#2563eb"
OUTLINE_COLOR = "#1e3a8a"


def default_style(role: str, geometry_type: str) -> list[dict]:
    base = geometry_type.removeprefix("Multi")
    if base == "Point":
        return [
            {
                "id": f"{role}-circle",
                "type": "circle",
                "paint": {
                    "circle-radius": 4,
                    "circle-color": DEFAULT_COLOR,
                    "circle-stroke-color": "#ffffff",
                    "circle-stroke-width": 1,
                },
            }
        ]
    if base == "LineString":
        return [
            {
                "id": f"{role}-line",
                "type": "line",
                "layout": {"line-cap": "round", "line-join": "round"},
                "paint": {"line-color": DEFAULT_COLOR, "line-width": 2},
            }
        ]
    return [
        {
            "id": f"{role}-fill",
            "type": "fill",
            "paint": {"fill-color": DEFAULT_COLOR, "fill-opacity": 0.35},
        },
        {
            "id": f"{role}-outline",
            "type": "line",
            "paint": {"line-color": OUTLINE_COLOR, "line-width": 1},
        },
    ]


def style_for_output(output: dict) -> list[dict]:
    """The style stored on a new map layer output (12.4 will honour ``style_hint``)."""
    return default_style(output["role"], output["geometry_type"])
