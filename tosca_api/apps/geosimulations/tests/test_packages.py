"""Package manifest schema and validator (Task 12.2)."""

from __future__ import annotations

import gzip
import json
import os
import stat
import zipfile

import pytest
from jsonschema import Draft202012Validator

from tosca_api.apps.geosimulations.packages import (
    PackageLimits,
    PackageValidationError,
    manifest_schema,
    open_package,
    validate_package_dir,
)

from .package_helpers import (
    _sha256,
    manifest_template,
    write_gpkg,
    write_manifest,
    write_package,
    zip_dir,
)


def _issues(root, **kwargs) -> list[str]:
    with pytest.raises(PackageValidationError) as error:
        validate_package_dir(root, **kwargs)
    return [str(issue) for issue in error.value.issues]


def _rewrite(root, path, content: bytes, manifest) -> None:
    """Replace a package file and keep its checksum valid."""
    (root / path).write_bytes(content)
    manifest["files"][path] = _sha256(root / path)
    write_manifest(root, manifest)


def test_schema_is_a_valid_draft_2020_12_schema():
    Draft202012Validator.check_schema(manifest_schema())


def test_valid_directory_package(tmp_path):
    manifest = write_package(tmp_path)

    package = validate_package_dir(tmp_path)

    assert package.manifest == manifest
    assert package.output("replay")["kind"] == "trips"
    assert package.file("tables/kpis.csv") == tmp_path.resolve() / "tables" / "kpis.csv"
    assert package.warnings == ()
    with pytest.raises(KeyError):
        package.file("manifest.json")


def test_valid_zip_package_is_extracted_and_cleaned_up(tmp_path):
    write_package(tmp_path / "package")
    archive = zip_dir(tmp_path / "package", tmp_path / "package.zip")

    with open_package(archive) as package:
        staging = package.root
        assert (staging / "layers.gpkg").is_file()
        assert package.output("bike_flow")["layer"] == "bike_flow"

    assert not staging.exists()


def test_unknown_fields_are_kept_for_provenance(tmp_path):
    manifest = manifest_template()
    manifest["future_field"] = {"anything": True}
    manifest["outputs"][0]["new_hint"] = "ok"
    write_package(tmp_path, manifest)

    assert validate_package_dir(tmp_path).manifest["future_field"] == {"anything": True}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda m: m.pop("format"), "'format' is a required property"),
        (lambda m: m.update(format_version=2), "format_version: 1 was expected"),
        (lambda m: m["outputs"][0].update(kind="movie"), "outputs[0].kind"),
        (lambda m: m["outputs"][0].update(role="bike flow"), "outputs[0].role"),
        (lambda m: m["outputs"][0].update(path="/etc/passwd.gpkg"), "outputs[0].path"),
        (lambda m: m["outputs"][0].update(path="../layers.gpkg"), "outputs[0].path"),
        (lambda m: m["outputs"][0].pop("srid"), "'srid' is a required property"),
        (lambda m: m["outputs"][1].update(encoding_version=2), "outputs[1].encoding_version"),
        (lambda m: m["outputs"][2].update(path="tables/kpis.xlsx"), "outputs[2].path"),
        (lambda m: m["files"].update({"layers.gpkg": "md5:abc"}), "files.layers.gpkg"),
        (lambda m: m["run"].pop("parameters"), "'parameters' is a required property"),
        (lambda m: m.update(outputs=[]), "outputs"),
    ],
)
def test_schema_violations_are_reported_with_their_location(tmp_path, mutate, expected):
    manifest = write_package(tmp_path)
    mutate(manifest)
    write_manifest(tmp_path, manifest)

    assert any(expected in issue for issue in _issues(tmp_path)), _issues(tmp_path)


