"""Convert a package's GeoPackage layers to PMTiles with GDAL.

GDAL's PMTiles driver (GDAL ≥ 3.8) writes Mapbox vector tiles in EPSG:3857,
reprojecting from the layer's CRS. MapLibre reads the file straight from object
storage with HTTP range requests, so no tile server is involved.
"""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

from django.contrib.gis.gdal import DataSource, OGRGeometry, SpatialReference
from django.contrib.gis.gdal.error import GDALException

WEB_MERCATOR_WIDTH_M = 40_075_016.686
DEFAULT_MAXZOOM = 15
TIMEOUT_SECONDS = 600


class TileBuildError(Exception):
    pass


@dataclass(frozen=True)
class TileResult:
    path: Path
    minzoom: int
    maxzoom: int
    bounds: list[float] | None
    features: int


def minzoom_for_extent(width_m: float, height_m: float, maxzoom: int) -> int:
    """The zoom at which the whole layer spans about two tiles."""
    span = max(width_m, height_m)
    if span <= 0:
        return maxzoom
    zoom = math.floor(math.log2(WEB_MERCATOR_WIDTH_M / span)) - 1
    return max(0, min(zoom, maxzoom))


def _layer_extent(gpkg: Path, layer_name: str) -> tuple[int, list[float] | None, float, float]:
    """Feature count, WGS84 bounds, and Web Mercator width/height of a layer."""
    try:
        layer = DataSource(str(gpkg))[layer_name]
        count = layer.num_feat
        if count == 0:
            return 0, None, 0.0, 0.0
        envelope = OGRGeometry(layer.extent.wkt, srs=layer.srs)
    except (GDALException, IndexError) as exc:
        raise TileBuildError(f"Cannot read layer {layer_name!r}: {exc}") from exc
    wgs84 = envelope.clone()
    wgs84.transform(SpatialReference(4326))
    mercator = envelope.clone()
    mercator.transform(SpatialReference(3857))
    west, south, east, north = wgs84.extent
    m_west, m_south, m_east, m_north = mercator.extent
    bounds = [round(west, 6), round(south, 6), round(east, 6), round(north, 6)]
    return count, bounds, m_east - m_west, m_north - m_south


def build_pmtiles(
    gpkg: Path,
    layer_name: str,
    role: str,
    target: Path,
    *,
    maxzoom: int = DEFAULT_MAXZOOM,
) -> TileResult:
    """Write ``layer_name`` from ``gpkg`` to ``target`` as PMTiles; the tile layer is named ``role``."""
    count, bounds, width_m, height_m = _layer_extent(gpkg, layer_name)
    minzoom = minzoom_for_extent(width_m, height_m, maxzoom)
    command = [
        "ogr2ogr",
        "-f", "PMTiles",
        str(target),
        str(gpkg),
        layer_name,
        "-nln", role,
        "-dsco", f"MINZOOM={minzoom}",
        "-dsco", f"MAXZOOM={maxzoom}",
        "-dsco", f"NAME={role}",
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise TileBuildError(f"Tiling {role!r} took longer than {TIMEOUT_SECONDS} s.") from exc
    except OSError as exc:
        raise TileBuildError(f"Cannot run ogr2ogr: {exc}") from exc
    if completed.returncode != 0 or not target.is_file():
        detail = (completed.stderr or completed.stdout).strip().splitlines()[-1:] or ["no output"]
        raise TileBuildError(f"Tiling {role!r} failed: {detail[0]}")
    return TileResult(path=target, minzoom=minzoom, maxzoom=maxzoom, bounds=bounds, features=count)
