from __future__ import annotations

import json
import zipfile

import pytest
from django.core.management import call_command

from tosca_api.apps.geosimulations.importer import import_package
from tosca_api.apps.geosimulations.models import SimulationModel, SimulationRun, Status
from tosca_api.apps.geosimulations.seed_bundles import (
    MANIFEST_NAME,
    SeedBundleError,
    export_seed,
    import_seed,
)

from .helpers import org
from .package_helpers import write_real_package


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
    yield
    with django_capture_on_commit_callbacks(execute=True):
        SimulationRun.objects.all().delete()
        SimulationModel.objects.all().delete()


def test_seed_round_trip_publishes_drafts_and_omits_failed_runs(
    dcs, package_dir, tmp_path, cleanup, django_capture_on_commit_callbacks
):
    imported = import_package(package_dir, organization=dcs)
    model = imported.run.model
    model.summary = "Portable catalog metadata"
    model.description_content = {"blocks": [{"type": "paragraph", "data": {"text": "Rich text"}}]}
    model.parameter_schema = {"type": "object", "properties": {"agents": {"type": "integer"}}}
    model.default_parameters = {"agents": 100}
    model.runner_version = "0.2.0"
    model.save()
    SimulationRun.objects.create(
        model=model,
        key="failed-local-run",
        title="Failed local run",
        model_version="0.2.0",
        execution_state=SimulationRun.ExecutionState.FAILED,
    )

    archive, omitted = export_seed(model, tmp_path / "exports")

    assert archive.name == "hamburg-bike-abm-simulation-seed.zip"
    assert omitted == ({"key": "failed-local-run", "reason": "execution state is failed"},)
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read(MANIFEST_NAME))
        assert [item["key"] for item in manifest["runs"]] == ["tiny_run_v1"]
        assert manifest["model"]["summary"] == "Portable catalog metadata"

    with django_capture_on_commit_callbacks(execute=True):
        model.delete()
    result = import_seed(archive, organization=dcs)

    assert result.model.status == Status.PUBLISHED
    assert result.model.runner_id is None
    assert result.model.summary == "Portable catalog metadata"
    assert result.model.parameter_schema["properties"]["agents"]["type"] == "integer"
    assert [run.key for run in result.imported] == ["tiny_run_v1"]
    assert result.imported[0].status == Status.PUBLISHED
    assert result.imported[0].runner_id is None
    assert result.omitted == omitted
    assert not SimulationRun.objects.filter(key="failed-local-run").exists()


def test_seed_import_is_idempotent_for_published_runs(dcs, package_dir, tmp_path, cleanup):
    run = import_package(package_dir, organization=dcs, publish=True).run
    archive, _ = export_seed(run.model, tmp_path / "seed.zip")

    result = import_seed(archive, organization=dcs)

    assert result.imported == ()
    assert [item.pk for item in result.skipped] == [run.pk]


def test_seed_rejects_a_changed_nested_package(dcs, package_dir, tmp_path, cleanup):
    run = import_package(package_dir, organization=dcs).run
    archive, _ = export_seed(run.model, tmp_path / "seed.zip")
    changed = tmp_path / "changed.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(changed, "w") as target:
        for item in source.infolist():
            content = source.read(item)
            if item.filename.startswith("runs/"):
                content += b"changed"
            target.writestr(item, content)

    with pytest.raises(SeedBundleError, match="Checksum mismatch"):
        import_seed(changed, organization=dcs)


def test_seed_management_commands(dcs, package_dir, tmp_path, cleanup, capsys):
    run = import_package(package_dir, organization=dcs).run
    destination = tmp_path / "seed.zip"
    call_command("export_simulation_seed", run.model.key, str(destination))

    out = capsys.readouterr().out
    assert "1 exported" in out and destination.exists()
    call_command(
        "import_simulation_seed", str(destination), "--organization", dcs.slug, "--replace"
    )
    out = capsys.readouterr().out
    assert "tiny_run_v1 [published]" in out
