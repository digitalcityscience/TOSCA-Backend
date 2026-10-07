"""Item health check (external catalog ticket 05)."""

from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command
from django.urls import reverse

from tosca_api.apps.external_catalog import health
from tosca_api.apps.external_catalog.models import CategoryItem, ExternalService
from tosca_api.apps.external_catalog.tests.test_admin import _category, _org, _service, _source, _staff_client
from tosca_api.apps.external_catalog.tests.test_remote import FakeResponse, http  # noqa: F401 - fixture

State = CategoryItem.AvailabilityState
OGC_ROOT = "https://example.test/api"
LANDING = "https://example.test/api/stadtrad"
STA_ROOT = "https://example.test/sta/v1.1"


@pytest.fixture
def world():
    client, user = _staff_client("writer-a", "org-a")
    org = _org("org-a")
    ogc = _service(org, user, "ogc", base_url=OGC_ROOT)
    sta = _service(org, user, "sta", service_type=ExternalService.ServiceType.SENSORTHINGS, base_url=STA_ROOT)
    category = _category(org, user, "mobility")
    return client, user, ogc, sta, category


def _ogc_item(category, service, collection="stationen", dataset="stadtrad", **fields):
    return CategoryItem.objects.create(
        category=category,
        service=service,
        title=collection,
        ogc_dataset_id=dataset,
        ogc_collection_id=collection,
        created_by=category.created_by,
        **fields,
    )


def _sta_item(category, service, layer="Status", **fields):
    return CategoryItem.objects.create(
        category=category,
        service=service,
        title=layer,
        sta_service_name="HH_STA_E-Ladestationen",
        sta_layer_name=layer,
        created_by=category.created_by,
        **fields,
    )


def _ogc_routes(routes):
    routes[OGC_ROOT] = FakeResponse({"apis": [{"id": "stadtrad", "title": "StadtRAD", "landingPageUri": LANDING}]})
    routes[f"{LANDING}/collections/stationen"] = FakeResponse(
        {"id": "stationen", "links": [{"rel": "items", "type": "application/geo+json", "href": "x"}]}
    )
    routes[f"{LANDING}/collections/stationen/items"] = FakeResponse({"numberMatched": 358, "features": []})
    routes[f"{LANDING}/collections/building"] = FakeResponse(
        {"id": "building", "links": [{"rel": "items", "type": "application/city+json", "href": "x"}]}
    )
    routes[f"{LANDING}/collections/gone"] = FakeResponse(status=404)
    routes[f"{LANDING}/collections/broken"] = FakeResponse(status=503)


@pytest.mark.django_db
def test_ogc_items_get_state_and_feature_count(world, http):  # noqa: F811
    routes, calls = http
    _, _, ogc, _, category = world
    _ogc_routes(routes)
    ok = _ogc_item(category, ogc, "stationen")
    building = _ogc_item(category, ogc, "building")
    gone = _ogc_item(category, ogc, "gone", feature_count=12)
    broken = _ogc_item(category, ogc, "broken", feature_count=7)
    no_dataset = _ogc_item(category, ogc, "x", dataset="removed")

    summary = health.check_items(health.items_for(categories=[category]))

    assert summary == {State.OK: 1, State.ERROR: 2, State.MISSING: 2}
    rows = {item.pk: item for item in CategoryItem.objects.all()}
    assert (rows[ok.pk].availability_state, rows[ok.pk].feature_count, rows[ok.pk].last_check_error) == (
        State.OK,
        358,
        "",
    )
    assert rows[ok.pk].last_checked_at is not None
    assert rows[building.pk].availability_state == State.ERROR and "GeoJSON" in rows[building.pk].last_check_error
    assert (rows[gone.pk].availability_state, rows[gone.pk].feature_count) == (State.MISSING, None)
    # A service problem is not proof the data is gone: keep the last known count.
    assert (rows[broken.pk].availability_state, rows[broken.pk].feature_count) == (State.ERROR, 7)
    assert "HTTP 503" in rows[broken.pk].last_check_error
    assert rows[no_dataset.pk].availability_state == State.MISSING
    items_call = next(params for url, params in calls if url.endswith("/stationen/items"))
    assert items_call["limit"] == "1"


