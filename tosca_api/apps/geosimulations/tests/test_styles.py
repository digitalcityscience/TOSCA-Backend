"""Style templates and legends for map layer outputs (Task 12.4)."""

from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from tosca_api.apps.geosimulations.styles import (
    CATEGORICAL,
    OTHER_COLOR,
    SEQUENTIAL,
    _nice,
    build_style,
    class_breaks,
    parse_style_hint,
)


def _gpkg(path, layer, column, values, sql_type="INTEGER"):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(f'CREATE TABLE "{layer}" (fid INTEGER PRIMARY KEY, "{column}" {sql_type})')
        connection.executemany(f'INSERT INTO "{layer}" ("{column}") VALUES (?)', [(v,) for v in values])
        connection.commit()
    return path


def _output(hint, geometry="LineString", role="flow", title="Flow"):
    return {"role": role, "title": title, "layer": role, "geometry_type": geometry, "style_hint": hint}


# Shaped like bike_flow: most segments carry one or two cyclists, a few carry hundreds.
SKEWED = [1] * 600 + [2] * 200 + [3] * 100 + list(range(4, 60)) * 2 + [120, 300, 640, 1000]


def test_parse_style_hint():
    assert parse_style_hint("graduated:agent_count") == ("graduated", "agent_count")
    assert parse_style_hint("simple") == ("simple", None)
    assert parse_style_hint(None) == ("simple", None)
    assert parse_style_hint("heatmap:") == ("heatmap", None)


@pytest.mark.parametrize(("value", "nice"), [(3.4, 5), (11.8, 10), (40, 50), (138, 100), (0.27, 0.2)])
def test_nice_numbers(value, nice):
    assert _nice(value) == pytest.approx(nice)


def test_long_tailed_counts_get_rounded_log_breaks():
    assert class_breaks(SKEWED) == [5, 20, 50, 200]


def test_even_data_gets_quantile_breaks():
    breaks = class_breaks([float(value) for value in range(1, 101)])

    assert breaks == pytest.approx([20.8, 40.6, 60.4, 80.2])


def test_constant_values_have_one_class():
    assert class_breaks([7, 7, 7]) == []


def test_graduated_lines_scale_colour_and_width(tmp_path):
    gpkg = _gpkg(tmp_path / "l.gpkg", "flow", "agent_count", SKEWED)

    result = build_style(_output("graduated:agent_count"), gpkg)

    (layer,) = result.style
    assert layer["type"] == "line"
    assert layer["paint"]["line-color"] == [
        "step", ["to-number", ["get", "agent_count"], 0],
        SEQUENTIAL[0], 5, SEQUENTIAL[1], 20, SEQUENTIAL[2], 50, SEQUENTIAL[3], 200, SEQUENTIAL[4],
    ]
    assert layer["paint"]["line-width"][2] == ["zoom"]
    assert layer["layout"]["line-sort-key"] == ["to-number", ["get", "agent_count"], 0]
    assert layer["metadata"]["tosca:legend-label"] == "Flow"
    legend = result.legend
    assert (legend["type"], legend["attribute"], legend["geometry"]) == ("graduated", "agent_count", "LineString")
    assert [item["label"] for item in legend["items"]] == ["< 5", "5 – 20", "20 – 50", "50 – 200", "≥ 200"]
    assert [item["color"] for item in legend["items"]] == SEQUENTIAL
    assert result.warnings == []


def test_choropleth_polygons_for_hexagon_grids(tmp_path):
    gpkg = _gpkg(tmp_path / "h.gpkg", "hexagons", "share", [i / 10 for i in range(50)], "REAL")

    result = build_style(_output("choropleth:share", "MultiPolygon", role="hexagons", title="Share"), gpkg)

    fill, outline = result.style
    assert (fill["type"], outline["type"]) == ("fill", "line")
    assert fill["paint"]["fill-color"][0] == "step"
    assert len(result.legend["items"]) == 5
    assert result.legend["geometry"] == "Polygon"


def test_graduated_polygons_become_a_choropleth(tmp_path):
    gpkg = _gpkg(tmp_path / "h.gpkg", "flow", "share", [1, 2, 3, 4, 5, 6])

    result = build_style(_output("graduated:share", "Polygon"), gpkg)

    assert result.style[0]["type"] == "fill"


