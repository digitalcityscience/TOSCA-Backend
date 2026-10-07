"""Source index load ("Load catalog"): background run, admin button/action, command."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.management import CommandError, call_command
from django.urls import reverse
from django.utils import timezone

from tosca_api.apps.external_catalog import harvest, remote
from tosca_api.apps.external_catalog.models import CategoryItem, ExternalService, ServiceSource
from tosca_api.apps.external_catalog.tests.test_admin import _category, _org, _service, _source, _staff_client


def _row(dataset_id, source_id, **fields):
    row = {
        "dataset_id": dataset_id,
        "dataset_title": dataset_id.title(),
        "source_id": source_id,
        "title": source_id.title(),
        "description": "",
        "item_type": "feature",
        "geojson": True,
        "count": 1,
        "themes": [],
    }
    row.update(fields)
    return row


ROWS = [_row("kept", "c", title="Renamed", themes=["TRAN"]), _row("new", "c")]


def _catalog_url(service):
    return reverse("admin:external_catalog_externalservice_catalog", args=[service.pk])


@pytest.fixture
def indexed(monkeypatch):
    """Fake remote indexers; background threads run inline."""
    calls = []

    def fake(service):
        calls.append(service.slug)
        return ROWS, []

    monkeypatch.setattr(remote, "index_ogc_sources", fake)
    monkeypatch.setattr(remote, "index_sta_sources", fake)
    monkeypatch.setattr(harvest, "_spawn", harvest.run_by_pk)
    return calls


@pytest.mark.django_db
def test_run_replaces_the_index_and_refreshes_item_titles(indexed):
    _, user = _staff_client("writer-a", "org-a")
    ogc = _service(_org("org-a"), user, "ogc")
    _source(ogc, "gone", "c")
    _source(ogc, "kept", "c", title="Old")
    item = CategoryItem.objects.create(
        category=_category(_org("org-a"), user, "cat"),
        service=ogc,
        title="Old",
        ogc_dataset_id="kept",
        ogc_collection_id="c",
        created_by=user,
    )

    assert harvest.claim(ogc)
    harvest.run(ogc)

    rows = {(row.dataset_id, row.source_id): (row.title, row.themes) for row in ServiceSource.objects.all()}
    assert rows == {("kept", "c"): ("Renamed", ["TRAN"]), ("new", "c"): ("C", [])}
    item.refresh_from_db()
    assert (item.title, item.dataset_title) == ("Renamed", "Kept")
    ogc.refresh_from_db()
    assert harvest.state(ogc)["state"] == "done"


@pytest.mark.django_db
def test_partial_problems_are_kept_as_a_note(monkeypatch):
    _, user = _staff_client("writer-a", "org-a")
    ogc = _service(_org("org-a"), user, "ogc")
    problems = [f"Dataset {index}: The service answered HTTP 503." for index in range(7)]
    monkeypatch.setattr(remote, "index_ogc_sources", lambda service: ([_row("d", "c")], problems))

    harvest.claim(ogc)
    harvest.run(ogc)

    ogc.refresh_from_db()
    state = harvest.state(ogc)
    assert state["state"] == "done"
    assert state["note"].startswith("7 dataset(s) could not be read: Dataset 0")
    assert state["note"].endswith("(and 2 more)")


@pytest.mark.django_db
def test_claim_allows_one_run_at_a_time_and_recovers_stale_runs():
    _, user = _staff_client("writer-a", "org-a")
    ogc = _service(_org("org-a"), user, "ogc")

    assert harvest.claim(ogc) is True
    assert harvest.claim(ogc) is False
    assert harvest.state(ogc)["state"] == "running"

    ExternalService.objects.filter(pk=ogc.pk).update(
        catalog_load_started_at=timezone.now() - harvest.STALE_AFTER - timedelta(minutes=1)
    )
    ogc.refresh_from_db()
    assert harvest.state(ogc)["state"] == "never"
    assert harvest.claim(ogc) is True


@pytest.mark.django_db
def test_failed_run_is_recorded_and_can_be_retried(monkeypatch):
    _, user = _staff_client("writer-a", "org-a")
    ogc = _service(_org("org-a"), user, "ogc")

    def down(service):
        raise remote.RemoteServiceError("The service answered HTTP 503.")

    monkeypatch.setattr(remote, "index_ogc_sources", down)
    harvest.claim(ogc)
    with pytest.raises(remote.RemoteServiceError):
        harvest.run(ogc)

    ogc.refresh_from_db()
    assert harvest.state(ogc) | {"started_at": None} == {
        "state": "failed",
        "started_at": None,
        "loaded_at": None,
        "error": "The service answered HTTP 503.",
        "note": None,
    }
    assert harvest.claim(ogc) is True


@pytest.mark.django_db
def test_load_button_starts_the_load_in_background(indexed, django_capture_on_commit_callbacks):
    client, user = _staff_client("writer-a", "org-a")
    sta = _service(_org("org-a"), user, "sta", service_type=ExternalService.ServiceType.SENSORTHINGS)

    assert client.get(_catalog_url(sta)).json()["state"] == "never"
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(_catalog_url(sta))

    assert response.status_code == 202
    assert response.json()["started"] is True
    assert indexed == ["sta"]
    assert client.get(_catalog_url(sta)).json()["state"] == "done"
    assert ServiceSource.objects.filter(service=sta).count() == 2


@pytest.mark.django_db
def test_load_button_needs_change_permission(indexed):
    reader, user = _staff_client("reader-a", "org-a", level="READER")
    ogc = _service(_org("org-a"), user, "ogc")

    assert reader.get(_catalog_url(ogc)).status_code == 200
    assert reader.post(_catalog_url(ogc)).status_code == 403
    assert indexed == []


@pytest.mark.django_db
def test_service_list_action_starts_loads(indexed, django_capture_on_commit_callbacks):
    client, user = _staff_client("admin-a", "org-a", level="ADMIN")
    ogc = _service(_org("org-a"), user, "ogc")
    harvest.claim(_service(_org("org-a"), user, "busy"))

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("admin:external_catalog_externalservice_changelist"),
            {"action": "load_catalog", "_selected_action": [str(ogc.pk), str(ExternalService.objects.get(slug="busy").pk)]},
            follow=True,
        )

    messages = [str(message) for message in response.context["messages"]]
    assert any("Loading the catalog of: Ogc" in message for message in messages)
    assert any("1 service(s) skipped" in message for message in messages)
    assert indexed == ["ogc"]


@pytest.mark.django_db
def test_command_loads_active_services(indexed):
    _, user = _staff_client("writer-a", "org-a")
    _service(_org("org-a"), user, "ogc")
    _service(_org("org-a"), user, "off", is_active=False)

    call_command("load_external_catalog")

    assert indexed == ["ogc"]
    with pytest.raises(CommandError, match="nope"):
        call_command("load_external_catalog", service=["nope"])
