"""Package import into object storage (Task 12.3).

These tests tile real GeoPackages with GDAL and write to the configured test
storage (Garage test buckets when the environment uses S3).
"""

from __future__ import annotations

import gzip
import json
from unittest.mock import patch

import pytest
from django.core.files.storage import storages
from django.core.management import CommandError, call_command

from tosca_api.apps.geosimulations import storage as storage_ops
from tosca_api.apps.geosimulations.importer import PackageImportError, import_package
from tosca_api.apps.geosimulations.models import (
    SimulationAsset,
    SimulationModel,
    SimulationOutput,
    SimulationRun,
    Status,
)
from tosca_api.apps.geosimulations.packages import PackageValidationError
from tosca_api.apps.geosimulations.tiles import TileBuildError

from .helpers import org, simulation_model
from .package_helpers import write_manifest, write_real_package, zip_dir

# Not transaction=True: with --reuse-db, a flushed test database loses the rows
# migrations seed (organizations, entitlements) for every later test. Files are
# removed after commit, so tests trigger on_commit callbacks explicitly.
pytestmark = pytest.mark.django_db


@pytest.fixture
def dcs():
    return org("dcs")


@pytest.fixture
def package_dir(tmp_path):
    write_real_package(tmp_path / "package")
    return tmp_path / "package"


@pytest.fixture
def cleanup(django_capture_on_commit_callbacks):
    """Delete every run created by a test, so stored objects are removed too."""
    yield
    with django_capture_on_commit_callbacks(execute=True):
        for run in SimulationRun.objects.all():
            run.delete()
        SimulationAsset.objects.all().delete()


def _stored(asset) -> bytes:
    with storages[asset.storage_alias].open(asset.storage_path, "rb") as handle:
        return handle.read()


def test_import_creates_model_run_outputs_and_files(dcs, package_dir, cleanup, django_user_model):
    user = django_user_model.objects.create_user(username="importer")

    result = import_package(package_dir, organization=dcs, user=user)

    run = result.run
    assert result.model_created
    assert run.model.key == "hamburg-bike-abm"
    assert run.model.organization == dcs
    assert run.model.status == Status.DRAFT
    assert (run.key, run.title, run.status) == ("tiny_run_v1", "Tiny run", Status.DRAFT)
    assert run.execution_state == SimulationRun.ExecutionState.IMPORTED
    assert run.time_start.isoformat() == "2026-09-08T11:00:00+02:00"
    assert run.timezone == "Europe/Berlin"
    assert run.created_by == user
    assert run.manifest["labels"]["bike"]["cargo"]["de"] == "Lastenrad"
    assert run.kpis == {"agents": 1}

    outputs = {output.role: output for output in run.outputs.select_related("asset")}
    assert list(outputs) == ["bike_flow", "replay", "kpis", "preview"]

    layer = outputs["bike_flow"]
    assert layer.asset.mime == "application/vnd.pmtiles"
    assert layer.asset.storage_path == f"geosimulations/dcs/{run.pk}/layers/bike_flow.pmtiles"
    assert _stored(layer.asset)[:7] == b"PMTiles"
    assert layer.metadata["source_layer"] == "bike_flow"
    assert layer.metadata["features"] == 2
    assert layer.metadata["maxzoom"] == 15
    assert 0 <= layer.metadata["minzoom"] <= 15
    west, south, east, north = layer.metadata["bounds"]
    assert 9.9 < west < east < 10.1 and 53.5 < south < north < 53.6
    assert layer.metadata["style_hint"] == "graduated:agent_count"
    assert layer.style[0]["type"] == "line"

    replay = outputs["replay"]
    assert json.loads(gzip.decompress(_stored(replay.asset)))["encoding"] == "tosca-trips"
    assert replay.metadata["encoding_version"] == 1

    assert outputs["kpis"].data == {"columns": ["metric", "value", "unit"], "rows": [["agents", 1, "count"]]}
    assert outputs["preview"].asset.mime == "image/png"

    assert run.source_package.original_name == "package.zip"
    assert run.assets.count() == 4  # package, tiles, replay, preview
    assert {asset.storage_alias for asset in run.assets.all()} == {"default"}


