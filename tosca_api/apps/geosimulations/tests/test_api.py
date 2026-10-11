"""Public read API (Task 12.5)."""

from __future__ import annotations

from datetime import datetime

import pytest
from django.core.files.storage import storages
from rest_framework.test import APIClient

from tosca_api.apps.geosimulations.models import SimulationOutput, Status

from .helpers import asset, org, simulation_model, simulation_run

pytestmark = pytest.mark.django_db

MODELS = "/api/v1/geosimulations/"
RUNS = "/api/v1/geosimulations/runs/"


def _client(django_user_model, *, org_slug=None, roles=(), username="reader"):
    client = APIClient()
    user = django_user_model.objects.create_user(username=username)
    token = {"realm_access": {"roles": list(roles)}, "default_organization": org_slug}
    client.force_authenticate(user=user, token=token)
    return client


@pytest.fixture
def catalog():
    dcs, qg2 = org("dcs"), org("qg2")
    dcs_public = simulation_model(dcs, key="dcs-public", title="A DCS public", status=Status.PUBLISHED)
    dcs_draft = simulation_model(dcs, key="dcs-draft", title="B DCS draft")
    qg2_public = simulation_model(qg2, key="qg2-public", title="C QG2 public", status=Status.PUBLISHED)
    qg2_draft = simulation_model(qg2, key="qg2-draft", title="D QG2 draft")
    return {
        "dcs_public": dcs_public,
        "dcs_draft": dcs_draft,
        "qg2_public": qg2_public,
        "qg2_draft": qg2_draft,
        "dcs_public_run": simulation_run(dcs_public, key="published", status=Status.PUBLISHED),
        "dcs_draft_run": simulation_run(dcs_public, key="draft"),
        "qg2_public_run": simulation_run(qg2_public, key="published", status=Status.PUBLISHED),
        "qg2_draft_run": simulation_run(qg2_public, key="draft"),
        "qg2_hidden_model_run": simulation_run(qg2_draft, key="published", status=Status.PUBLISHED),
    }


def _keys(response):
    return [item["key"] for item in response.data["results"]]


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


def test_anonymous_readers_see_published_models_and_runs(catalog):
    client = APIClient()

    models = client.get(MODELS)
    runs = client.get(RUNS)

    assert models.status_code == 200
    assert _keys(models) == ["dcs-public", "qg2-public"]
    assert [item["run_count"] for item in models.data["results"]] == [1, 1]
    assert sorted((item["key"], item["status"]) for item in runs.data["results"]) == [
        ("published", "published"),
        ("published", "published"),
    ]
    assert client.get(f"{RUNS}{catalog['qg2_hidden_model_run'].pk}/").status_code == 404
    assert client.get(f"{MODELS}{catalog['dcs_draft'].pk}/").status_code == 404


def test_members_also_see_their_own_organizations_drafts(catalog, django_user_model):
    client = _client(django_user_model, org_slug="dcs", roles=["ROLE_DCS_READER"])

    models = client.get(MODELS)
    detail = client.get(f"{MODELS}{catalog['dcs_public'].pk}/")

    assert _keys(models) == ["dcs-public", "dcs-draft", "qg2-public"]
    assert models.data["results"][0]["run_count"] == 2
    assert [run["key"] for run in detail.data["runs"]] == ["draft", "published"]
    assert client.get(f"{RUNS}{catalog['dcs_draft_run'].pk}/").status_code == 200
    # Another organization's drafts stay invisible, as 404 rather than 403.
    assert client.get(f"{RUNS}{catalog['qg2_draft_run'].pk}/").status_code == 404
    assert client.get(f"{MODELS}{catalog['qg2_draft'].pk}/").status_code == 404


def test_platform_exempt_callers_see_everything(catalog, django_user_model):
    client = _client(django_user_model, roles=["DJANGO_SUPERADMIN"])

    assert len(client.get(MODELS).data["results"]) == 4
    assert len(client.get(RUNS).data["results"]) == 5


def test_runs_can_be_filtered_by_model(catalog):
    response = APIClient().get(RUNS, {"model": str(catalog["qg2_public"].pk)})

    assert [item["id"] for item in response.data["results"]] == [str(catalog["qg2_public_run"].pk)]


def test_the_api_is_read_only(catalog, django_user_model):
    client = _client(django_user_model, roles=["DJANGO_SUPERADMIN"])

    assert client.post(MODELS, {"key": "x"}).status_code == 405
    assert client.delete(f"{RUNS}{catalog['dcs_public_run'].pk}/").status_code == 405


# ---------------------------------------------------------------------------
# Model and run detail
# ---------------------------------------------------------------------------


def test_model_detail(catalog):
    response = APIClient().get(f"{MODELS}{catalog['dcs_public'].pk}/")

    data = response.data
    assert data["organization"] == {"slug": "dcs", "name": "DCS"}
    assert data["description_content"]["blocks"] == []
    assert data["parameter_schema"] is None
    assert data["run_count"] == 1
    assert [run["key"] for run in data["runs"]] == ["published"]
    assert data["runs"][0]["time"] == {
        "start": "2026-09-08T11:00:00+02:00",
        "end": "2026-09-08T14:14:29+02:00",
        "timezone": "Europe/Berlin",
    }


