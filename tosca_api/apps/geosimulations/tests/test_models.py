"""Model rules for GeoSimulations (Tasks 12.1 and 12.3)."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db.models import RestrictedError

from tosca_api.apps.geosimulations.models import (
    SimulationAsset,
    SimulationOutput,
    SimulationRun,
    Status,
)

from .helpers import START, asset, org, simulation_model, simulation_run

pytestmark = pytest.mark.django_db


@pytest.fixture
def dcs():
    return org("dcs")


@pytest.fixture
def model(dcs):
    return simulation_model(dcs)


@pytest.fixture
def run(model):
    return simulation_run(model)


# ---------------------------------------------------------------------------
# SimulationModel
# ---------------------------------------------------------------------------


def test_model_sanitizes_text_and_projects_description(dcs):
    model = simulation_model(
        dcs,
        title="<script>alert(1)</script>Bike ABM",
        summary="<b>Agents</b> to Hbf",
        description="Cyclists ride to the main station.",
    )

    assert "<script>" not in model.title
    assert "Bike ABM" in model.title
    assert model.description_content["blocks"][0]["data"]["text"] == (
        "Cyclists ride to the main station."
    )
    assert model.description == "Cyclists ride to the main station."
    assert model.status == Status.DRAFT


def test_model_key_is_unique(dcs, model):
    with pytest.raises(ValidationError, match="key"):
        simulation_model(dcs, key=model.key)


def test_model_parameter_schema_must_be_an_object(dcs):
    with pytest.raises(ValidationError, match="Parameter schema"):
        simulation_model(dcs, parameter_schema=["agents"])


# ---------------------------------------------------------------------------
# SimulationRun
# ---------------------------------------------------------------------------


def test_params_hash_is_canonical_and_versioned(model, run):
    reordered = simulation_run(model, key="reordered", parameters={"seed": 42, "agents": 1000})
    other_version = simulation_run(model, key="newer", model_version="0.3.0")

    assert len(run.params_hash) == 64
    assert reordered.params_hash == run.params_hash
    assert other_version.params_hash != run.params_hash
    assert run.params_hash == SimulationRun.compute_params_hash(
        "hamburg-bike-abm", "0.2.0", {"agents": 1000, "seed": 42}
    )


def test_params_hash_follows_parameter_changes(run):
    before = run.params_hash
    run.parameters = {"agents": 500, "seed": 42}
    run.save()

    assert run.params_hash != before


def test_run_key_is_unique_per_model_only(dcs, model, run):
    with pytest.raises(ValidationError):
        simulation_run(model, key=run.key)

    other_model = simulation_model(dcs, key="other-model")
    assert simulation_run(other_model, key=run.key).pk


def test_run_time_window_must_be_complete_and_ordered(model):
    with pytest.raises(ValidationError, match="neither"):
        simulation_run(model, key="half", time_end=None)
    with pytest.raises(ValidationError, match="cannot end before"):
        simulation_run(model, key="backwards", time_end=START - timedelta(seconds=1))


def test_run_duration(run):
    assert run.duration_seconds == 3 * 3600 + 14 * 60 + 29


def test_only_complete_runs_with_a_time_window_can_be_published(model):
    with pytest.raises(ValidationError, match="Only imported or succeeded"):
        simulation_run(
            model,
            key="queued",
            status=Status.PUBLISHED,
            execution_state=SimulationRun.ExecutionState.QUEUED,
        )
    with pytest.raises(ValidationError, match="time window"):
        simulation_run(
            model, key="untimed", status=Status.PUBLISHED, time_start=None, time_end=None
        )

    assert simulation_run(model, key="ok", status=Status.PUBLISHED).status == Status.PUBLISHED


def test_run_timezone_must_be_iana(model):
    with pytest.raises(ValidationError, match="timezone"):
        simulation_run(model, key="tz", timezone="Mars/Olympus")


@pytest.mark.parametrize(
    "bounds",
    [[1, 2, 3], [10.3, 53.4, 9.7, 53.7], [9.7, 53.7, 10.3, 53.4], [0, 0, 200, 1], ["a", 0, 1, 1]],
)
def test_run_bounds_are_validated(model, bounds):
    with pytest.raises(ValidationError, match="ounds"):
        simulation_run(model, key="bad-bounds", bounds=bounds)


def test_deleting_a_model_cascades_runs(model, run):
    model.delete()

    assert not SimulationRun.objects.filter(pk=run.pk).exists()


# ---------------------------------------------------------------------------
# SimulationOutput and SimulationAsset
# ---------------------------------------------------------------------------


def _output(run, role="bike_flow", kind=SimulationOutput.Kind.VECTOR_LAYER, **fields):
    return SimulationOutput.objects.create(
        run=run, role=role, kind=kind, title=role.replace("_", " ").title(), **fields
    )


STYLE = [{"id": "bike_flow-line", "type": "line", "paint": {"line-width": 2}}]


def test_map_layer_output_holds_tiles_and_a_style(run, dcs):
    tiles = asset(dcs, run=run, path="geosimulations/dcs/run/layers/bike_flow.pmtiles")

    output = _output(run, asset=tiles, style=STYLE, metadata={"minzoom": 8})

    assert output.asset == tiles
    assert run.assets.get() == tiles


@pytest.mark.parametrize(
    ("kind", "fields", "message"),
    [
        (SimulationOutput.Kind.VECTOR_LAYER, {}, "need an asset"),
        (SimulationOutput.Kind.TRIPS, {}, "need an asset"),
        (SimulationOutput.Kind.IMAGE, {}, "need an asset"),
        (SimulationOutput.Kind.TABLE, {}, "object dataset"),
    ],
)
def test_output_kind_requires_its_payload(run, kind, fields, message):
    with pytest.raises(ValidationError, match=message):
        _output(run, kind=kind, **fields)


def test_tables_hold_rows_not_files(run, dcs):
    with pytest.raises(ValidationError, match="not an asset"):
        _output(run, role="kpis", kind=SimulationOutput.Kind.TABLE, data=[], asset=asset(dcs))


@pytest.mark.parametrize(
    ("kind", "style", "message"),
    [
        (SimulationOutput.Kind.TRIPS, STYLE, "Only map layer outputs"),
        (SimulationOutput.Kind.VECTOR_LAYER, {"id": "x"}, "list of MapLibre layers"),
        (SimulationOutput.Kind.VECTOR_LAYER, [{"paint": {}}], "list of MapLibre layers"),
    ],
)
def test_style_rules(run, dcs, kind, style, message):
    with pytest.raises(ValidationError, match=message):
        _output(run, kind=kind, asset=asset(dcs), style=style)


def test_output_cannot_use_another_runs_asset(model, run, dcs):
    other_run = simulation_run(model, key="other")

    with pytest.raises(ValidationError, match="another run"):
        _output(run, asset=asset(dcs, run=other_run))


def test_output_role_is_unique_per_run(run):
    _output(run, role="kpis", kind=SimulationOutput.Kind.TABLE, data={"columns": ["metric"], "rows": []})

    with pytest.raises(ValidationError):
        _output(run, role="kpis", kind=SimulationOutput.Kind.TABLE, data=[])


def test_deleting_a_run_removes_outputs_assets_and_stored_files(
    run, dcs, django_capture_on_commit_callbacks
):
    replay = asset(dcs, run=run)
    _output(run, role="replay", kind=SimulationOutput.Kind.TRIPS, asset=replay)
    run.source_package = asset(dcs, run=run, path="geosimulations/dcs/run/package.zip")
    run.save()
    deleted = []
    storage = SimpleNamespace(delete=deleted.append)

    with patch.object(SimulationAsset, "storage", storage):
        with django_capture_on_commit_callbacks(execute=True):
            run.delete()

    assert not SimulationAsset.objects.exists()
    assert not SimulationOutput.objects.exists()
    assert sorted(deleted) == [
        "geosimulations/dcs/run/package.zip",
        "geosimulations/run/replay/trips.json.gz",
    ]


def test_stored_files_survive_a_rolled_back_delete(run, dcs, django_capture_on_commit_callbacks):
    asset(dcs, run=run)
    deleted = []

    with patch.object(SimulationAsset, "storage", SimpleNamespace(delete=deleted.append)):
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            run.delete()

    assert len(callbacks) == 1
    assert deleted == []  # nothing runs unless the transaction commits


def test_assets_used_by_an_output_cannot_be_deleted_alone(run, dcs):
    replay = asset(dcs, run=run)
    _output(run, role="replay", kind=SimulationOutput.Kind.TRIPS, asset=replay)

    with pytest.raises(RestrictedError):
        replay.delete()


def test_asset_checksum_format(dcs):
    with pytest.raises(ValidationError, match="sha256"):
        asset(dcs, sha256="md5:abc")
