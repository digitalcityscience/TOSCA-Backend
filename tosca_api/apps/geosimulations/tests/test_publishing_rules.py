"""Status (is it published?) and visibility (who reads it then), and who may start runs.

* Models: published + ``public`` → anyone; published + ``organization`` → its
  organization. Drafts/archived → its organization.
* Runs and comparisons: published + ``public`` → anyone (model public too),
  published + ``organization`` → the model's organization, ``private`` → only
  the user who started it. Drafts/archived → the model's organization and the
  creator. ``link`` runs → by id only.
* ``run_access``: anyone / signed-in / organization members / editors.
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.test import override_settings
from rest_framework.test import APIClient

from tosca_api.apps.geosimulations.models import SimulationModel, SimulationRun, SimulationRunner, Status

from .helpers import org, simulation_model, simulation_run

pytestmark = pytest.mark.django_db

Visibility = SimulationRun.Visibility
RunAccess = SimulationModel.RunAccess
RUNS = "/api/v1/geosimulations/runs/"
MODELS = "/api/v1/geosimulations/"


def _client(django_user_model, username, *, org_slug=None, level=None):
    user = django_user_model.objects.create_user(username=username)
    roles = [f"ROLE_{org_slug.upper()}_{level}"] if org_slug and level else []
    client = APIClient()
    client.force_authenticate(user=user, token={"realm_access": {"roles": roles}, "default_organization": org_slug})
    return client, user


@pytest.fixture
def readers(django_user_model):
    org("dcs"), org("qg2")
    return {
        "anonymous": (APIClient(), None),
        "member": _client(django_user_model, "member", org_slug="dcs", level="READER"),
        "outsider": _client(django_user_model, "outsider", org_slug="qg2", level="READER"),
        "creator": _client(django_user_model, "creator"),
    }


def _listed(client) -> set[str]:
    return {item["key"] for item in client.get(RUNS).data["results"]}


def test_run_readers_follow_status_and_visibility(readers):
    model = simulation_model(org("dcs"), status=Status.PUBLISHED)
    creator = readers["creator"][1]
    for key, status, visibility in [
        ("pub-public", Status.PUBLISHED, Visibility.PUBLIC),
        ("pub-org", Status.PUBLISHED, Visibility.ORGANIZATION),
        ("pub-private", Status.PUBLISHED, Visibility.PRIVATE),
        ("draft-public", Status.DRAFT, Visibility.PUBLIC),
        ("archived-org", Status.ARCHIVED, Visibility.ORGANIZATION),
        ("draft-private", Status.DRAFT, Visibility.PRIVATE),
        ("draft-link", Status.DRAFT, Visibility.LINK),
    ]:
        simulation_run(model, key=key, status=status, visibility=visibility, created_by=creator)

    everyone = {"pub-public"}
    organization = {"pub-public", "pub-org", "draft-public", "archived-org"}
    assert _listed(readers["anonymous"][0]) == everyone
    assert _listed(readers["outsider"][0]) == everyone
    assert _listed(readers["member"][0]) == organization
    assert _listed(readers["creator"][0]) == {
        "pub-public", "pub-org", "pub-private", "draft-public", "archived-org", "draft-private", "draft-link"
    }


def test_link_runs_open_by_id_but_are_never_listed(readers):
    model = simulation_model(org("dcs"), status=Status.PUBLISHED)
    run = simulation_run(model, key="anonymous", visibility=Visibility.LINK)
    client = readers["outsider"][0]

    assert client.get(f"{RUNS}{run.pk}/").status_code == 200
    assert "anonymous" not in _listed(client)


def test_organization_models_hide_themselves_and_their_public_runs(readers):
    model = simulation_model(org("dcs"), status=Status.PUBLISHED, visibility=SimulationModel.Visibility.ORGANIZATION)
    run = simulation_run(model, key="public-run", status=Status.PUBLISHED, visibility=Visibility.PUBLIC)

    for name in ("anonymous", "outsider"):
        client = readers[name][0]
        assert client.get(f"{MODELS}{model.pk}/").status_code == 404
        assert client.get(f"{RUNS}{run.pk}/").status_code == 404
        assert _listed(client) == set()
    member = readers["member"][0]
    assert member.get(f"{MODELS}{model.pk}/").status_code == 200
    assert _listed(member) == {"public-run"}
    assert not run.is_public


def test_draft_models_are_seen_only_by_their_organization(readers):
    model = simulation_model(org("dcs"))

    assert readers["anonymous"][0].get(f"{MODELS}{model.pk}/").status_code == 404
    assert readers["outsider"][0].get(f"{MODELS}{model.pk}/").status_code == 404
    assert readers["member"][0].get(f"{MODELS}{model.pk}/").status_code == 200


# ---------------------------------------------------------------------------
# Who may start runs
# ---------------------------------------------------------------------------


@pytest.fixture
def runnable(settings):
    settings.GEOSIMULATIONS_RUNS_ENABLED = True
    dcs = org("dcs")
    runner = SimulationRunner(organization=dcs, name="Runner", base_url="http://bike-runner:8080")
    runner.set_token("secret")
    runner.save()
    return simulation_model(
        dcs,
        status=Status.PUBLISHED,
        runner=runner,
        runner_version="0.2.0",
        parameter_schema={"type": "object", "properties": {"agents": {"type": "integer"}}},
        default_parameters={"agents": 10},
    )


def _start(client, model):
    return client.post(f"{MODELS}{model.pk}/runs/", {"parameters": {"agents": 5}}, format="json")


@pytest.mark.parametrize(
    ("access", "expected"),
    [
        (RunAccess.ANYONE, {"anonymous": 202, "signed_in": 202, "outsider": 202, "reader": 202, "writer": 202}),
        (RunAccess.SIGNED_IN, {"anonymous": 401, "signed_in": 202, "outsider": 202, "reader": 202, "writer": 202}),
        (RunAccess.ORGANIZATION, {"anonymous": 401, "signed_in": 403, "outsider": 403, "reader": 202, "writer": 202}),
        (RunAccess.EDITORS, {"anonymous": 401, "signed_in": 403, "outsider": 403, "reader": 403, "writer": 202}),
    ],
)
def test_run_access_decides_who_may_start_runs(runnable, django_user_model, access, expected):
    runnable.run_access = access
    runnable.save()
    org("qg2")
    callers = {
        "anonymous": APIClient(),
        "signed_in": _client(django_user_model, "plain")[0],
        "outsider": _client(django_user_model, "outsider", org_slug="qg2", level="WRITER")[0],
        "reader": _client(django_user_model, "reader", org_slug="dcs", level="READER")[0],
        "writer": _client(django_user_model, "writer", org_slug="dcs", level="WRITER")[0],
    }

    with patch_quota_free():
        answers = {name: _start(client, runnable).status_code for name, client in callers.items()}
        can_run = {name: client.get(f"{MODELS}{runnable.pk}/").data["can_run"] for name, client in callers.items()}

    # 200: an identical run someone started earlier is reused (still allowed to start).
    assert {name: 202 if code == 200 else code for name, code in answers.items()} == expected
    assert can_run == {name: code == 202 for name, code in expected.items()}


def test_anonymous_runs_need_the_setting_even_when_anyone_may_run(runnable):
    runnable.run_access = RunAccess.ANYONE
    runnable.save()

    with override_settings(GEOSIMULATIONS_ALLOW_ANONYMOUS_RUNS=False):
        assert _start(APIClient(), runnable).status_code == 401
        assert APIClient().get(f"{MODELS}{runnable.pk}/").data["can_run"] is False


@pytest.mark.parametrize("visibility", [Visibility.PUBLIC, Visibility.LINK])
def test_users_cannot_start_public_or_link_runs(runnable, django_user_model, visibility):
    client, _ = _client(django_user_model, "plain")
    response = client.post(
        f"{MODELS}{runnable.pk}/runs/", {"parameters": {"agents": 5}, "visibility": visibility}, format="json"
    )

    assert response.status_code == 400 and "visibility" in response.data


def patch_quota_free():
    return override_settings(
        GEOSIMULATIONS_QUOTAS={
            key: 100
            for key in (
                "anonymous_active", "anonymous_daily", "all_anonymous_active", "all_anonymous_daily",
                "user_active", "user_daily", "organization_active", "organization_daily",
            )
        }
    )


# ---------------------------------------------------------------------------
# Comparisons, stories, migration
# ---------------------------------------------------------------------------


def test_public_comparisons_need_public_runs():
    from tosca_api.apps.geosimulations.models import SimulationComparison

    model = simulation_model(org("dcs"), status=Status.PUBLISHED)
    baseline = simulation_run(model, key="a", status=Status.PUBLISHED)
    candidate = simulation_run(model, key="b", status=Status.PUBLISHED, visibility=Visibility.ORGANIZATION)

    with pytest.raises(ValidationError, match="visible to anyone first"):
        SimulationComparison.objects.create(
            model=model, key="cmp", title="Cmp", baseline_run=baseline, candidate_run=candidate,
            status=Status.PUBLISHED, visibility=Visibility.PUBLIC,
        )


def test_stories_only_embed_runs_everyone_reads(django_user_model):
    from tosca_api.apps.campaigns.models import Campaign
    from tosca_api.apps.geostories.models import GeoStory, GeoStoryScene, GeoStorySceneSimulation

    model = simulation_model(org("dcs"), status=Status.PUBLISHED)
    run = simulation_run(model, status=Status.PUBLISHED, visibility=Visibility.ORGANIZATION)
    author = django_user_model.objects.create_user(username="author")
    story = GeoStory.objects.create(
        title="Story", campaign=Campaign.objects.create(title="C", created_by=author), author=author
    )
    scene = GeoStoryScene.objects.create(geostory=story, title="Scene")

    with pytest.raises(ValidationError, match="visible to anyone"):
        GeoStorySceneSimulation.objects.create(scene=scene, run=run)


def test_migration_keeps_published_items_public():
    migration = importlib.import_module(
        "tosca_api.apps.geosimulations.migrations.0009_publishing_visibility_and_run_access"
    )
    model = simulation_model(
        org("dcs"), status=Status.PUBLISHED,
        visibility=SimulationModel.Visibility.ORGANIZATION, run_access=RunAccess.ORGANIZATION,
    )
    draft_model = simulation_model(org("dcs"), key="draft", visibility=SimulationModel.Visibility.ORGANIZATION)
    published = simulation_run(model, key="published", status=Status.PUBLISHED, visibility=Visibility.ORGANIZATION)
    draft = simulation_run(model, key="draft", visibility=Visibility.ORGANIZATION)
    link = simulation_run(model, key="link", status=Status.PUBLISHED, visibility=Visibility.LINK)

    migration.keep_published_items_public(apps, None)

    for item in (model, draft_model, published, draft, link):
        item.refresh_from_db()
    assert (model.visibility, model.run_access) == (SimulationModel.Visibility.PUBLIC, RunAccess.ANYONE)
    assert draft_model.visibility == SimulationModel.Visibility.ORGANIZATION
    assert published.visibility == Visibility.PUBLIC
    assert draft.visibility == Visibility.ORGANIZATION
    assert link.visibility == Visibility.LINK
