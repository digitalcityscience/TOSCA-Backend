"""Reader visibility querysets (Task 12.1; the public API in Task 12.5 uses them)."""

from __future__ import annotations

import pytest

from tosca_api.apps.geosimulations.models import SimulationModel, SimulationRun, Status

from .helpers import org, simulation_model, simulation_run

pytestmark = pytest.mark.django_db


@pytest.fixture
def catalog():
    dcs, qg2 = org("dcs"), org("qg2")
    dcs_public = simulation_model(dcs, key="dcs-public", status=Status.PUBLISHED)
    dcs_draft = simulation_model(dcs, key="dcs-draft")
    qg2_public = simulation_model(qg2, key="qg2-public", status=Status.PUBLISHED)
    qg2_draft = simulation_model(qg2, key="qg2-draft")
    return {
        "models": {
            "dcs-public": dcs_public,
            "dcs-draft": dcs_draft,
            "qg2-public": qg2_public,
            "qg2-draft": qg2_draft,
        },
        "runs": {
            "dcs-public/published": simulation_run(dcs_public, key="published", status=Status.PUBLISHED),
            "dcs-public/draft": simulation_run(dcs_public, key="draft"),
            "dcs-draft/published": simulation_run(dcs_draft, key="published", status=Status.PUBLISHED),
            "qg2-public/published": simulation_run(qg2_public, key="published", status=Status.PUBLISHED),
            "qg2-public/archived": simulation_run(qg2_public, key="archived", status=Status.ARCHIVED),
            "qg2-draft/published": simulation_run(qg2_draft, key="published", status=Status.PUBLISHED),
        },
    }


def _model_keys(queryset):
    return set(queryset.values_list("key", flat=True))


def _run_keys(queryset):
    return {f"{run.model.key}/{run.key}" for run in queryset.select_related("model")}


def test_anonymous_readers_see_only_published_models_and_runs(catalog):
    assert _model_keys(SimulationModel.objects.published()) == {"dcs-public", "qg2-public"}
    # A published run under a draft model stays hidden.
    assert _run_keys(SimulationRun.objects.published()) == {
        "dcs-public/published",
        "qg2-public/published",
    }
    assert _run_keys(SimulationRun.objects.readable_by(org_slug=None)) == _run_keys(
        SimulationRun.objects.published()
    )


def test_members_also_see_their_own_organizations_drafts(catalog):
    assert _model_keys(SimulationModel.objects.readable_by(org_slug="dcs")) == {
        "dcs-public",
        "dcs-draft",
        "qg2-public",
    }
    assert _run_keys(SimulationRun.objects.readable_by(org_slug="dcs")) == {
        "dcs-public/published",
        "dcs-public/draft",
        "dcs-draft/published",
        "qg2-public/published",
    }


def test_other_organizations_drafts_and_archives_never_leak(catalog):
    visible = _run_keys(SimulationRun.objects.readable_by(org_slug="qg2"))

    assert "dcs-public/draft" not in visible
    assert "dcs-draft/published" not in visible
    assert {"qg2-public/archived", "qg2-draft/published"} <= visible


def test_platform_exempt_callers_see_everything(catalog):
    assert SimulationModel.objects.readable_by(org_slug=None, exempt=True).count() == 4
    assert SimulationRun.objects.readable_by(org_slug=None, exempt=True).count() == 6