def test_missing_or_malformed_manifest(tmp_path):
    assert _issues(tmp_path) == ["manifest.json: Missing manifest.json at the package root."]
    (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
    assert "Not valid JSON" in _issues(tmp_path)[0]
    (tmp_path / "manifest.json").write_text("[]", encoding="utf-8")
    assert "must be a JSON object" in _issues(tmp_path)[0]


# ---------------------------------------------------------------------------
# Rules beyond the schema
# ---------------------------------------------------------------------------


def test_duplicate_roles(tmp_path):
    manifest = write_package(tmp_path)
    manifest["outputs"][3]["role"] = "kpis"
    write_manifest(tmp_path, manifest)

    assert "outputs[3] (kpis): Duplicate role." in _issues(tmp_path)


def test_files_must_match_the_listing_and_checksums(tmp_path):
    manifest = write_package(tmp_path)
    (tmp_path / "extra.txt").write_text("surprise", encoding="utf-8")
    (tmp_path / "tables" / "kpis.csv").write_text("metric,value\nchanged,1\n", encoding="utf-8")
    (tmp_path / "preview.png").unlink()
    write_manifest(tmp_path, manifest)

    issues = _issues(tmp_path)

    assert "extra.txt: File is not listed in files (no checksum)." in issues
    assert "files.tables/kpis.csv: Checksum mismatch." in issues
    assert "files.preview.png: Listed file is missing." in issues


def test_every_output_file_needs_a_checksum(tmp_path):
    manifest = write_package(tmp_path)
    del manifest["files"]["preview.png"]
    write_manifest(tmp_path, manifest)

    issues = _issues(tmp_path)

    assert "outputs[3] (preview): preview.png has no checksum in files." in issues


@pytest.mark.parametrize(
    ("time", "expected"),
    [
        ({"start": "2026-09-08T11:00:00"}, "run.time.start: The date-time needs a UTC offset."),
        ({"end": "yesterday"}, "run.time.end: Not an ISO 8601 date-time."),
        ({"end": "2026-09-08T10:59:59+02:00"}, "run.time.end: The simulated time cannot end"),
        ({"timezone": "Mars/Olympus"}, "run.time.timezone: Unknown IANA timezone"),
    ],
)
def test_run_time_rules(tmp_path, time, expected):
    manifest = write_package(tmp_path)
    manifest["run"]["time"].update(time)
    write_manifest(tmp_path, manifest)

    assert any(issue.startswith(expected) for issue in _issues(tmp_path)), _issues(tmp_path)


def test_run_bounds_must_be_ordered(tmp_path):
    manifest = write_package(tmp_path)
    manifest["run"]["bounds"] = [10.3, 53.4, 9.7, 53.7]
    write_manifest(tmp_path, manifest)

    assert any(issue.startswith("run.bounds:") for issue in _issues(tmp_path))


# ---------------------------------------------------------------------------
# Output content checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("layers", "expected"),
    [
        ({"other": ("LINESTRING", 25832, 2)}, "Layer 'bike_flow' is not in layers.gpkg."),
        ({"bike_flow": ("LINESTRING", 4326, 2)}, "SRID is 4326, manifest says 25832."),
        ({"bike_flow": ("POINT", 25832, 2)}, "Geometry type is POINT, manifest says LineString."),
        ({"bike_flow": ("LINESTRING", 25832, 5)}, "Layer has 5 features, manifest says 2."),
    ],
)
def test_vector_layer_must_match_the_geopackage(tmp_path, layers, expected):
    manifest = write_package(tmp_path)
    (tmp_path / "layers.gpkg").unlink()
    write_gpkg(tmp_path / "layers.gpkg", layers)
    manifest["files"]["layers.gpkg"] = _sha256(tmp_path / "layers.gpkg")
    write_manifest(tmp_path, manifest)

    assert f"outputs[0] (bike_flow): {expected}" in _issues(tmp_path)


def test_vector_layer_file_must_be_a_geopackage(tmp_path):
    manifest = write_package(tmp_path)
    _rewrite(tmp_path, "layers.gpkg", b"not a database", manifest)

    assert "outputs[0] (bike_flow): layers.gpkg is not a GeoPackage." in _issues(tmp_path)


def test_trips_must_be_gzip_with_the_declared_encoding(tmp_path):
    manifest = write_package(tmp_path)
    _rewrite(tmp_path, "replay/trips.json.gz", b'{"encoding":"tosca-trips"}', manifest)
    assert "outputs[1] (replay): replay/trips.json.gz is not gzip-compressed." in _issues(tmp_path)

    _rewrite(tmp_path, "replay/trips.json.gz", gzip.compress(b'{"encoding":"other"}'), manifest)
    assert any("tosca-trips encoding header" in issue for issue in _issues(tmp_path))


def test_trips_size_must_match_and_budget_is_only_a_warning(tmp_path):
    manifest = write_package(tmp_path)
    size = (tmp_path / "replay" / "trips.json.gz").stat().st_size
    manifest["outputs"][1]["size_bytes"] = size + 1
    write_manifest(tmp_path, manifest)
    assert f"outputs[1] (replay): File is {size} bytes, manifest says {size + 1}." in _issues(
        tmp_path
    )

    manifest["outputs"][1]["size_bytes"] = size
    write_manifest(tmp_path, manifest)
    package = validate_package_dir(tmp_path, limits=PackageLimits(trips_budget_bytes=10))
    assert [issue.location for issue in package.warnings] == ["outputs[1] (replay)"]


