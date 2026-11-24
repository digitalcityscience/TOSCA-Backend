"""The OGC API – Processes facade (/api/v1/ogcapi/): gates, mappings and the shared run policy."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from tosca_api.apps.geosimulations import ogc_mapping
from tosca_api.apps.geosimulations.models import (
    SimulationOutput,
    SimulationRun,
    SimulationRunner,
    Status,
)
from tosca_api.apps.geosimulations.ogc_api import OgcExecuteThrottle

from .helpers import asset, org, simulation_model, simulation_run
from .test_run_api import QUOTAS

pytestmark = pytest.mark.django_db

BASE = "/api/v1/ogcapi/"
ROLE = "simulation-api"
Execution = SimulationRun.ExecutionState
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["district"],
    "properties": {
        "agents": {"type": "integer", "minimum": 1, "maximum": 5000, "title": "Agents"},
        "district": {"type": "string"},
    },
}
DEFAULTS = {"agents": 100}
READ_ROUTES = ["", "conformance", "processes", "processes/hamburg-bike-abm", "jobs", f"jobs/{'0' * 8}-0000-0000-0000-{'0' * 12}"]


@pytest.fixture(autouse=True)
def settings_on():
    cache.clear()
    with override_settings(
        GEOSIMULATIONS_OGC_API_ENABLED=True,
        GEOSIMULATIONS_RUNS_ENABLED=True,
        GEOSIMULATIONS_QUOTAS=QUOTAS,
    ):
        yield
    cache.clear()


@pytest.fixture
def dcs():
    return org("dcs")


@pytest.fixture
def runner(dcs):
    runner = SimulationRunner(organization=dcs, name="Bike runner", base_url="http://bike-runner:8080")
    runner.set_token("runner-secret")
    runner.save()
    return runner


@pytest.fixture
def model(dcs, runner):
    return simulation_model(
        dcs,
        status=Status.PUBLISHED,
        runner=runner,
        runner_version="0.2.0",
        summary="Bike agents",
        parameter_schema=SCHEMA,
        default_parameters=DEFAULTS,
    )


def _client(django_user_model, username, *, roles=(ROLE,), org_slug=None):
    user = django_user_model.objects.create_user(username=username)
    client = APIClient()
    client.force_authenticate(
        user=user, token={"realm_access": {"roles": list(roles)}, "default_organization": org_slug}
    )
    return client, user


def _execute(client, key="hamburg-bike-abm", **body):
    body.setdefault("inputs", {"district": "Altona"})
    return client.post(f"{BASE}processes/{key}/execution", body, format="json")


def _finish(job_id, state=Execution.SUCCEEDED):
    SimulationRun.objects.filter(pk=job_id).update(execution_state=state)
    return SimulationRun.objects.get(pk=job_id)


def _output(run, dcs, role="bike_flow", mime="application/vnd.pmtiles", order=0):
    return SimulationOutput.objects.create(
        run=run,
        role=role,
        kind=SimulationOutput.Kind.VECTOR_LAYER,
        title=role,
        display_order=order,
        asset=asset(dcs, run=run, path=f"geosimulations/dcs/{run.pk}/{role}.bin", mime=mime),
    )


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", READ_ROUTES)
def test_everything_is_404_while_the_facade_is_off(path, model, django_user_model):
    client, _ = _client(django_user_model, "partner")
    with override_settings(GEOSIMULATIONS_OGC_API_ENABLED=False):
        for caller in (client, APIClient()):
            response = caller.get(BASE + path)
            assert response.status_code == 404
            assert response.json()["status"] == 404
    with override_settings(GEOSIMULATIONS_OGC_API_ENABLED=False):
        assert client.post(f"{BASE}processes/hamburg-bike-abm/execution", {}, format="json").status_code == 404


@pytest.mark.parametrize("path", READ_ROUTES)
def test_anonymous_callers_get_401_even_when_anonymous_runs_are_allowed(path, model):
    with override_settings(GEOSIMULATIONS_ALLOW_ANONYMOUS_RUNS=True):
        response = APIClient().get(BASE + path)
        posted = APIClient().post(f"{BASE}processes/hamburg-bike-abm/execution", {}, format="json")
    assert response.status_code == posted.status_code == 401
    assert response["WWW-Authenticate"].startswith("Bearer")
    assert response.json()["status"] == 401


def test_session_and_drf_tokens_do_not_authenticate(model, django_user_model):
    user = django_user_model.objects.create_user(username="admin-user")
    client = APIClient()
    client.force_login(user)
    assert client.get(BASE + "processes").status_code == 401
    client.get(BASE + "processes", HTTP_AUTHORIZATION="Token abc")
    assert client.get(BASE + "processes", HTTP_AUTHORIZATION="Token abc").status_code == 401


@pytest.mark.parametrize("path", READ_ROUTES)
def test_the_api_role_is_required(path, model, django_user_model):
    client, _ = _client(django_user_model, "no-role", roles=("ROLE_DCS_ADMIN", "DJANGO_SUPERADMIN"), org_slug="dcs")

    response = client.get(BASE + path)

    assert response.status_code == 403
    assert response.json()["status"] == 403
    assert client.post(f"{BASE}processes/hamburg-bike-abm/execution", {}, format="json").status_code == 403


def test_the_role_name_is_configurable(model, django_user_model):
    client, _ = _client(django_user_model, "partner", roles=("custom-role",))
    assert client.get(BASE + "processes").status_code == 403
    with override_settings(GEOSIMULATIONS_OGC_API_ROLE="custom-role"):
        assert client.get(BASE + "processes").status_code == 200


# ---------------------------------------------------------------------------
# Landing page, conformance, processes
# ---------------------------------------------------------------------------


def test_landing_page_and_conformance(django_user_model):
    client, _ = _client(django_user_model, "partner")

    landing = client.get(BASE).json()
    rels = {item["rel"]: item["href"] for item in landing["links"]}
    assert set(rels) == {
        "self",
        "service-desc",
        "http://www.opengis.net/def/rel/ogc/1.0/conformance",
        "http://www.opengis.net/def/rel/ogc/1.0/processes",
        "http://www.opengis.net/def/rel/ogc/1.0/job-list",
    }
    assert rels["http://www.opengis.net/def/rel/ogc/1.0/processes"].endswith("/api/v1/ogcapi/processes")

    conformance = client.get(BASE + "conformance").json()["conformsTo"]
    prefix = "http://www.opengis.net/spec/ogcapi-processes-1/1.0/conf/"
    assert conformance == [prefix + name for name in ("core", "ogc-process-description", "json", "oas30", "job-list")]


def test_process_list_shows_only_models_the_caller_may_run(model, dcs, runner, django_user_model):
    simulation_model(dcs, key="editors-only", status=Status.PUBLISHED, runner=runner, run_access="editors")
    simulation_model(dcs, key="no-runner", status=Status.PUBLISHED)
    simulation_model(dcs, key="draft", status=Status.DRAFT, runner=runner)
    org_only = simulation_model(
        dcs, key="members-only", status=Status.PUBLISHED, runner=runner, run_access="organization"
    )
    outsider, _ = _client(django_user_model, "outsider")
    member, _ = _client(django_user_model, "member", roles=(ROLE, "ROLE_DCS_READER"), org_slug="dcs")

    outsider_ids = [item["id"] for item in outsider.get(BASE + "processes").json()["processes"]]
    member_ids = [item["id"] for item in member.get(BASE + "processes").json()["processes"]]

    assert outsider_ids == ["hamburg-bike-abm"]
    assert member_ids == ["hamburg-bike-abm", org_only.key]
    summary = outsider.get(BASE + "processes").json()["processes"][0]
    assert summary["version"] == "0.2.0"
    assert summary["jobControlOptions"] == ["async-execute"]
    assert summary["outputTransmission"] == ["reference"]


def test_process_list_pages_with_a_next_link(model, dcs, runner, django_user_model):
    simulation_model(dcs, key="second-model", status=Status.PUBLISHED, runner=runner)
    client, _ = _client(django_user_model, "partner")

    first = client.get(BASE + "processes?limit=1").json()
    assert [item["id"] for item in first["processes"]] == ["hamburg-bike-abm"]
    next_link = next(item["href"] for item in first["links"] if item["rel"] == "next")

    second = client.get(next_link).json()
    assert [item["id"] for item in second["processes"]] == ["second-model"]
    assert all(item["rel"] != "next" for item in second["links"])
    assert client.get(BASE + "processes?limit=0").status_code == 400
    assert client.get(BASE + "processes?limit=abc").status_code == 400


def test_runs_switched_off_hides_the_processes(model, django_user_model):
    client, _ = _client(django_user_model, "partner")
    with override_settings(GEOSIMULATIONS_RUNS_ENABLED=False):
        assert client.get(BASE + "processes").json()["processes"] == []
        assert client.get(BASE + "processes/hamburg-bike-abm").status_code == 404
        assert _execute(client).status_code == 404


def test_process_description(model, dcs, django_user_model):
    client, _ = _client(django_user_model, "partner")
    run = simulation_run(model, key="done", status=Status.PUBLISHED, execution_state=Execution.SUCCEEDED)
    _output(run, dcs, role="bike_flow")

    response = client.get(BASE + "processes/hamburg-bike-abm")

    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["version"], body["title"]) == ("hamburg-bike-abm", "0.2.0", "Hamburg bike simulation")
    assert body["jobControlOptions"] == ["async-execute"]
    assert body["outputTransmission"] == ["reference"]
    inputs = body["inputs"]
    assert inputs["district"] == {"title": "district", "schema": {"type": "string"}}  # required: no minOccurs
    assert inputs["agents"]["minOccurs"] == 0  # a default makes it optional
    assert inputs["agents"]["schema"]["default"] == 100 and inputs["agents"]["schema"]["maximum"] == 5000
    for name in ("tosca_seed", "tosca_title", "tosca_visibility"):
        assert inputs[name]["minOccurs"] == 0
    assert inputs["tosca_visibility"]["schema"]["enum"] == ["private", "organization"]
    assert set(body["outputs"]) == {"bike_flow"}
    assert body["outputs"]["bike_flow"]["schema"]["contentMediaType"] == "application/vnd.pmtiles"


def test_optional_properties_carry_min_occurs_zero(model):
    model.default_parameters = {}
    model.parameter_schema = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}, "b": {"type": "string"}}}

    inputs = ogc_mapping.process_inputs(model)

    assert "minOccurs" not in inputs["a"] and inputs["b"]["minOccurs"] == 0


def test_unknown_or_unrunnable_processes_are_404(model, dcs, runner, django_user_model):
    simulation_model(dcs, key="editors-only", status=Status.PUBLISHED, runner=runner, run_access="editors")
    client, _ = _client(django_user_model, "partner")

    missing = client.get(BASE + "processes/nope")
    assert missing.status_code == 404
    assert missing.json()["type"] == "http://www.opengis.net/def/exceptions/ogcapi-processes-1/1.0/no-such-process"
    assert client.get(BASE + "processes/editors-only").status_code == 404
    assert _execute(client, "nope").status_code == 404
    assert _execute(client, "editors-only").status_code == 403  # visible model, run_access says no


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def test_execute_starts_an_asynchronous_run(model, django_user_model):
    client, user = _client(django_user_model, "partner")

    response = _execute(
        client,
        inputs={
            "district": {"value": "Altona"},
            "agents": 50,
            "tosca_seed": 7,
            "tosca_title": "Altona test",
        },
    )

    assert response.status_code == 201
    body = response.json()
    run = SimulationRun.objects.get(pk=body["jobID"])
    assert response["Location"].endswith(f"/api/v1/ogcapi/jobs/{run.pk}")
    assert (body["status"], body["type"], body["processID"], body["reused"]) == (
        "accepted",
        "process",
        "hamburg-bike-abm",
        False,
    )
    assert body["created"] and body["links"][0]["rel"] == "self"
    assert (run.created_by, run.seed, run.title, run.visibility) == (user, 7, "Altona test", "private")
    assert run.parameters == {"agents": 50, "district": "Altona"}
    assert run.job.state == "pending"


def test_execute_ignores_a_prefer_header_and_always_answers_asynchronously(model, django_user_model):
    client, _ = _client(django_user_model, "partner")
    response = client.post(
        BASE + "processes/hamburg-bike-abm/execution",
        {"inputs": {"district": "Altona"}, "response": "document"},
        format="json",
        HTTP_PREFER="respond-sync",
    )
    assert response.status_code == 201


def test_execute_results_link_carries_the_requested_response_style(model, django_user_model):
    client, _ = _client(django_user_model, "partner")
    rel = "http://www.opengis.net/def/rel/ogc/1.0/results"

    plain = _execute(client).json()
    document = _execute(client, inputs={"district": "Bergedorf"}, response="document").json()

    def href(body):
        (found,) = [item["href"] for item in body["links"] if item["rel"] == rel]
        return found

    assert href(plain).endswith(f"/jobs/{plain['jobID']}/results")
    assert href(document).endswith(f"/jobs/{document['jobID']}/results?response=document")


def test_execute_rejects_bad_requests(model, django_user_model):
    client, _ = _client(django_user_model, "partner")

    by_reference = _execute(client, inputs={"district": {"href": "https://example.org/x"}})
    assert by_reference.status_code == 400 and "href" in by_reference.json()["detail"]
    for inputs in (
        {"district": "x", "tosca_seed": -1},
        {"district": "x", "tosca_seed": True},
        {"district": "x", "tosca_visibility": "public"},
        {"district": "x", "tosca_upload": {"value": {}}},
    ):
        assert _execute(client, inputs=inputs).status_code == 400
    assert _execute(client, inputs=["x"]).status_code == 400
    assert client.post(BASE + "processes/hamburg-bike-abm/execution", "not json", content_type="application/json").status_code == 400

    invalid = _execute(client, inputs={"district": "x", "agents": 9000, "colour": "red"})
    assert invalid.status_code == 400
    assert any("9000" in error for error in invalid.json()["errors"])
    assert _execute(client, inputs={}).status_code == 400  # district is required
    assert not SimulationRun.objects.exists()


def test_execute_without_a_runner_is_409(dcs, django_user_model):
    simulation_model(dcs, key="static-only", status=Status.PUBLISHED)
    client, _ = _client(django_user_model, "partner")

    response = _execute(client, "static-only", inputs={})

    assert response.status_code == 409 and response.json()["status"] == 409


def test_identical_requests_return_the_existing_run(model, django_user_model):
    client, _ = _client(django_user_model, "partner")
    first = _execute(client).json()

    again = _execute(client, inputs={"district": "Altona", "agents": 100})

    assert again.status_code == 201
    assert again.json()["jobID"] == first["jobID"] and again.json()["reused"] is True
    assert again["Location"].endswith(first["jobID"])
    assert SimulationRun.objects.count() == 1


def test_organization_visibility_needs_the_models_organization(model, django_user_model):
    outsider, _ = _client(django_user_model, "outsider")
    member, _ = _client(django_user_model, "member", roles=(ROLE, "ROLE_DCS_READER"), org_slug="dcs")

    refused = _execute(outsider, inputs={"district": "x", "tosca_visibility": "organization"})
    shared = _execute(member, inputs={"district": "x", "tosca_visibility": "organization"})

    assert refused.status_code == 400
    assert shared.status_code == 201
    assert SimulationRun.objects.get().visibility == "organization"


def test_quotas_apply_even_to_a_platform_exempt_caller(model, django_user_model):
    client, _ = _client(django_user_model, "superadmin", roles=(ROLE, "DJANGO_SUPERADMIN"))
    assert _execute(client, inputs={"district": "a"}).status_code == 201
    assert _execute(client, inputs={"district": "b"}).status_code == 201

    blocked = _execute(client, inputs={"district": "c"})

    assert blocked.status_code == 429
    assert int(blocked["Retry-After"]) >= 1
    assert blocked.json()["status"] == 429 and "type" in blocked.json()


def test_exempt_callers_do_not_read_other_peoples_runs(model, django_user_model):
    owner, _ = _client(django_user_model, "owner")
    admin, _ = _client(django_user_model, "superadmin", roles=(ROLE, "DJANGO_SUPERADMIN"))
    job_id = _execute(owner).json()["jobID"]

    assert admin.get(f"{BASE}jobs/{job_id}").status_code == 404
    assert admin.get(f"{BASE}jobs").json()["jobs"] == []


def test_execute_is_throttled_per_user(model, django_user_model):
    client, _ = _client(django_user_model, "partner")
    with patch.dict(OgcExecuteThrottle.THROTTLE_RATES, {"ogc_execute": "2/hour"}):
        assert _execute(client).status_code == 201
        assert _execute(client).status_code == 201
        throttled = _execute(client)
    assert throttled.status_code == 429 and int(throttled["Retry-After"]) > 0
    assert throttled.json()["type"]


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


def test_job_list_shows_only_the_callers_own_runs(model, dcs, django_user_model):
    mine, user = _client(django_user_model, "mine")
    other, _ = _client(django_user_model, "other")
    first = _execute(mine, inputs={"district": "a"}).json()["jobID"]
    second = _execute(mine, inputs={"district": "b"}).json()["jobID"]
    _execute(other, inputs={"district": "c"})
    # A public run of someone else is readable by id but not listed.
    public = simulation_run(model, key="public", status=Status.PUBLISHED, execution_state=Execution.SUCCEEDED)
    _finish(first, Execution.FAILED)

    listed = mine.get(BASE + "jobs").json()

    assert [job["jobID"] for job in listed["jobs"]] == [second, first]  # newest first
    assert mine.get(f"{BASE}jobs/{public.pk}").status_code == 200
    assert [job["jobID"] for job in mine.get(BASE + "jobs?status=failed").json()["jobs"]] == [first]
    assert [job["jobID"] for job in mine.get(BASE + "jobs?status=accepted").json()["jobs"]] == [second]
    assert [job["jobID"] for job in mine.get(BASE + "jobs?processID=hamburg-bike-abm").json()["jobs"]] == [second, first]
    assert mine.get(BASE + "jobs?processID=other-model").json()["jobs"] == []
    assert len(mine.get(BASE + "jobs?type=process").json()["jobs"]) == 2
    assert mine.get(BASE + "jobs?type=other").json()["jobs"] == []
    assert mine.get(BASE + "jobs?status=bogus").status_code == 400

    paged = mine.get(BASE + "jobs?limit=1").json()
    assert [job["jobID"] for job in paged["jobs"]] == [second]
    next_link = next(item["href"] for item in paged["links"] if item["rel"] == "next")
    assert [job["jobID"] for job in mine.get(next_link).json()["jobs"]] == [first]


@pytest.mark.parametrize(
    ("state", "status"),
    [
        (Execution.QUEUED, "accepted"),
        (Execution.RUNNING, "running"),
        (Execution.SUCCEEDED, "successful"),
        (Execution.IMPORTED, "successful"),
        (Execution.FAILED, "failed"),
    ],
)
def test_status_info_maps_the_execution_state(state, status, model, django_user_model):
    client, _ = _client(django_user_model, "partner")
    job_id = _execute(client).json()["jobID"]
    run = _finish(job_id, state)
    run.job.message = "Half way"
    run.job.save()
    SimulationRun.objects.filter(pk=job_id).update(execution_error="boom at http://bike-runner:8080/secret")

    body = client.get(f"{BASE}jobs/{job_id}").json()

    assert (body["jobID"], body["status"], body["type"], body["processID"]) == (
        job_id,
        status,
        "process",
        "hamburg-bike-abm",
    )
    assert {"created", "updated", "links"} <= set(body)
    assert "bike-runner" not in str(body)  # the runner's error text is never exposed
    assert ("results" in str(body["links"])) is (status == "successful")
    if status == "failed":
        assert body["message"] == "The simulation run failed."
    elif status != "successful":
        assert body["message"] == "Half way"


def test_unknown_unreadable_and_malformed_jobs_are_404(model, django_user_model):
    owner, _ = _client(django_user_model, "owner")
    stranger, _ = _client(django_user_model, "stranger")
    job_id = _execute(owner).json()["jobID"]
    _finish(job_id)

    for client, path in (
        (stranger, f"jobs/{job_id}"),  # another user's private run
        (stranger, f"jobs/{job_id}/results"),
        (owner, "jobs/00000000-0000-0000-0000-000000000000"),
        (owner, "jobs/not-a-uuid"),
        (owner, "jobs/not-a-uuid/results"),
    ):
        response = client.get(BASE + path)
        assert response.status_code == 404
        assert response.json() == {
            "type": "http://www.opengis.net/def/exceptions/ogcapi-processes-1/1.0/no-such-job",
            "title": "No such job",
            "status": 404,
            "detail": "The job does not exist.",
        }


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@pytest.fixture
def finished(model, dcs, django_user_model):
    client, _ = _client(django_user_model, "partner")
    job_id = _execute(client).json()["jobID"]
    run = _finish(job_id)
    _output(run, dcs, role="bike_flow", order=0)
    _output(run, dcs, role="preview", mime="image/png", order=1)
    SimulationOutput.objects.create(
        run=run, role="kpis", kind=SimulationOutput.Kind.TABLE, title="KPIs", display_order=2,
        data={"columns": ["a"], "rows": [[1]]},
    )
    return client, run


def test_results_are_not_ready_until_the_run_finishes(model, django_user_model):
    client, _ = _client(django_user_model, "partner")
    job_id = _execute(client).json()["jobID"]

    for state in (Execution.QUEUED, Execution.RUNNING):
        _finish(job_id, state)
        response = client.get(f"{BASE}jobs/{job_id}/results")
        assert response.status_code == 404
        assert response.json()["type"] == "http://www.opengis.net/def/exceptions/ogcapi-processes-1/1.0/result-not-ready"


def test_results_of_a_failed_run_are_an_exception(model, django_user_model):
    client, _ = _client(django_user_model, "partner")
    job_id = _execute(client).json()["jobID"]
    _finish(job_id, Execution.FAILED)

    response = client.get(f"{BASE}jobs/{job_id}/results")

    assert response.status_code == 500
    body = response.json()
    assert body["status"] == 500 and body["type"] and body["title"] == "Job failed"


def test_results_document_lists_expiring_links_only(finished):
    client, run = finished

    response = client.get(f"{BASE}jobs/{run.pk}/results?response=document")

    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["bike_flow", "preview"]  # table outputs have no file
    assert body["preview"]["type"] == "image/png"
    for entry in body.values():
        assert entry["href"].startswith("http") and str(run.pk) in entry["href"]
        assert ("expires" in entry) is ("X-Amz-Signature" in entry["href"])  # presigned links say when they expire
        assert set(entry) <= {"href", "type", "expires"}


def test_results_raw_answers_204_with_link_headers(finished):
    client, run = finished

    default = client.get(f"{BASE}jobs/{run.pk}/results")  # raw is the spec default
    response = client.get(f"{BASE}jobs/{run.pk}/results?response=raw")

    assert default.status_code == 204 and default["Link"] == response["Link"]
    assert response.status_code == 204 and not response.content
    links = response["Link"].split(", ")
    assert len(links) == 2
    assert 'rel="item"; type="application/vnd.pmtiles"; title="bike_flow"' in links[0]
    assert 'type="image/png"; title="preview"' in links[1]
    assert client.get(f"{BASE}jobs/{run.pk}/results?response=xml").status_code == 400


def test_a_reused_public_run_can_be_read_by_another_caller(model, dcs, django_user_model):
    run = simulation_run(model, key="public", status=Status.PUBLISHED, execution_state=Execution.SUCCEEDED)
    _output(run, dcs)
    client, _ = _client(django_user_model, "reader")

    assert client.get(f"{BASE}jobs/{run.pk}").json()["status"] == "successful"
    assert list(client.get(f"{BASE}jobs/{run.pk}/results?response=document").json()) == ["bike_flow"]


def test_a_reused_anonymous_link_run_is_readable_by_id_but_not_listed(model, dcs, django_user_model):
    run = simulation_run(
        model, key="link", status=Status.PUBLISHED, execution_state=Execution.SUCCEEDED,
        visibility=SimulationRun.Visibility.LINK,
    )
    _output(run, dcs)
    client, _ = _client(django_user_model, "reader")

    assert client.get(f"{BASE}jobs/{run.pk}").status_code == 200
    assert client.get(f"{BASE}jobs/{run.pk}/results").status_code == 204
    assert client.get(f"{BASE}jobs").json()["jobs"] == []
