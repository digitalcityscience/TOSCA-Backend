"""PMTiles conversion helpers (Task 12.3)."""

from __future__ import annotations

import pytest

from tosca_api.apps.geosimulations.styles import default_style
from tosca_api.apps.geosimulations.tiles import TileBuildError, build_pmtiles, minzoom_for_extent

from .package_helpers import write_real_gpkg


@pytest.mark.parametrize(
    ("span_m", "expected"),
    [
        (59_000, 8),  # Hamburg: the measured seed layers start at z8
        (5_000, 11),  # a district
        (40_075_016, 0),  # the whole world
        (10, 15),  # tiny extents are capped at maxzoom
        (0, 15),
    ],
)
def test_minzoom_for_extent(span_m, expected):
    assert minzoom_for_extent(span_m, span_m / 2, maxzoom=15) == expected


def test_build_pmtiles_reports_extent_and_zooms(tmp_path):
    write_real_gpkg(tmp_path / "layers.gpkg")

    result = build_pmtiles(tmp_path / "layers.gpkg", "bike_flow", "flow", tmp_path / "flow.pmtiles")

    assert result.path.read_bytes()[:7] == b"PMTiles"
    assert result.features == 2
    assert (result.minzoom, result.maxzoom) == (13, 15)
    west, south, east, north = result.bounds
    assert 9.9 < west < east < 10.1 and 53.5 < south < north < 53.6


def test_build_pmtiles_fails_cleanly_for_unknown_layers(tmp_path):
    write_real_gpkg(tmp_path / "layers.gpkg")

    with pytest.raises(TileBuildError, match="Cannot read layer 'missing'"):
        build_pmtiles(tmp_path / "layers.gpkg", "missing", "flow", tmp_path / "flow.pmtiles")


@pytest.mark.parametrize(
    ("geometry_type", "types"),
    [
        ("Point", ["circle"]),
        ("MultiLineString", ["line"]),
        ("MultiPolygon", ["fill", "line"]),
    ],
)
def test_default_styles_follow_geometry(geometry_type, types):
    style = default_style("bike_flow", geometry_type)

    assert [layer["type"] for layer in style] == types
    assert all(layer["id"].startswith("bike_flow-") and "source" not in layer for layer in style)
