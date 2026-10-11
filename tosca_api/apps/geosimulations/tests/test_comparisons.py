"""Run comparisons (Task 12.10): model, packages, import, storage, API and styles."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.core.files.storage import storages
from django.core.management import call_command
from rest_framework.test import APIClient

from tosca_api.apps.core.models import MediaAsset
from tosca_api.apps.geosimulations import lifecycle
from tosca_api.apps.geosimulations.importer import PackageImportError, import_package
from tosca_api.apps.geosimulations.models import (
    SimulationAsset,
    SimulationComparison,
    SimulationOutput,
    SimulationRun,
    Status,
)
from tosca_api.apps.geosimulations.packages import PackageValidationError, validate_package_dir
from tosca_api.apps.geosimulations.styles import (
    AttributeValues,
    diverging_style,
    diverging_thresholds,
)

from .helpers import asset, org, simulation_model, simulation_run
from .package_helpers import comparison_manifest, write_comparison_package, write_manifest

pytestmark = pytest.mark.django_db

COMPARISONS = "/api/v1/geosimulations/comparisons/"
MODELS = "/api/v1/geosimulations/"


@pytest.fixture
def dcs():
    return org("dcs")


@pytest.fixture
def runs(dcs):
    model = simulation_model(dcs, status=Status.PUBLISHED)
    baseline = simulation_run(
        model, key="baseline_v1", title="Queues", status=Status.PUBLISHED,
        kpis={"agents": 1000, "mean_trip_time_min": 20.0, "zero_metric": 0, "label": "x"},
    )
    candidate = simulation_run(
        model, key="candidate_v1", title="Turn-aware queues", status=Status.PUBLISHED,
        kpis={"agents": 1000, "mean_trip_time_min": 23.0, "zero_metric": 2, "only_here": 5},
    )
    return model, baseline, candidate


def comparison(model, baseline, candidate, **fields) -> SimulationComparison:
    values = {
        "model": model,
        "key": "queues_vs_turn_queues",
        "title": "Queues vs turn-aware queues",
        "baseline_run": baseline,
        "candidate_run": candidate,
    }
    values.update(fields)
    return SimulationComparison.objects.create(**values)


@pytest.fixture
def cleanup(django_capture_on_commit_callbacks):
    """Delete comparisons, then runs, so stored objects are removed too."""
    yield
    with django_capture_on_commit_callbacks(execute=True):
        for item in SimulationComparison.objects.all():
            item.delete()
        for run in SimulationRun.objects.all():
            run.delete()
        SimulationAsset.objects.all().delete()


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def test_kpi_changes_cover_numeric_kpis_both_runs_report(runs):
    model, baseline, candidate = runs

    changes = {item["metric"]: item for item in comparison(model, baseline, candidate).kpi_changes()}

    assert set(changes) == {"agents", "mean_trip_time_min", "zero_metric"}
    assert changes["mean_trip_time_min"] == {
        "metric": "mean_trip_time_min", "baseline": 20.0, "candidate": 23.0, "delta": 3.0, "delta_percent": 15.0,
    }
    assert changes["zero_metric"]["delta_percent"] is None


def test_runs_must_differ_and_belong_to_the_model(runs, dcs):
    model, baseline, candidate = runs
    other_run = simulation_run(simulation_model(dcs, key="other-model"), key="elsewhere")

    with pytest.raises(ValidationError, match="two different runs"):
        comparison(model, baseline, baseline)
    with pytest.raises(ValidationError, match="comparison's model"):
        comparison(model, baseline, other_run)


def test_publishing_needs_both_runs_published(runs):
    model, baseline, candidate = runs
    candidate.status = Status.DRAFT
    candidate.save()

    with pytest.raises(ValidationError, match="Publish both runs first: Turn-aware queues"):
        comparison(model, baseline, candidate, status=Status.PUBLISHED)


def test_runs_used_by_a_comparison_cannot_be_deleted(runs):
    from django.db.models import ProtectedError

    model, baseline, candidate = runs
    comparison(model, baseline, candidate)

    with pytest.raises(ProtectedError):
        baseline.delete()


def test_outputs_belong_to_exactly_one_owner_and_comparisons_have_no_replay(runs, dcs):
    model, baseline, candidate = runs
    item = comparison(model, baseline, candidate)
    file = asset(dcs, comparison=item)

    with pytest.raises(ValidationError, match="Comparisons have no replay"):
        SimulationOutput.objects.create(comparison=item, role="replay", kind="trips", title="Replay", asset=file)
    with pytest.raises(ValidationError, match="exactly one run or comparison"):
        SimulationOutput.objects.create(
            run=baseline, comparison=item, role="table", kind="table", title="Table", data={"columns": [], "rows": []}
        )
    with pytest.raises(ValidationError, match="another run or comparison"):
        SimulationOutput.objects.create(run=baseline, role="preview", kind="image", title="Preview", asset=file)


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------


def test_a_comparison_package_validates(tmp_path):
    write_comparison_package(tmp_path / "package", real_gpkg=False)

    package = validate_package_dir(tmp_path / "package")

    assert package.kind == "comparison"
    assert [output["role"] for output in package.outputs] == ["flow_change", "summary"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda manifest: manifest.update(run={"key": "x"}), "should not be valid"),
        (lambda manifest: manifest.pop("comparison"), "'comparison' is a required property"),
        (lambda manifest: manifest["outputs"][1].update(kind="trips"), "is not one of"),
        (lambda manifest: manifest["comparison"]["candidate"].update(run_key="baseline_v1"), "two different runs"),
    ],
)
def test_comparison_packages_reject_mixed_or_invalid_content(tmp_path, change, message):
    manifest = write_comparison_package(tmp_path / "package", real_gpkg=False)
    change(manifest)
    write_manifest(tmp_path / "package", manifest)

    with pytest.raises(PackageValidationError, match=message):
        validate_package_dir(tmp_path / "package")


# ---------------------------------------------------------------------------
# Import and storage
# ---------------------------------------------------------------------------


def test_import_creates_the_comparison_with_its_own_outputs_and_files(runs, dcs, tmp_path, cleanup):
    model, baseline, candidate = runs
    write_comparison_package(tmp_path / "package")

    result = import_package(tmp_path / "package", organization=dcs)

    item = result.comparison
    assert result.run is None and result.item == item
    assert (item.baseline_run, item.candidate_run, item.status) == (baseline, candidate, Status.DRAFT)
    assert item.manifest["kind"] == "comparison"
    flow, summary = item.outputs.order_by("display_order")
    assert (flow.kind, flow.legend["type"], flow.metadata["source_layer"]) == ("vector_layer", "diverging", "flow_change")
    assert summary.data["columns"] == ["scenario", "mean_trip_time_min"]
    assert item.source_package.comparison == item
    assert all(
        path.startswith(f"geosimulations/dcs/comparisons/{item.pk}/")
        for path in item.assets.values_list("storage_path", flat=True)
    )
    assert storages[flow.asset.storage_alias].exists(flow.asset.storage_path)


def test_import_needs_both_runs_and_published_runs_to_publish(runs, dcs, tmp_path):
    model, baseline, candidate = runs
    write_comparison_package(tmp_path / "missing", comparison_manifest(candidate_key="not-imported"), real_gpkg=False)
    with pytest.raises(PackageImportError, match="candidate run 'not-imported'"):
        import_package(tmp_path / "missing", organization=dcs)

    candidate.status = Status.DRAFT
    candidate.save()
    write_comparison_package(tmp_path / "draft", real_gpkg=False)
    with pytest.raises(PackageImportError, match="publish both runs"):
        import_package(tmp_path / "draft", organization=dcs, publish=True)
    assert not SimulationComparison.objects.exists()


def test_import_refuses_another_organizations_model(runs, tmp_path):
    write_comparison_package(tmp_path / "package", real_gpkg=False)

    with pytest.raises(PackageImportError, match="belongs to another organization"):
        import_package(tmp_path / "package", organization=org("qg2"))


def test_reimport_needs_replace_and_replaced_runs_are_refused(runs, dcs, tmp_path, cleanup):
    write_comparison_package(tmp_path / "package")
    first = import_package(tmp_path / "package", organization=dcs).comparison

    with pytest.raises(PackageImportError, match="import with replace"):
        import_package(tmp_path / "package", organization=dcs)
    second = import_package(tmp_path / "package", organization=dcs, replace=True).comparison

    assert second.pk != first.pk
    assert not SimulationComparison.objects.filter(pk=first.pk).exists()


def test_replacing_a_compared_run_is_refused(runs, dcs, tmp_path, cleanup):
    from .package_helpers import manifest_template, write_package

    model, baseline, candidate = runs
    comparison(model, baseline, candidate)
    manifest = manifest_template()
    manifest["run"]["key"] = "baseline_v1"
    write_package(tmp_path / "run", manifest)

    with pytest.raises(PackageImportError, match="used by comparisons"):
        import_package(tmp_path / "run", organization=dcs, replace=True)


def test_publishing_a_comparison_moves_its_files(runs, dcs, tmp_path, cleanup, django_capture_on_commit_callbacks):
    write_comparison_package(tmp_path / "package")
    item = import_package(tmp_path / "package", organization=dcs).comparison
    assert lifecycle.desired_alias(item) == MediaAsset.StorageAlias.DEFAULT

    item.status = Status.PUBLISHED
    with django_capture_on_commit_callbacks(execute=True):
        item.save()

    aliases = set(SimulationAsset.objects.filter(comparison=item).values_list("storage_alias", flat=True))
    assert aliases == {MediaAsset.StorageAlias.PUBLIC}


def test_the_import_command_reports_a_comparison(runs, dcs, tmp_path, cleanup, capsys):
    write_comparison_package(tmp_path / "package")

    call_command("import_simulation_package", str(tmp_path / "package"), "--organization", "dcs")

    out = capsys.readouterr().out
    assert "Compare: queues_vs_turn_queues [draft]" in out
    assert "baseline_v1 → candidate_v1" in out


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def published(runs, dcs, tmp_path, cleanup, django_capture_on_commit_callbacks):
    model, baseline, candidate = runs
    write_comparison_package(tmp_path / "package")
    with django_capture_on_commit_callbacks(execute=True):
        item = import_package(tmp_path / "package", organization=dcs, publish=True).comparison
    return model, baseline, candidate, item


def test_anonymous_readers_get_the_published_comparison(published):
    model, baseline, candidate, item = published
    client = APIClient()

    listing = client.get(COMPARISONS)
    detail = client.get(f"{COMPARISONS}{item.pk}/")
    model_detail = client.get(f"{MODELS}{model.pk}/")

    assert [entry["key"] for entry in listing.data["results"]] == ["queues_vs_turn_queues"]
    assert listing.data["results"][0]["baseline"] == {"id": str(baseline.pk), "key": "baseline_v1", "title": "Queues"}
    assert detail.status_code == 200
    data = detail.data
    assert data["candidate"]["time"]["timezone"] == "Europe/Berlin"
    assert {change["metric"] for change in data["kpi_changes"]} == {"agents", "mean_trip_time_min", "zero_metric"}
    assert [output["role"] for output in data["outputs"]] == ["flow_change", "summary"]
    assert data["outputs"][0]["url"] and data["outputs"][0]["legend"]["type"] == "diverging"
    assert data["labels"]["bike"]["cargo"]["en"] == "Cargo bike (compared)"
    assert [entry["key"] for entry in model_detail.data["comparisons"]] == ["queues_vs_turn_queues"]


def test_a_comparison_disappears_when_one_run_is_unpublished(published, django_capture_on_commit_callbacks):
    model, baseline, candidate, item = published
    candidate.status = Status.DRAFT
    with django_capture_on_commit_callbacks(execute=True):
        candidate.save()
    client = APIClient()

    assert client.get(COMPARISONS).data["results"] == []
    assert client.get(f"{COMPARISONS}{item.pk}/").status_code == 404
    assert client.get(f"{MODELS}{model.pk}/").data["comparisons"] == []


def test_comparisons_filter_by_model_and_run(published):
    model, baseline, candidate, item = published
    client = APIClient()

    assert len(client.get(COMPARISONS, {"run": str(baseline.pk)}).data["results"]) == 1
    assert len(client.get(COMPARISONS, {"run": str(candidate.pk)}).data["results"]) == 1
    assert len(client.get(COMPARISONS, {"model": str(model.pk)}).data["results"]) == 1
    other = simulation_run(model, key="unrelated", status=Status.PUBLISHED)
    assert client.get(COMPARISONS, {"run": str(other.pk)}).data["results"] == []


# ---------------------------------------------------------------------------
# Diverging style
# ---------------------------------------------------------------------------


def test_diverging_thresholds_are_rounded_and_symmetric():
    values = [-120, -40, -8, -3, 0, 2, 5, 9, 30, 300]

    small, large = diverging_thresholds(values)

    # Median change 9 → 10; top tenth 156 → 200 (1-2-5 rounding).
    assert (small, large) == (10, 200)
    assert diverging_thresholds([0, 0]) is None


def test_diverging_style_colours_decreases_blue_and_increases_red():
    result = diverging_style("flow_change", "Change", "LineString", AttributeValues("delta", [-120, -8, 2, 9, 300], True))

    color = result.style[0]["paint"]["line-color"]
    assert color[:3] == ["step", ["to-number", ["get", "delta"], 0], "#2166ac"]
    assert [item["label"] for item in result.legend["items"]] == [
        "< −200", "−200 – −10", "−10 – +10", "+10 – +200", "≥ +200",
    ]
    assert result.legend["type"] == "diverging"


def test_skip_existing_leaves_runs_and_comparisons_untouched(runs, dcs, tmp_path, cleanup):
    from .package_helpers import manifest_template, write_package

    write_comparison_package(tmp_path / "comparison")
    first = import_package(tmp_path / "comparison", organization=dcs).comparison
    manifest = manifest_template()
    manifest["run"]["key"] = "baseline_v1"
    write_package(tmp_path / "run", manifest)

    skipped_comparison = import_package(tmp_path / "comparison", organization=dcs, skip_existing=True)
    skipped_run = import_package(tmp_path / "run", organization=dcs, skip_existing=True)

    assert skipped_comparison.skipped and skipped_comparison.comparison == first
    assert skipped_run.skipped and skipped_run.run.key == "baseline_v1"
    assert SimulationComparison.objects.count() == 1
