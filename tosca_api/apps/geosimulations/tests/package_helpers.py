"""Build small, valid simulation packages (format v1) for tests, without GDAL."""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def write_gpkg(path: Path, layers: dict[str, tuple[str, int, int]]) -> None:
    """Minimal GeoPackage metadata: {layer: (geometry type, srid, features)}."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE gpkg_contents (table_name TEXT PRIMARY KEY, data_type TEXT, srs_id INTEGER)"
        )
        connection.execute(
            "CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT, "
            "geometry_type_name TEXT, srs_id INTEGER)"
        )
        for name, (geometry_type, srid, features) in layers.items():
            connection.execute(f'CREATE TABLE "{name}" (fid INTEGER PRIMARY KEY, geom BLOB)')
            connection.execute("INSERT INTO gpkg_contents VALUES (?, 'features', ?)", (name, srid))
            connection.execute(
                "INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', ?, ?)",
                (name, geometry_type, srid),
            )
            connection.executemany(f'INSERT INTO "{name}" (geom) VALUES (NULL)', [()] * features)
        connection.commit()


def trips_bytes() -> bytes:
    bundle = {
        "encoding": "tosca-trips",
        "encoding_version": 1,
        "time_end_s": 42.0,
        "attributes": ["bike"],
        "trips": [{"id": "one", "a": ["cargo"], "p": [[10.0, 53.5], [10.001, 53.5]], "t": [0, 42]}],
        "stops": [],
    }
    return gzip.compress(json.dumps(bundle, separators=(",", ":")).encode(), mtime=0)


def manifest_template() -> dict:
    return {
        "format": "tosca-simulation-package",
        "format_version": 1,
        "model": {"key": "hamburg-bike-abm", "version": "0.2.0", "title": "Hamburg bike simulation"},
        "run": {
            "key": "tiny_run_v1",
            "title": "Tiny run",
            "created_at": "2026-10-09T12:00:00+00:00",
            "seed": 42,
            "parameters": {"agents": 1, "seed": 42},
            "time": {
                "start": "2026-09-08T11:00:00+02:00",
                "end": "2026-09-08T11:00:42+02:00",
                "timezone": "Europe/Berlin",
            },
            "bounds": [9.73, 53.395, 10.325, 53.739],
        },
        "outputs": [
            {
                "role": "bike_flow",
                "kind": "vector_layer",
                "title": "Cyclists per segment",
                "path": "layers.gpkg",
                "layer": "bike_flow",
                "geometry_type": "LineString",
                "srid": 25832,
                "features": 2,
                "style_hint": "graduated:agent_count",
            },
            {
                "role": "replay",
                "kind": "trips",
                "title": "Agent replay",
                "path": "replay/trips.json.gz",
                "encoding": "tosca-trips",
                "encoding_version": 1,
                "agents": 1,
            },
            {"role": "kpis", "kind": "table", "title": "Key figures", "path": "tables/kpis.csv"},
            {"role": "preview", "kind": "image", "title": "Preview", "path": "preview.png"},
        ],
        "labels": {"bike": {"cargo": {"en": "Cargo bike", "de": "Lastenrad"}}},
        "provenance": {"exporter": "tests"},
    }


def write_package(root: Path, manifest: dict | None = None, *, checksums: bool = True) -> dict:
    """Write a valid package to ``root`` and return its manifest."""
    root.mkdir(parents=True, exist_ok=True)
    write_gpkg(root / "layers.gpkg", {"bike_flow": ("LINESTRING", 25832, 2)})
    (root / "replay").mkdir(exist_ok=True)
    (root / "replay" / "trips.json.gz").write_bytes(trips_bytes())
    (root / "tables").mkdir(exist_ok=True)
    (root / "tables" / "kpis.csv").write_text("metric,value,unit\nagents,1,count\n", encoding="utf-8")
    (root / "preview.png").write_bytes(PNG)
    manifest = manifest or manifest_template()
    if checksums:
        manifest["files"] = {
            path.relative_to(root).as_posix(): _sha256(path)
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.name != "manifest.json"
        }
    write_manifest(root, manifest)
    return manifest


def write_manifest(root: Path, manifest: dict) -> None:
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def zip_dir(root: Path, target: Path) -> Path:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())
    return target


def write_real_gpkg(path: Path, layer: str = "bike_flow") -> None:
    """A real two-feature line GeoPackage in EPSG:25832, written with ogr2ogr."""
    import subprocess

    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"agent_count": count, "street_name": name},
                "geometry": {"type": "LineString", "coordinates": coordinates},
            }
            for count, name, coordinates in (
                (12, "Mönckebergstraße", [[566000, 5934000], [566400, 5934100]]),
                (3, "Steindamm", [[566400, 5934100], [566900, 5934600]]),
            )
        ],
    }
    source = path.with_suffix(".geojson")
    source.write_text(json.dumps(geojson), encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ogr2ogr", "-f", "GPKG", str(path), str(source), "-nln", layer, "-a_srs", "EPSG:25832"],
        check=True,
        capture_output=True,
    )
    source.unlink()


def write_real_package(root: Path, manifest: dict | None = None) -> dict:
    """Like ``write_package`` but with a GeoPackage GDAL can actually tile."""
    manifest = write_package(root, manifest)
    (root / "layers.gpkg").unlink()
    write_real_gpkg(root / "layers.gpkg")
    manifest["files"]["layers.gpkg"] = _sha256(root / "layers.gpkg")
    write_manifest(root, manifest)
    return manifest


def comparison_manifest(baseline_key: str = "baseline_v1", candidate_key: str = "candidate_v1") -> dict:
    return {
        "format": "tosca-simulation-package",
        "format_version": 1,
        "kind": "comparison",
        "model": {"key": "hamburg-bike-abm", "version": "0.2.0", "title": "Hamburg bike simulation"},
        "comparison": {
            "key": "queues_vs_turn_queues",
            "title": "Queues vs turn-aware queues",
            "baseline": {"run_key": baseline_key},
            "candidate": {"run_key": candidate_key},
            "bounds": [9.73, 53.395, 10.325, 53.739],
        },
        "outputs": [
            {
                "role": "flow_change",
                "kind": "vector_layer",
                "title": "Change in cyclists per segment",
                "path": "layers.gpkg",
                "layer": "flow_change",
                "geometry_type": "LineString",
                "srid": 25832,
                "features": 2,
                "style_hint": "diverging:agent_count",
            },
            {"role": "summary", "kind": "table", "title": "Summary", "path": "tables/summary.csv"},
        ],
        "labels": {"bike": {"cargo": {"en": "Cargo bike (compared)"}}},
    }


def write_comparison_package(root: Path, manifest: dict | None = None, *, real_gpkg: bool = True) -> dict:
    """A comparison package: one difference layer and one table."""
    root.mkdir(parents=True, exist_ok=True)
    if real_gpkg:
        write_real_gpkg(root / "layers.gpkg", layer="flow_change")
    else:
        write_gpkg(root / "layers.gpkg", {"flow_change": ("LINESTRING", 25832, 2)})
    (root / "tables").mkdir(exist_ok=True)
    (root / "tables" / "summary.csv").write_text(
        "scenario,mean_trip_time_min\nbaseline,21.5\ncandidate,23.0\n", encoding="utf-8"
    )
    manifest = manifest or comparison_manifest()
    manifest["files"] = {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
    write_manifest(root, manifest)
    return manifest