def test_tables_need_a_header_and_a_bounded_row_count(tmp_path):
    manifest = write_package(tmp_path)
    _rewrite(tmp_path, "tables/kpis.csv", b"metric,metric\nagents,1\n", manifest)
    assert "outputs[2] (kpis): tables/kpis.csv has duplicate column names." in _issues(tmp_path)

    rows = "metric,value\n" + "agents,1\n" * 4
    _rewrite(tmp_path, "tables/kpis.csv", rows.encode(), manifest)
    assert "outputs[2] (kpis): Tables are limited to 3 rows." in _issues(
        tmp_path, limits=PackageLimits(max_table_rows=3)
    )


def test_image_content_must_match_its_extension(tmp_path):
    manifest = write_package(tmp_path)
    _rewrite(tmp_path, "preview.png", b"<svg/>", manifest)

    assert "outputs[3] (preview): preview.png content does not match its extension." in _issues(
        tmp_path
    )


# ---------------------------------------------------------------------------
# Untrusted archives and directories
# ---------------------------------------------------------------------------


def _zip_with(tmp_path, members: dict[str, bytes], *, symlink: str | None = None):
    target = tmp_path / "evil.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "/etc/passwd")
    return target


def _open_issues(source, **kwargs) -> list[str]:
    with pytest.raises(PackageValidationError) as error:
        with open_package(source, **kwargs):
            pass
    return [str(issue) for issue in error.value.issues]


@pytest.mark.parametrize("name", ["../escape.txt", "/etc/cron.d/x", "a/../../b", "dir\\file.txt"])
def test_zip_members_cannot_escape_the_package(tmp_path, name):
    archive = _zip_with(tmp_path, {"manifest.json": b"{}", name: b"x"})

    issues = _open_issues(archive)

    assert any("not a safe relative path" in issue for issue in issues), issues
    assert not (tmp_path / "escape.txt").exists()


def test_zip_symlinks_are_rejected(tmp_path):
    archive = _zip_with(tmp_path, {"manifest.json": b"{}"}, symlink="layers.gpkg")

    assert "layers.gpkg: Symbolic links are not allowed." in _open_issues(archive)


def test_zip_size_and_file_count_are_bounded(tmp_path):
    archive = _zip_with(tmp_path, {f"file{index}.txt": b"x" for index in range(5)})
    assert "at most 3 are allowed" in _open_issues(archive, limits=PackageLimits(max_files=3))[0]

    archive = _zip_with(tmp_path, {"big.txt": os.urandom(2048)})
    assert "unpacks to 2048 bytes" in _open_issues(
        archive, limits=PackageLimits(max_package_bytes=1024)
    )[0]


def test_zip_bombs_are_rejected_by_compression_ratio(tmp_path):
    archive = _zip_with(tmp_path, {"bomb.txt": b"\0" * 1_000_000})

    assert "bomb.txt: Compression ratio is too high." in _open_issues(archive)


def test_sources_that_are_neither_directories_nor_zips(tmp_path):
    source = tmp_path / "package.tar"
    source.write_bytes(b"not a zip")

    assert _open_issues(source) == ["A package must be a directory or a .zip archive."]


def test_symlinks_inside_directory_packages_are_rejected(tmp_path):
    manifest = write_package(tmp_path / "package")
    (tmp_path / "secret.csv").write_text("metric,value\n", encoding="utf-8")
    link = tmp_path / "package" / "tables" / "linked.csv"
    link.symlink_to(tmp_path / "secret.csv")
    write_manifest(tmp_path / "package", manifest)

    assert "tables/linked.csv: Symbolic links are not allowed." in _issues(tmp_path / "package")


def test_real_exporter_manifest_shape_is_accepted(tmp_path):
    """Fields written by geodata-science bike-export-tosca (2026-10-09) validate."""
    manifest = manifest_template()
    manifest["model"]["source_revision"] = None
    manifest["outputs"][1].update(
        crs="EPSG:4326",
        time_unit="s",
        time_origin="run.time.start",
        vertices=2,
        stops=0,
        simplify_m=1.0,
        uncompressed_bytes=200,
    )
    manifest["outputs"][2]["rows"] = 1
    manifest["provenance"] = {
        "exported_at": "2026-10-09T20:38:00+00:00",
        "exporter": "hamburg-bike-routing 0.2.0",
        "inputs": {"network": {"path": "radinfrastruktur.gpkg", "sha256": "sha256:" + "0" * 64}},
        "limitations": ["Synthetic."],
    }
    write_package(tmp_path, manifest)
    json.loads((tmp_path / "manifest.json").read_text())

    assert validate_package_dir(tmp_path).output("replay")["vertices"] == 2