@pytest.mark.django_db
def test_sensorthings_items_are_counted_by_paging_ids_without_count(world, http, monkeypatch):  # noqa: F811
    routes, calls = http
    _, _, _, sta, category = world
    monkeypatch.setattr(health, "STA_PAGE_SIZE", 2)

    def datastreams(url, params):
        if "'Status'" in params["$filter"]:
            values = [{"@iot.id": i} for i in range(5)][int(params["$skip"]) : int(params["$skip"]) + 2]
        else:
            values = []
        return FakeResponse({"value": values})

    routes[f"{STA_ROOT}/Datastreams"] = datastreams
    status = _sta_item(category, sta, "Status")
    empty = _sta_item(category, sta, "Nothing")

    health.check_items([status, empty])

    status.refresh_from_db()
    empty.refresh_from_db()
    assert (status.availability_state, status.feature_count) == (State.OK, 5)
    assert (empty.availability_state, empty.feature_count) == (State.MISSING, 0)
    assert all("$count" not in params for _, params in calls)
    assert all(params["$select"] == "@iot.id" for _, params in calls)


@pytest.mark.django_db
def test_check_refreshes_titles_from_the_index(world, http):  # noqa: F811
    routes, _ = http
    _, _, ogc, _, category = world
    _ogc_routes(routes)
    item = _ogc_item(category, ogc, "stationen")
    _source(ogc, "stadtrad", "stationen", title="StadtRAD-Stationen", dataset_title="StadtRAD")

    health.check_items([item])

    item.refresh_from_db()
    assert (item.title, item.dataset_title) == ("StadtRAD-Stationen", "StadtRAD")


@pytest.mark.django_db
def test_command_filters_by_service_and_category(world, monkeypatch, capsys):
    _, user, ogc, sta, category = world
    _ogc_item(category, ogc)
    _sta_item(category, sta)
    checked = []
    monkeypatch.setattr(health, "check_item", lambda item: (checked.append(item.service.slug), (State.OK, 1, ""))[1])

    call_command("check_external_catalog", service=["sta"])
    assert checked == ["sta"]
    call_command("check_external_catalog", category=["mobility"])
    assert sorted(checked) == ["ogc", "sta", "sta"]
    assert "1 OK" in capsys.readouterr().out
    with pytest.raises(CommandError, match="nope"):
        call_command("check_external_catalog", category=["nope"])


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("changelist", "selected"),
    [
        ("admin:external_catalog_categoryitem_changelist", "item"),
        ("admin:external_catalog_category_changelist", "category"),
        ("admin:external_catalog_externalservice_changelist", "service"),
    ],
)
def test_admin_actions_check_in_background(world, monkeypatch, django_capture_on_commit_callbacks, changelist, selected):
    client, _, ogc, _, category = world
    item = _ogc_item(category, ogc)
    monkeypatch.setattr(health, "_spawn", health.run_for_ids)
    monkeypatch.setattr(health, "check_item", lambda item: (State.OK, 42, ""))
    pk = {"item": item.pk, "category": category.pk, "service": ogc.pk}[selected]

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse(changelist), {"action": "check_availability", "_selected_action": [str(pk)]}, follow=True
        )

    assert any("Checking 1 item(s)" in str(message) for message in response.context["messages"])
    item.refresh_from_db()
    assert (item.availability_state, item.feature_count) == (State.OK, 42)


@pytest.mark.django_db
def test_readers_get_no_check_action(world):
    reader, _ = _staff_client("reader-a", "org-a", level="READER")

    response = reader.get(reverse("admin:external_catalog_categoryitem_changelist"))

    assert "check_availability" not in response.content.decode()
