"""Model rules for GeoSimulations (Task 12.1)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError

from tosca_api.apps.geodata_providers.models import Style
from tosca_api.apps.geodata_providers.test_helpers import make_layer
from tosca_api.apps.geosimulations.models import (
    SimulationAsset,
    SimulationOutput,
    SimulationRun,
    Status,
)
from tosca_api.apps.geostories.tests.scene_helpers import assign_style, make_vector_layer

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


def test_vector_output_pins_the_layers_default_style(run, django_user_model):
    user = django_user_model.objects.create_user(username="styler")
    layer = make_vector_layer("dcs-simulations:bike_flow", user=user)

    output = _output(run, layer=layer)

    assert output.style_assignment is not None
    assert output.style_assignment.layer_id == layer.id


def test_vector_output_rejects_a_style_of_another_layer(run, django_user_model):
    user = django_user_model.objects.create_user(username="styler")
    layer = make_vector_layer("dcs-simulations:bike_flow", user=user)
    other = make_vector_layer("dcs-simulations:signal_delay", user=user)
    foreign_style = assign_style(
        other, user, fmt=Style.StyleFormat.MBSTYLE, name="foreign", role="alternate"
    )

    with pytest.raises(ValidationError, match="belong to the selected layer"):
        _output(run, layer=layer, style_assignment=foreign_style)


def test_vector_output_layer_must_belong_to_the_models_organization(django_user_model):
    other_org_model = simulation_model(org("qg2"), key="qg2-model")
    other_org_run = simulation_run(other_org_model)
    layer = make_layer("dcs-simulations:bike_flow")  # workspace defaults to org "dcs"

    with pytest.raises(ValidationError, match="organization"):
        _output(other_org_run, layer=layer)


@pytest.mark.parametrize(
    ("kind", "fields", "message"),
    [
        (SimulationOutput.Kind.VECTOR_LAYER, {}, "needs a layer"),
        (SimulationOutput.Kind.TRIPS, {}, "need an asset"),
        (SimulationOutput.Kind.IMAGE, {}, "need an asset"),
        (SimulationOutput.Kind.TABLE, {}, "rows in data"),
    ],
)
def test_output_kind_requires_its_payload(run, kind, fields, message):
    with pytest.raises(ValidationError, match=message):
        _output(run, kind=kind, **fields)


def test_output_payloads_cannot_be_mixed(run, dcs):
    replay = asset(dcs)
    layer = make_layer("dcs-simulations:mixed")

    with pytest.raises(ValidationError, match="cannot have an asset"):
        _output(run, layer=layer, asset=replay)
    with pytest.raises(ValidationError, match="Only vector layer outputs"):
        _output(run, role="replay", kind=SimulationOutput.Kind.TRIPS, asset=replay, layer=layer)
    with pytest.raises(ValidationError, match="not a layer or asset"):
        _output(run, role="kpis", kind=SimulationOutput.Kind.TABLE, data=[], asset=replay)


def test_output_role_is_unique_per_run(run):
    _output(run, role="kpis", kind=SimulationOutput.Kind.TABLE, data={"columns": [], "rows": []})

    with pytest.raises(ValidationError):
        _output(run, role="kpis", kind=SimulationOutput.Kind.TABLE, data=[])


def test_deleting_a_run_keeps_its_assets(run, dcs):
    replay = asset(dcs)
    _output(run, role="replay", kind=SimulationOutput.Kind.TRIPS, asset=replay)

    run.delete()

    assert SimulationAsset.objects.filter(pk=replay.pk).exists()
    assert not SimulationOutput.objects.exists()


def test_assets_used_by_an_output_are_protected(run, dcs):
    replay = asset(dcs)
    _output(run, role="replay", kind=SimulationOutput.Kind.TRIPS, asset=replay)

    with pytest.raises(ProtectedError):
        replay.delete()


def test_asset_checksum_format(dcs):
    with pytest.raises(ValidationError, match="sha256"):
        asset(dcs, sha256="md5:abc")


def test_layer_usage_counts_each_run_once_and_cascades(model, run):
    layer = make_layer("dcs-simulations:shared")
    second_run = simulation_run(model, key="second")
    _output(run, role="bike_flow", layer=layer)
    _output(run, role="bike_flow_copy", layer=layer)
    _output(second_run, role="bike_flow", layer=layer)

    assert layer.usage_summary()["simulations"] == 2

    layer.delete()
    assert not SimulationOutput.objects.exists()
