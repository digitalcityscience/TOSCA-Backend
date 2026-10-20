"""Simulations in GeoStory scenes (GeoSimulations Task 12.9)."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.urls import reverse
from rest_framework.test import APIClient

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.geosimulations.models import SimulationOutput, Status
from tosca_api.apps.geosimulations.tests.helpers import asset, org, simulation_model, simulation_run
from tosca_api.apps.geostories.forms import (
    GeoStoryResultPresentationForm,
    GeoStorySceneSimulationForm,
)
from tosca_api.apps.geostories.models import (
    GeoStory,
    GeoStoryResultPresentation,
    GeoStoryScene,
    GeoStorySceneSimulation,
)

User = get_user_model()
pytestmark = pytest.mark.django_db

DURATION_S = 3 * 3600 + 14 * 60 + 29  # helpers.simulation_run's window


@pytest.fixture
def story():
    user = User.objects.create_user(username="sim-story-author")
    campaign = Campaign.objects.create(title="Campaign", created_by=user)
    return GeoStory.objects.create(
        title="Cycling to the station", campaign=campaign, author=user, status=GeoStory.Status.PUBLISHED
    )


@pytest.fixture
def scene(story):
    return GeoStoryScene.objects.create(geostory=story, title="Morning rush")


@pytest.fixture
def run():
    dcs = org("dcs")
    model = simulation_model(dcs, status=Status.PUBLISHED)
    run = simulation_run(model, status=Status.PUBLISHED)
    base = f"geosimulations/dcs/{run.pk}"
    SimulationOutput.objects.create(
        run=run, role="bike_flow", kind=SimulationOutput.Kind.VECTOR_LAYER, title="Cyclists per segment",
        asset=asset(dcs, run=run, path=f"{base}/layers/bike_flow.pmtiles", mime="application/vnd.pmtiles"),
        style=[{"id": "bike_flow-line", "type": "line"}],
        legend={"type": "simple", "title": "Flow", "geometry": "LineString", "items": [{"label": "Flow"}]},
        metadata={"source_layer": "bike_flow", "geometry_type": "LineString", "minzoom": 8, "maxzoom": 15},
    )
    SimulationOutput.objects.create(
        run=run, role="origins", kind=SimulationOutput.Kind.VECTOR_LAYER, title="Trip origins", display_order=1,
        asset=asset(dcs, run=run, path=f"{base}/layers/origins.pmtiles"),
        style=[{"id": "origins-circle", "type": "circle"}],
    )
    SimulationOutput.objects.create(
        run=run, role="replay", kind=SimulationOutput.Kind.TRIPS, title="Agent replay", display_order=2,
        asset=asset(dcs, run=run, path=f"{base}/replay/trips.json.gz"),
        metadata={"agents": 1000, "encoding": "tosca-trips", "encoding_version": 1},
    )
    SimulationOutput.objects.create(
        run=run, role="kpis", kind=SimulationOutput.Kind.TABLE, title="Key figures", display_order=3,
        data={"columns": ["metric", "value"], "rows": [["agents", 1000]]},
    )
    return run


def _output(run, role):
    return run.outputs.get(role=role)


def _simulation(scene, run, **fields):
    simulation = GeoStorySceneSimulation.objects.create(scene=scene, run=run, **fields)
    simulation.map_outputs.set([_output(run, "bike_flow")])
    return simulation


# --- Model -----------------------------------------------------------------


def test_a_valid_scene_simulation(scene, run):
    simulation = _simulation(
        scene, run, display_order=2, replay_start_s=2400, replay_end_s=3300, playback_rate=30
    )

    assert scene.simulation == simulation
    assert simulation.display_order == 2
    assert list(simulation.map_outputs.all()) == [_output(run, "bike_flow")]


def test_only_published_runs_of_published_models(scene, run):
    run.status = Status.DRAFT
    run.save()

    with pytest.raises(ValidationError, match="Only published runs"):
        _simulation(scene, run)


@pytest.mark.parametrize(
    ("start", "end"),
    [(3300, 2400), (0, DURATION_S + 1), (DURATION_S, None)],
)
def test_replay_window_must_fit_the_run(scene, run, start, end):
    with pytest.raises(ValidationError, match="replay window"):
        _simulation(scene, run, replay_start_s=start, replay_end_s=end)


def test_replay_needs_a_trips_output(scene, run):
    _output(run, "replay").delete()

    with pytest.raises(ValidationError, match="no replay"):
        _simulation(scene, run)
    assert _simulation(scene, run, show_replay=False).pk


def test_runs_shown_in_a_story_cannot_be_deleted(scene, run):
    _simulation(scene, run)

    with pytest.raises(ProtectedError):
        run.delete()


# --- Admin form ------------------------------------------------------------


def _form(scene, run, outputs, **data):
    return GeoStorySceneSimulationForm(
        data={
            "run": str(run.pk),
            "map_outputs": [str(output.pk) for output in outputs],
            "display_order": "0",
            "show_replay": "on",
            "loop": "on",
            "playback_rate": "60",
            **data,
        },
        instance=GeoStorySceneSimulation(scene=scene),
    )


def test_form_offers_only_map_layers_of_published_runs(scene, run):
    draft = simulation_run(run.model, key="draft-run")
    SimulationOutput.objects.create(
        run=draft, role="bike_flow", kind=SimulationOutput.Kind.VECTOR_LAYER, title="Draft flow",
        asset=asset(org("dcs"), run=draft, path="geosimulations/dcs/draft/bike_flow.pmtiles"),
    )
    empty_form = GeoStorySceneSimulationForm(instance=GeoStorySceneSimulation(scene=scene))
    form = _form(scene, run, [])

    assert not empty_form.fields["map_outputs"].queryset.exists()
    assert set(form.fields["map_outputs"].queryset.values_list("role", flat=True)) == {"bike_flow", "origins"}
    assert list(form.fields["run"].queryset) == [run]


def test_form_rejects_map_layers_of_another_run(scene, run):
    other = simulation_run(run.model, key="other-run", status=Status.PUBLISHED)
    foreign = SimulationOutput.objects.create(
        run=other, role="bike_flow", kind=SimulationOutput.Kind.VECTOR_LAYER, title="Other flow",
        asset=asset(org("dcs"), run=other, path="geosimulations/dcs/other/bike_flow.pmtiles"),
    )

    form = _form(scene, run, [_output(run, "bike_flow"), foreign])

    assert not form.is_valid()
    assert "valid choice" in str(form.errors["map_outputs"])
    assert _form(scene, run, [_output(run, "bike_flow")]).is_valid()


# --- Public API ------------------------------------------------------------


def _scene_payload(story):
    response = APIClient().get(f"/api/v1/stories/{story.pk}/")
    assert response.status_code == 200
    return response.data["scenes"][0]


def test_story_detail_expands_a_structured_result_block(story, run):
    presentation = GeoStoryResultPresentation.objects.create(
        story=story,
        output=_output(run, "kpis"),
        caption="At a glance",
        view_type=GeoStoryResultPresentation.ViewType.METRIC_GRID,
    )
    story.content = {
        "blocks": [
            {
                "type": "simulationResult",
                "data": {"presentation_id": str(presentation.pk)},
            }
        ]
    }
    story.save()

    response = APIClient().get(f"/api/v1/stories/{story.pk}/")

    assert response.status_code == 200
    block = response.data["content"]["blocks"][0]
    assert block["data"]["presentation_id"] == str(presentation.pk)
    result = block["data"]["presentation"]
    assert result["caption"] == "At a glance"
    assert result["view"] == {"type": "metric_grid", "version": 1}
    assert result["source"]["type"] == "run"
    assert result["output"]["data"]["profile"] == "metric_set"


def test_result_presentation_validates_its_view_and_protects_the_output(story, run):
    output = _output(run, "kpis")
    with pytest.raises(ValidationError, match="x-axis"):
        GeoStoryResultPresentation.objects.create(
            story=story,
            output=output,
            view_type=GeoStoryResultPresentation.ViewType.LINE_CHART,
            configuration={"x": "metric", "series": [{"field": "value"}]},
        )

    GeoStoryResultPresentation.objects.create(
        story=story,
        output=output,
        view_type=GeoStoryResultPresentation.ViewType.METRIC_GRID,
    )
    with pytest.raises(ProtectedError):
        output.delete()

    with pytest.raises(ValidationError, match="must not redefine"):
        GeoStoryResultPresentation.objects.create(
            story=story,
            output=output,
            view_type=GeoStoryResultPresentation.ViewType.TABLE,
            configuration={"type": "line_chart"},
        )


def test_result_form_builds_chart_configuration_without_json(story, run):
    form = GeoStoryResultPresentationForm(
        data={
            "story": story.pk,
            "output": _output(run, "kpis").pk,
            "caption": "Agents over value",
            "view_type": GeoStoryResultPresentation.ViewType.LINE_CHART,
            "x_field": "value",
            "series_fields": ["value"],
        }
    )

    assert "configuration" not in form.fields
    assert "caption" in form.fields
    assert "title" not in form.fields
    assert form.is_valid(), form.errors
    presentation = form.save()
    assert presentation.configuration == {
        "x": "value",
        "series": [{"field": "value"}],
    }


def test_result_schema_endpoint_describes_the_selected_output(admin_client, run):
    response = admin_client.get(
        reverse("admin:geostories_geostoryresultpresentation_output_schema"),
        {"output": _output(run, "kpis").pk},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "Run"
    assert response.json()["role"] == "kpis"
    assert response.json()["profile"] == "metric_set"
    assert [field["key"] for field in response.json()["fields"]] == ["metric", "value"]


def test_story_admin_groups_add_actions_with_their_lists(admin_client, story, scene, run):
    GeoStoryResultPresentation.objects.create(
        story=story,
        output=_output(run, "kpis"),
        view_type=GeoStoryResultPresentation.ViewType.METRIC_GRID,
    )

    response = admin_client.get(reverse("admin:geostories_geostory_change", args=[story.pk]))
    html = response.content.decode()

    assert response.status_code == 200
    assert html.count("story-related-collection") >= 2
    assert "Add scene" in html and "Add simulation result" in html
    assert "GeoStory scenes" not in html
    assert "GeoStory simulation results" not in html
    assert "geocontext/editorjs/init_v2.js" in html
    assert "geostories/js/editorjs_simulation_result.js" in html
    assert "data-editorjs-simulation-results" in html


def test_story_detail_carries_the_scene_simulation(story, scene, run):
    _simulation(
        scene, run, display_order=2, replay_start_s=2400, replay_end_s=3300, playback_rate=30
    )

    simulation = _scene_payload(story)["simulation"]

    assert simulation["display_order"] == 2
    assert simulation["run"]["title"] == run.title
    assert simulation["display_order"] == 2
    assert simulation["time"]["start"] == "2026-09-08T11:00:00+02:00"
    assert simulation["bounds"] == run.bounds
    (layer,) = simulation["map_outputs"]
    assert (layer["role"], layer["kind"], layer["source_layer"]) == ("bike_flow", "vector_layer", "bike_flow")
    assert "layers/bike_flow.pmtiles" in layer["url"]
    assert layer["legend"]["type"] == "simple"
    replay = simulation["replay"]
    assert (replay["start_s"], replay["end_s"], replay["playback_rate"], replay["loop"]) == (2400, 3300, 30, True)
    assert replay["output"]["kind"] == "trips"
    assert replay["output"]["url_expires_at"] == layer["url_expires_at"]


def test_whole_run_replay_by_default(story, scene, run):
    _simulation(scene, run)

    replay = _scene_payload(story)["simulation"]["replay"]

    assert (replay["start_s"], replay["end_s"]) == (0, DURATION_S)


def test_scenes_without_a_public_simulation_report_none(story, scene, run):
    assert _scene_payload(story)["simulation"] is None

    _simulation(scene, run)
    run.model.status = Status.DRAFT
    run.model.save()

    assert _scene_payload(story)["simulation"] is None


# --- Admin inline ----------------------------------------------------------


def test_scene_admin_saves_the_simulation_inline(story, run):
    from django.test import Client
    from django.urls import reverse

    from tosca_api.apps.geostories.tests.test_admin_scenes import _scene_post

    admin = User.objects.create_user(username="sim-admin", is_staff=True, is_superuser=True)
    client = Client()
    client.force_login(admin)
    data = _scene_post(story)
    data.update({
        "simulation-TOTAL_FORMS": "1",
        "simulation-0-run": str(run.pk),
        "simulation-0-map_outputs": [str(_output(run, "bike_flow").pk), str(_output(run, "origins").pk)],
        "simulation-0-display_order": "2",
        "simulation-0-show_replay": "on",
        "simulation-0-loop": "on",
        "simulation-0-replay_start_s": "2400",
        "simulation-0-replay_end_s": "3300",
        "simulation-0-playback_rate": "30",
    })

    page = client.get(reverse("admin:geostories_geostoryscene_add"))
    outputs_response = client.get(
        reverse("admin:geostories_geostoryscene_simulation_outputs"),
        {"run": str(run.pk)},
    )
    response = client.post(reverse("admin:geostories_geostoryscene_add"), data)

    assert page.status_code == 200
    assert "Trip origins" not in page.content.decode()
    assert outputs_response.status_code == 200
    output_labels = [output["label"] for output in outputs_response.json()["outputs"]]
    assert len(output_labels) == 2
    assert any("Trip origins" in label and "role: origins" in label for label in output_labels)
    assert response.status_code == 302, response.context["inline_admin_formsets"][1].formset.errors
    simulation = GeoStoryScene.objects.get(geostory=story).simulation
    assert (simulation.run, simulation.display_order, simulation.replay_start_s, simulation.playback_rate) == (
        run,
        2,
        2400,
        30,
    )
    assert set(simulation.map_outputs.values_list("role", flat=True)) == {"bike_flow", "origins"}