def test_proportional_points_use_area_scaled_circles(tmp_path):
    gpkg = _gpkg(tmp_path / "p.gpkg", "flow", "delay", [0.0, 4.0, 9.0, 16.0, 52.8], "REAL")

    result = build_style(_output("proportional:delay", "Point"), gpkg)

    radius = result.style[0]["paint"]["circle-radius"]
    assert radius[:2] == ["interpolate", ["linear"]]
    assert radius[2][0] == "sqrt"
    assert radius[-4:] == [0, 3, pytest.approx(52.8**0.5), 18]
    legend = result.legend
    assert legend["type"] == "proportional"
    assert [item["label"] for item in legend["items"]] == ["0", "9", "52.8"]
    assert [item["size"] for item in legend["items"]] == [3, pytest.approx(9.2, abs=0.1), 18]


def test_categorical_colours_use_manifest_labels_and_group_the_rest(tmp_path):
    values = ["radweg"] * 5 + ["streifen"] * 4 + [f"other_{i}" for i in range(12)]
    gpkg = _gpkg(tmp_path / "c.gpkg", "flow", "infrastructure_type", values, "TEXT")
    labels = {"infrastructure_type": {"radweg": {"en": "Cycle track", "de": "Radweg"}}}

    result = build_style(_output("categorical:infrastructure_type"), gpkg, labels=labels)

    match = result.style[0]["paint"]["line-color"]
    assert match[:4] == ["match", ["get", "infrastructure_type"], "radweg", CATEGORICAL[0]]
    assert match[-1] == OTHER_COLOR
    items = result.legend["items"]
    assert items[0] == {"color": CATEGORICAL[0], "value": "radweg", "label": {"en": "Cycle track", "de": "Radweg"}}
    assert items[1]["label"] == "streifen"
    assert len(items) == 11 and items[-1]["label"] == "Other"


def test_decimal_categories_are_matched_as_text(tmp_path):
    gpkg = _gpkg(tmp_path / "c.gpkg", "flow", "speed", [12.5, 15.0, 12.5], "REAL")

    match = build_style(_output("categorical:speed"), gpkg).style[0]["paint"]["line-color"]

    assert match[1] == ["to-string", ["get", "speed"]]
    assert match[2] == "12.5"


def test_heatmap_points_fade_into_circles(tmp_path):
    gpkg = _gpkg(tmp_path / "p.gpkg", "stops", "weight", [1, 2, 3, 10])

    result = build_style(_output("heatmap:weight", "Point", role="stops"), gpkg)

    heatmap, circles = result.style
    assert heatmap["type"] == "heatmap"
    assert heatmap["paint"]["heatmap-weight"] == [
        "interpolate", ["linear"], ["to-number", ["get", "weight"], 0], 0, 0, 10, 1,
    ]
    assert heatmap["paint"]["heatmap-color"][2] == ["heatmap-density"]
    assert (circles["type"], circles["minzoom"]) == ("circle", 13)
    assert result.legend["type"] == "heatmap"


@pytest.mark.parametrize(
    ("hint", "geometry", "values", "sql_type", "warning", "layer_type"),
    [
        ("sparkle:agent_count", "LineString", [1, 2], "INTEGER", "unknown style hint", "line"),
        ("graduated", "LineString", [1, 2], "INTEGER", "needs an attribute", "line"),
        ("graduated:missing", "LineString", [1, 2], "INTEGER", "missing or empty", "line"),
        ("graduated:agent_count", "LineString", ["a", "b"], "TEXT", "using categories", "line"),
        ("heatmap:agent_count", "LineString", [1, 5, 9], "INTEGER", "heatmaps need points", "line"),
        ("proportional:agent_count", "Polygon", [1, 5, 9], "INTEGER", "using a choropleth", "fill"),
    ],
)
def test_unusable_hints_fall_back_with_a_warning(tmp_path, hint, geometry, values, sql_type, warning, layer_type):
    gpkg = _gpkg(tmp_path / "f.gpkg", "flow", "agent_count", values, sql_type)

    result = build_style(_output(hint, geometry), gpkg)

    assert result.style[0]["type"] == layer_type
    assert any(warning in message for message in result.warnings), result.warnings
    assert result.legend["items"]


def test_simple_hint_uses_the_plain_style_with_a_legend(tmp_path):
    gpkg = _gpkg(tmp_path / "s.gpkg", "flow", "agent_count", [1])

    result = build_style(_output("simple", "Point"), gpkg)

    assert result.style[0]["type"] == "circle"
    assert result.legend == {
        "type": "simple",
        "title": "Flow",
        "geometry": "Point",
        "items": [{"color": "#2563eb", "label": "Flow"}],
    }
    assert result.warnings == []
