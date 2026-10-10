"""Validate TOSCA simulation packages before anything is imported.

A package is a directory or ``.zip`` with ``manifest.json`` at its root (format
v1, ``schemas/package-manifest-v1.schema.json``). Packages are untrusted input:
today an admin uploads them, later a runner service produces them for runs that
frontend users start. Validation therefore never trusts paths, sizes, or
archive members, and it runs without a request so the admin, the import command,
and a runner callback share the same code.

Usage::

    with open_package(path) as package:
        package.manifest, package.output("bike_flow"), package.file("layers.gpkg")
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import sqlite3
import stat
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from functools import cache
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.exceptions import ValidationError
from jsonschema import Draft202012Validator

from .models import validate_bounds, validate_timezone_name

MANIFEST_NAME = "manifest.json"
SCHEMA_PATH = Path(__file__).parent / "schemas" / "package-manifest-v1.schema.json"
GZIP_MAGIC = b"\x1f\x8b"
GPKG_MAGIC = b"SQLite format 3\x00"
IMAGE_MAGIC = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),
}
GEOMETRY_TYPES_IN_GPKG = {
    "Point": {"POINT"},
    "LineString": {"LINESTRING"},
    "Polygon": {"POLYGON"},
    "MultiPoint": {"MULTIPOINT", "POINT"},
    "MultiLineString": {"MULTILINESTRING", "LINESTRING"},
    "MultiPolygon": {"MULTIPOLYGON", "POLYGON"},
}


@dataclass(frozen=True)
class PackageLimits:
    """Bounds for untrusted packages; override with ``GEOSIMULATIONS_PACKAGE_LIMITS``."""

    max_package_bytes: int = 512 * 1024 * 1024
    max_files: int = 200
    max_manifest_bytes: int = 1024 * 1024
    max_table_rows: int = 5_000
    max_compression_ratio: int = 200
    trips_budget_bytes: int = 10 * 1000 * 1000

    @classmethod
    def from_settings(cls) -> PackageLimits:
        return cls(**getattr(settings, "GEOSIMULATIONS_PACKAGE_LIMITS", {}))


@dataclass(frozen=True)
class PackageIssue:
    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.location}: {self.message}" if self.location else self.message


class PackageValidationError(Exception):
    def __init__(self, issues: list[PackageIssue]):
        self.issues = tuple(issues)
        super().__init__("Invalid simulation package:\n" + "\n".join(f"- {i}" for i in issues))


@dataclass(frozen=True)
class ValidatedPackage:
    root: Path
    manifest: dict
    warnings: tuple[PackageIssue, ...] = field(default=())

    @property
    def outputs(self) -> list[dict]:
        return self.manifest["outputs"]

    def output(self, role: str) -> dict:
        for output in self.outputs:
            if output["role"] == role:
                return output
        raise KeyError(role)

    def file(self, package_path: str) -> Path:
        """Absolute path of a validated package file."""
        if package_path not in self.manifest["files"]:
            raise KeyError(package_path)
        return self.root / package_path


@cache
def manifest_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@contextmanager
def open_package(
    source: str | Path, *, limits: PackageLimits | None = None
) -> Iterator[ValidatedPackage]:
    """Validate a package directory or ``.zip``; zips are extracted to a temporary directory."""
    limits = limits or PackageLimits.from_settings()
    source = Path(source)
    if source.is_dir():
        yield validate_package_dir(source, limits=limits)
        return
    if not zipfile.is_zipfile(source):
        raise PackageValidationError(
            [PackageIssue("", "A package must be a directory or a .zip archive.")]
        )
    with tempfile.TemporaryDirectory(prefix="geosim-package-") as staging:
        root = Path(staging)
        _safe_extract(source, root, limits)
        yield validate_package_dir(root, limits=limits)


def _safe_extract(archive_path: Path, root: Path, limits: PackageLimits) -> None:
    """Extract without zip-slip, links, or decompression bombs."""
    issues: list[PackageIssue] = []
    with zipfile.ZipFile(archive_path) as archive:
        members = [info for info in archive.infolist() if not info.is_dir()]
        if len(members) > limits.max_files:
            raise PackageValidationError(
                [PackageIssue("", f"The archive has {len(members)} files; at most {limits.max_files} are allowed.")]
            )
        total = sum(info.file_size for info in members)
        if total > limits.max_package_bytes:
            raise PackageValidationError(
                [PackageIssue("", f"The archive unpacks to {total} bytes; at most {limits.max_package_bytes} are allowed.")]
            )
        for info in members:
            name = info.filename
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                issues.append(PackageIssue(name, "Symbolic links are not allowed."))
            elif not _is_package_path(name):
                issues.append(PackageIssue(name, "Archive member path is not a safe relative path."))
            elif info.compress_size and info.file_size / info.compress_size > limits.max_compression_ratio:
                issues.append(PackageIssue(name, "Compression ratio is too high."))
        if issues:
            raise PackageValidationError(issues)
        for info in members:
            target = root / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with archive.open(info) as member, target.open("wb") as handle:
                # Enforce the declared size while streaming; headers can lie.
                for chunk in iter(lambda: member.read(1 << 20), b""):
                    written += len(chunk)
                    if written > info.file_size or written > limits.max_package_bytes:
                        raise PackageValidationError(
                            [PackageIssue(info.filename, "Member is larger than declared.")]
                        )
                    handle.write(chunk)


def _is_package_path(value: str) -> bool:
    if not value or "\\" in value or len(value) > 512:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in ("", ".", "..") for part in path.parts)


def validate_package_dir(root: str | Path, *, limits: PackageLimits | None = None) -> ValidatedPackage:
    """Validate an unpacked package and return it; raise ``PackageValidationError`` otherwise."""
    limits = limits or PackageLimits.from_settings()
    root = Path(root).resolve()
    manifest = _load_manifest(root, limits)

    schema_issues = [
        PackageIssue(_json_location(error.absolute_path), error.message)
        for error in sorted(
            Draft202012Validator(manifest_schema()).iter_errors(manifest),
            key=lambda error: list(error.absolute_path),
        )
    ]
    if schema_issues:
        # Later checks assume the shape the schema guarantees.
        raise PackageValidationError(schema_issues)

    issues: list[PackageIssue] = []
    warnings: list[PackageIssue] = []
    _check_run(manifest["run"], issues)
    _check_files(root, manifest, limits, issues)
    roles: set[str] = set()
    for index, output in enumerate(manifest["outputs"]):
        location = f"outputs[{index}] ({output['role']})"
        if output["role"] in roles:
            issues.append(PackageIssue(location, "Duplicate role."))
        roles.add(output["role"])
        if output["path"] not in manifest["files"]:
            issues.append(PackageIssue(location, f"{output['path']} has no checksum in files."))
            continue
        path = root / output["path"]
        if not path.is_file():
            continue  # already reported by _check_files
        checker = {
            "vector_layer": _check_vector_layer,
            "trips": _check_trips,
            "table": _check_table,
            "image": _check_image,
        }[output["kind"]]
        checker(path, output, location, limits, issues, warnings)
    if issues:
        raise PackageValidationError(issues)
    return ValidatedPackage(root=root, manifest=manifest, warnings=tuple(warnings))


def _load_manifest(root: Path, limits: PackageLimits) -> dict:
    path = root / MANIFEST_NAME
    if not path.is_file() or path.is_symlink():
        raise PackageValidationError([PackageIssue(MANIFEST_NAME, "Missing manifest.json at the package root.")])
    if path.stat().st_size > limits.max_manifest_bytes:
        raise PackageValidationError([PackageIssue(MANIFEST_NAME, "manifest.json is too large.")])
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageValidationError([PackageIssue(MANIFEST_NAME, f"Not valid JSON: {exc}")]) from exc
    if not isinstance(manifest, dict):
        raise PackageValidationError([PackageIssue(MANIFEST_NAME, "The manifest must be a JSON object.")])
    return manifest


def _json_location(path) -> str:
    location = ""
    for part in path:
        location += f"[{part}]" if isinstance(part, int) else (f".{part}" if location else part)
    return location


def _check_run(run: dict, issues: list[PackageIssue]) -> None:
    time = run["time"]
    try:
        validate_timezone_name(time["timezone"])
    except ValidationError as exc:
        issues.append(PackageIssue("run.time.timezone", exc.messages[0]))
    moments = {}
    for name in ("start", "end"):
        try:
            moment = datetime.fromisoformat(time[name])
        except ValueError:
            issues.append(PackageIssue(f"run.time.{name}", "Not an ISO 8601 date-time."))
            continue
        if moment.tzinfo is None:
            issues.append(PackageIssue(f"run.time.{name}", "The date-time needs a UTC offset."))
            continue
        moments[name] = moment
    if len(moments) == 2 and moments["end"] < moments["start"]:
        issues.append(PackageIssue("run.time.end", "The simulated time cannot end before it starts."))
    if "bounds" in run:
        try:
            validate_bounds(run["bounds"])
        except ValidationError as exc:
            issues.append(PackageIssue("run.bounds", exc.messages[0]))


def _check_files(root: Path, manifest: dict, limits: PackageLimits, issues: list[PackageIssue]) -> None:
    declared = manifest["files"]
    present: set[str] = set()
    total = 0
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            issues.append(PackageIssue(relative, "Symbolic links are not allowed."))
            continue
        if path.is_file() and relative != MANIFEST_NAME:
            present.add(relative)
            total += path.stat().st_size
    if len(present) > limits.max_files:
        issues.append(PackageIssue("files", f"{len(present)} files; at most {limits.max_files} are allowed."))
    if total > limits.max_package_bytes:
        issues.append(PackageIssue("files", f"{total} bytes; at most {limits.max_package_bytes} are allowed."))
    for relative in sorted(set(declared) - present):
        issues.append(PackageIssue(f"files.{relative}", "Listed file is missing."))
    for relative in sorted(present - set(declared)):
        issues.append(PackageIssue(relative, "File is not listed in files (no checksum)."))
    for relative in sorted(set(declared) & present):
        if _sha256(root / relative) != declared[relative]:
            issues.append(PackageIssue(f"files.{relative}", "Checksum mismatch."))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _starts_with(path: Path, *prefixes: bytes) -> bool:
    with path.open("rb") as handle:
        head = handle.read(max(len(prefix) for prefix in prefixes))
    return any(head.startswith(prefix) for prefix in prefixes)


def _check_vector_layer(path, output, location, limits, issues, warnings) -> None:
    if not _starts_with(path, GPKG_MAGIC):
        issues.append(PackageIssue(location, f"{output['path']} is not a GeoPackage."))
        return
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
            row = connection.execute(
                "SELECT c.data_type, g.geometry_type_name, g.srs_id "
                "FROM gpkg_contents c JOIN gpkg_geometry_columns g ON g.table_name = c.table_name "
                "WHERE c.table_name = ?",
                (output["layer"],),
            ).fetchone()
            count = (
                connection.execute(f'SELECT count(*) FROM "{output["layer"]}"').fetchone()[0]
                if row
                else None
            )
    except sqlite3.DatabaseError as exc:
        issues.append(PackageIssue(location, f"Cannot read {output['path']}: {exc}"))
        return
    if row is None:
        issues.append(PackageIssue(location, f"Layer {output['layer']!r} is not in {output['path']}."))
        return
    data_type, geometry_type, srs_id = row
    if data_type != "features":
        issues.append(PackageIssue(location, f"Layer {output['layer']!r} is not a feature layer."))
    if (geometry_type or "").upper() not in GEOMETRY_TYPES_IN_GPKG[output["geometry_type"]] | {"GEOMETRY"}:
        issues.append(
            PackageIssue(location, f"Geometry type is {geometry_type}, manifest says {output['geometry_type']}.")
        )
    if srs_id != output["srid"]:
        issues.append(PackageIssue(location, f"SRID is {srs_id}, manifest says {output['srid']}."))
    if "features" in output and count != output["features"]:
        issues.append(PackageIssue(location, f"Layer has {count} features, manifest says {output['features']}."))


def _check_trips(path, output, location, limits, issues, warnings) -> None:
    if not _starts_with(path, GZIP_MAGIC):
        issues.append(PackageIssue(location, f"{output['path']} is not gzip-compressed."))
        return
    size = path.stat().st_size
    if "size_bytes" in output and output["size_bytes"] != size:
        issues.append(PackageIssue(location, f"File is {size} bytes, manifest says {output['size_bytes']}."))
    if size > limits.trips_budget_bytes:
        warnings.append(
            PackageIssue(location, f"Replay is {size / 1e6:.1f} MB, over the {limits.trips_budget_bytes / 1e6:.0f} MB budget.")
        )
    try:
        with gzip.open(path, "rb") as handle:
            head = handle.read(4096)
    except (OSError, EOFError) as exc:
        issues.append(PackageIssue(location, f"Cannot decompress {output['path']}: {exc}"))
        return
    # Full parsing happens on import; here only confirm the declared encoding.
    if b'"encoding":"tosca-trips"' not in head.replace(b" ", b""):
        issues.append(PackageIssue(location, "Replay does not start with the tosca-trips encoding header."))


def _check_table(path, output, location, limits, issues, warnings) -> None:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            rows = 0
            for rows, _ in enumerate(reader, start=1):
                if rows > limits.max_table_rows:
                    break
    except (UnicodeDecodeError, csv.Error) as exc:
        issues.append(PackageIssue(location, f"Cannot read {output['path']} as UTF-8 CSV: {exc}"))
        return
    if not header:
        issues.append(PackageIssue(location, f"{output['path']} has no header row."))
    elif len(set(header)) != len(header):
        issues.append(PackageIssue(location, f"{output['path']} has duplicate column names."))
    if rows > limits.max_table_rows:
        issues.append(PackageIssue(location, f"Tables are limited to {limits.max_table_rows} rows."))


def _check_image(path, output, location, limits, issues, warnings) -> None:
    suffix = path.suffix.lower()
    if not _starts_with(path, *IMAGE_MAGIC[suffix]):
        issues.append(PackageIssue(location, f"{output['path']} content does not match its extension."))