@pytest.fixture
def run_with_outputs(catalog):
    run = catalog["dcs_public_run"]
    dcs = org("dcs")
    run.kpis = {"agents": 1000, "mean_trip_time_min": 44.8}
    run.manifest = {"labels": {"bike": {"cargo": {"en": "Cargo bike", "de": "Lastenrad"}}}}
    run.save()
    base = f"geosimulations/dcs/{run.pk}"
    legend = {"type": "simple", "title": "Flow", "geometry": "LineString", "items": [{"label": "Flow"}]}
    SimulationOutput.objects.create(
        run=run,
        role="bike_flow",
        kind=SimulationOutput.Kind.VECTOR_LAYER,
        title="Cyclists per segment",
        display_order=0,
        asset=asset(dcs, run=run, path=f"{base}/layers/bike_flow.pmtiles", mime="application/vnd.pmtiles"),
        style=[{"id": "bike_flow-line", "type": "line"}],
        legend=legend,
        metadata={
            "source_layer": "bike_flow",
            "geometry_type": "LineString",
            "minzoom": 8,
            "maxzoom": 15,
            "bounds": [9.73, 53.395, 10.325, 53.739],
            "features": 16681,
            "style_hint": "graduated:agent_count",
        },
    )
    SimulationOutput.objects.create(
        run=run,
        role="replay",
        kind=SimulationOutput.Kind.TRIPS,
        title="Agent replay",
        display_order=1,
        asset=asset(dcs, run=run, path=f"{base}/replay/trips.json.gz"),
        metadata={"agents": 1000, "encoding": "tosca-trips", "encoding_version": 1, "simplify_m": 1.0},
    )
    SimulationOutput.objects.create(
        run=run,
        role="kpis",
        kind=SimulationOutput.Kind.TABLE,
        title="Key figures",
        display_order=2,
        data={"columns": ["metric", "value"], "rows": [["agents", 1000]]},
    )
    SimulationOutput.objects.create(
        run=run,
        role="preview",
        kind=SimulationOutput.Kind.IMAGE,
        title="Preview",
        display_order=3,
        asset=asset(dcs, run=run, path=f"{base}/preview.png", mime="image/png"),
    )
    return run


def test_run_detail_returns_outputs_by_kind(run_with_outputs):
    response = APIClient().get(f"{RUNS}{run_with_outputs.pk}/")

    data = response.data
    assert response.status_code == 200
    assert data["model"]["key"] == "dcs-public"
    assert data["kpis"] == {"agents": 1000, "mean_trip_time_min": 44.8}
    assert data["labels"]["bike"]["cargo"]["de"] == "Lastenrad"
    assert data["bounds"] == [9.73, 53.395, 10.325, 53.739]
    assert data["time"]["timezone"] == "Europe/Berlin"
    assert "manifest" not in data and "params_hash" not in data
    layer, replay, table, image = data["outputs"]

    assert {key: layer[key] for key in ("role", "kind", "source_layer", "geometry_type", "minzoom", "maxzoom", "features")} == {
        "role": "bike_flow",
        "kind": "vector_layer",
        "source_layer": "bike_flow",
        "geometry_type": "LineString",
        "minzoom": 8,
        "maxzoom": 15,
        "features": 16681,
    }
    assert layer["style"] == [{"id": "bike_flow-line", "type": "line"}]
    assert layer["legend"]["type"] == "simple"
    assert "layers/bike_flow.pmtiles" in layer["url"]

    assert replay["size"] == 3_043_001
    assert replay["sha256"].startswith("sha256:")
    assert replay["metadata"] == {"agents": 1000, "encoding": "tosca-trips", "encoding_version": 1}
    assert "style" not in replay

    assert table["data"] == {"columns": ["metric", "value"], "rows": [["agents", 1000]]}
    assert "url" not in table

    assert image["mime"] == "image/png"
    assert "preview.png" in image["url"]


def test_file_urls_are_presigned_with_one_expiry_per_response(run_with_outputs):
    data = APIClient().get(f"{RUNS}{run_with_outputs.pk}/").data
    urls = [output for output in data["outputs"] if "url" in output]
    storage = storages["default"]

    expiries = {output["url_expires_at"] for output in urls}
    assert len(expiries) == 1
    if getattr(storage, "querystring_auth", False):
        assert all("Signature=" in output["url"] for output in urls)
        (expiry,) = expiries
        assert datetime.fromisoformat(expiry) > datetime.fromisoformat(data["updated_at"])
    else:
        assert expiries == {None}
        assert all(output["url"].startswith("http") for output in urls)


def test_run_detail_query_count_does_not_grow_with_outputs(run_with_outputs, django_assert_max_num_queries):
    with django_assert_max_num_queries(4):
        response = APIClient().get(f"{RUNS}{run_with_outputs.pk}/")

    assert len(response.data["outputs"]) == 4