def test_publish_moves_files_to_the_public_alias_and_publishes_the_model(dcs, package_dir, cleanup):
    run = import_package(package_dir, organization=dcs, publish=True).run

    assert run.status == Status.PUBLISHED
    assert SimulationModel.objects.get().status == Status.PUBLISHED
    assert {asset.storage_alias for asset in run.assets.all()} == {"media_public"}


def test_zip_packages_are_archived_as_uploaded(dcs, package_dir, tmp_path, cleanup):
    archive = zip_dir(package_dir, tmp_path / "upload.zip")

    run = import_package(archive, organization=dcs).run

    assert _stored(run.source_package) == archive.read_bytes()


def test_existing_model_of_another_organization_is_rejected(package_dir, cleanup):
    simulation_model(org("qg2"), key="hamburg-bike-abm")

    with pytest.raises(PackageImportError, match="another organization"):
        import_package(package_dir, organization=org("dcs"))

    assert not SimulationRun.objects.exists()
    assert not SimulationAsset.objects.exists()


def test_reimport_needs_replace_and_replace_swaps_the_run(
    dcs, package_dir, cleanup, django_capture_on_commit_callbacks
):
    first = import_package(package_dir, organization=dcs).run
    first_paths = list(first.assets.values_list("storage_path", flat=True))

    with pytest.raises(PackageImportError, match="already exists"):
        import_package(package_dir, organization=dcs)

    with django_capture_on_commit_callbacks(execute=True):
        second = import_package(package_dir, organization=dcs, replace=True).run

    assert SimulationRun.objects.get().pk == second.pk != first.pk
    for path in first_paths:
        assert not storages["default"].exists(path)


def test_failed_import_leaves_no_rows_or_files(dcs, package_dir, cleanup):
    saved = []
    original_upload = storage_ops.upload_file

    def tracking_upload(alias, name, path, **kwargs):
        stored = original_upload(alias, name, path, **kwargs)
        saved.append(stored)
        return stored

    with (
        patch.object(storage_ops, "upload_file", side_effect=tracking_upload),
        patch(
            "tosca_api.apps.geosimulations.importer.build_pmtiles",
            side_effect=TileBuildError("Tiling 'bike_flow' failed: boom"),
        ),
    ):
        with pytest.raises(PackageImportError, match="boom"):
            import_package(package_dir, organization=dcs)

    assert saved  # the package archive was uploaded before tiling failed ...
    assert not any(storages["default"].exists(name) for name in saved)  # ... and removed again
    assert not SimulationModel.objects.exists()
    assert not SimulationRun.objects.exists()
    assert not SimulationAsset.objects.exists()


def test_invalid_packages_are_rejected_before_anything_is_written(dcs, package_dir, cleanup):
    manifest = json.loads((package_dir / "manifest.json").read_text())
    manifest["run"]["time"]["timezone"] = "Mars/Olympus"
    write_manifest(package_dir, manifest)

    with pytest.raises(PackageValidationError):
        import_package(package_dir, organization=dcs)

    assert not SimulationModel.objects.exists()
    assert not SimulationOutput.objects.exists()


def test_management_command(dcs, package_dir, cleanup, capsys):
    call_command("import_simulation_package", str(package_dir), "--organization", "dcs", "--publish")

    out = capsys.readouterr().out
    assert "Run:     tiny_run_v1 [published]" in out
    assert "bike_flow" in out and "Imported." in out

    with pytest.raises(CommandError, match="already exists"):
        call_command("import_simulation_package", str(package_dir), "--organization", "dcs")
    with pytest.raises(CommandError, match="Unknown organization"):
        call_command("import_simulation_package", str(package_dir), "--organization", "nope")
