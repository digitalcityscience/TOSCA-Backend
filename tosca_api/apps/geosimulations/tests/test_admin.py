"""Admin behaviour for GeoSimulations (Task 12.1): org-scoped and read-mostly."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tosca_api.apps.geodata_providers.test_helpers import make_layer
from tosca_api.apps.geosimulations.models import (
    SimulationModel,
    SimulationOutput,
    SimulationRun,
    Status,
)
from tosca_api.apps.organizations.models import UserAuthorizationSnapshot

from .helpers import asset, org, simulation_model, simulation_run

User = get_user_model()
pytestmark = pytest.mark.django_db

DESCRIPTION = {"blocks": [{"type": "paragraph", "data": {"text": "Edited in TOSCA."}}]}


def _staff_client(username, org_slug, level="WRITER"):
    org(org_slug)
    user = User.objects.create_user(username=username, password="testpass123", is_staff=True)
    UserAuthorizationSnapshot.objects.create(
        user=user, org_roles={org_slug: level}, default_org=org_slug, synced_at=timezone.now()
    )
    client = Client()
    client.force_login(user)
    return client, user


@pytest.fixture
def catalog():
    own_model = simulation_model(org("dcs"), key="dcs-model")
    other_model = simulation_model(org("qg2"), key="qg2-model")
    return {
        "own_model": own_model,
        "own_run": simulation_run(own_model),
        "own_asset": asset(org("dcs")),
        "other_model": other_model,
        "other_run": simulation_run(other_model),
        "other_asset": asset(org("qg2"), path="geosimulations/other/trips.json.gz"),
    }


def test_staff_only_see_their_own_organization_rows(catalog):
    client, _ = _staff_client("dcs-writer", "dcs")

    for name, own, other in (
        ("simulationmodel", "own_model", "other_model"),
        ("simulationrun", "own_run", "other_run"),
        ("simulationasset", "own_asset", "other_asset"),
    ):
        changelist = client.get(reverse(f"admin:geosimulations_{name}_changelist"))
        assert list(changelist.context["cl"].queryset) == [catalog[own]], name
        own_page = client.get(reverse(f"admin:geosimulations_{name}_change", args=[catalog[own].pk]))
        assert own_page.status_code == 200, name
        other_page = client.get(
            reverse(f"admin:geosimulations_{name}_change", args=[catalog[other].pk])
        )
        assert other_page.status_code == 302, name  # admin redirects out-of-scope objects


def test_runs_and_assets_cannot_be_added_by_hand(catalog):
    client, _ = _staff_client("dcs-admin", "dcs", level="ADMIN")

    assert client.get(reverse("admin:geosimulations_simulationrun_add")).status_code == 403
    assert client.get(reverse("admin:geosimulations_simulationasset_add")).status_code == 403


def test_model_add_derives_organization_and_creator():
    client, user = _staff_client("dcs-writer", "dcs")

    response = client.post(
        reverse("admin:geosimulations_simulationmodel_add"),
        {
            "key": "pedestrian-abm",
            "title": "Pedestrian simulation",
            "summary": "",
            "status": Status.DRAFT,
            "description_content": json.dumps(DESCRIPTION),
            "default_parameters": "{}",
            "runs-TOTAL_FORMS": "0",
            "runs-INITIAL_FORMS": "0",
        },
    )

    assert response.status_code == 302, response.context["adminform"].form.errors
    model = SimulationModel.objects.get(key="pedestrian-abm")
    assert model.organization.slug == "dcs"
    assert model.created_by == user
    assert model.description == "Edited in TOSCA."


def test_model_key_is_read_only_after_creation(catalog):
    client, _ = _staff_client("dcs-writer", "dcs")

    response = client.get(
        reverse("admin:geosimulations_simulationmodel_change", args=[catalog["own_model"].pk])
    )

    assert "key" not in response.context["adminform"].form.fields
    assert "organization" not in response.context["adminform"].form.fields


def test_run_admin_edits_only_authored_fields(catalog):
    client, _ = _staff_client("dcs-writer", "dcs")
    run = catalog["own_run"]
    layer = make_layer("dcs-simulations:bike_flow")
    output = SimulationOutput.objects.create(
        run=run,
        role="bike_flow",
        kind=SimulationOutput.Kind.VECTOR_LAYER,
        title="Cyclists per segment",
        layer=layer,
        metadata={"features": 16681},
    )
    url = reverse("admin:geosimulations_simulationrun_change", args=[run.pk])

    page = client.get(url)
    assert set(page.context["adminform"].form.fields) == {"title", "status", "description_content"}
    assert "16681 features" in page.content.decode()

    response = client.post(
        url,
        {
            "title": "Renamed run",
            "status": Status.PUBLISHED,
            "description_content": json.dumps(DESCRIPTION),
            # Read-only values in the post must be ignored.
            "key": "hijacked",
            "model_version": "9.9.9",
            "outputs-TOTAL_FORMS": "1",
            "outputs-INITIAL_FORMS": "1",
            "outputs-0-id": str(output.pk),
            "outputs-0-run": str(run.pk),
            "outputs-0-display_order": "3",
            "outputs-0-title": "Bike volume",
        },
    )

    assert response.status_code == 302, response.context["adminform"].form.errors
    run.refresh_from_db()
    output.refresh_from_db()
    assert run.title == "Renamed run"
    assert run.status == Status.PUBLISHED
    assert run.key == "turn-queues-n1000-v2"
    assert run.model_version == "0.2.0"
    assert (output.display_order, output.title) == (3, "Bike volume")


def test_reader_can_view_but_not_change_runs(catalog):
    client, _ = _staff_client("dcs-reader", "dcs", level="READER")
    url = reverse("admin:geosimulations_simulationrun_change", args=[catalog["own_run"].pk])

    assert client.get(url).status_code == 200
    assert client.post(url, {"title": "Nope"}).status_code == 403
    assert SimulationRun.objects.get(pk=catalog["own_run"].pk).title != "Nope"
