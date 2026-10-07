"""Two-panel category picker and category-from-theme (external catalog ticket 03b)."""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from tosca_api.apps.external_catalog.models import Category, CategoryItem, ExternalService, Visibility
from tosca_api.apps.external_catalog.tests.test_admin import (
    _category,
    _category_post,
    _org,
    _service,
    _source,
    _staff_client,
)

ADD_URL = "admin:external_catalog_category_add"


def _change_url(category):
    return reverse("admin:external_catalog_category_change", args=[category.pk])


def _errors(response):
    return response.context["adminform"].form.errors


@pytest.fixture
def setup_a():
    client, user = _staff_client("writer-a", "org-a")
    org = _org("org-a")
    ogc = _service(org, user, "ogc")
    sta = _service(org, user, "sta", service_type=ExternalService.ServiceType.SENSORTHINGS)
    return client, user, org, ogc, sta


# ---------------------------------------------------------------------------
# Sources endpoint
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_sources_endpoint_lists_mapable_sources_and_themes(setup_a, client):
    client_a, _, _, ogc, _ = setup_a
    stations = _source(ogc, "stadtrad", "stationen", themes=["TRAN"], count=358)
    _source(ogc, "priobike", "ampel", themes=["TRAN", "ENVI"])
    _source(ogc, "lod2", "building", geojson=False, themes=["REGI"])
    url = reverse("admin:external_catalog_externalservice_sources", args=[ogc.pk])

    body = client_a.get(url).json()

    assert [row[3] for row in body["sources"]] == ["ampel", "stationen"]  # by dataset title
    assert body["sources"][1] == [stations.pk, "stadtrad", "Stadtrad", "stationen", "Stationen", 358, ["TRAN"]]
    assert body["themes"] == [
        {"code": "ENVI", "label": "Environment", "datasets": 1},
        {"code": "TRAN", "label": "Transport", "datasets": 2},
    ]
    assert body["catalog"]["state"] == "never"
    assert client.get(url).status_code == 302  # anonymous -> login


@pytest.mark.django_db
def test_sources_endpoint_is_org_scoped(setup_a):
    client_a, *_ = setup_a
    _, user_b = _staff_client("writer-b", "org-b")
    other = _service(_org("org-b"), user_b, "other")

    response = client_a.get(reverse("admin:external_catalog_externalservice_sources", args=[other.pk]))

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Composing a category
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_adding_sources_creates_items_with_service_titles(setup_a):
    client, user, org, ogc, sta = setup_a
    stations = _source(ogc, "stadtrad", "stadtrad_stationen", title="StadtRAD-Stationen", dataset_title="StadtRAD")
    charging = _source(sta, "HH_STA_E-Ladestationen", "Status_E-Ladepunkt", description="")

    response = client.post(reverse(ADD_URL), _category_post([f"src:{charging.pk}", f"src:{stations.pk}"]))

    assert response.status_code == 302, _errors(response)
    category = Category.objects.get(slug="shared-mobility")
    assert category.organization == org
    first, second = category.items.order_by("display_order")
    assert (first.service, first.sta_service_name, first.sta_layer_name, first.display_order) == (
        sta,
        "HH_STA_E-Ladestationen",
        "Status_E-Ladepunkt",
        0,
    )
    assert (second.title, second.dataset_title, second.description) == (
        "StadtRAD-Stationen",
        "StadtRAD",
        "About stadtrad_stationen",
    )
    assert (second.ogc_dataset_id, second.ogc_collection_id, second.created_by) == ("stadtrad", "stadtrad_stationen", user)


@pytest.mark.django_db
def test_change_keeps_reorders_removes_and_adds_in_one_save(setup_a):
    client, user, org, ogc, _ = setup_a
    admin_client, _ = _staff_client("admin-a", "org-a", level="ADMIN")
    category = _category(org, user, "mobility")
    keep = CategoryItem.objects.create(
        category=category, service=ogc, title="Keep", ogc_collection_id="keep", display_order=0, created_by=user
    )
    drop = CategoryItem.objects.create(
        category=category, service=ogc, title="Drop", ogc_collection_id="drop", display_order=1, created_by=user
    )
    new = _source(ogc, "", "new")

    response = admin_client.post(
        _change_url(category),
        _category_post([f"src:{new.pk}", f"item:{keep.pk}"], slug="mobility", title="Mobility"),
    )

    assert response.status_code == 302, _errors(response)
    items = list(category.items.order_by("display_order").values_list("ogc_collection_id", "display_order"))
    assert items == [("new", 0), ("keep", 1)]
    assert not CategoryItem.objects.filter(pk=drop.pk).exists()


@pytest.mark.django_db
def test_writers_may_add_but_not_remove_items(setup_a):
    client, user, org, ogc, _ = setup_a
    category = _category(org, user, "mobility")
    item = CategoryItem.objects.create(category=category, service=ogc, title="I", ogc_collection_id="i", created_by=user)

    response = client.post(_change_url(category), _category_post([], slug="mobility", title="Mobility"))

    assert response.status_code == 200
    assert "may not remove" in str(_errors(response)["item_sources"])
    assert CategoryItem.objects.filter(pk=item.pk).exists()


@pytest.mark.django_db
def test_foreign_unmapable_and_duplicate_sources_are_form_errors(setup_a):
    client, *_ , ogc, _ = setup_a
    _, user_b = _staff_client("writer-b", "org-b")
    foreign = _source(_service(_org("org-b"), user_b, "other"), "d", "c")
    building = _source(ogc, "lod2", "building", geojson=False)
    ok = _source(ogc, "d", "ok")

    for keys in ([f"src:{foreign.pk}"], [f"src:{building.pk}"], ["src:999999"], "not json"):
        data = _category_post(keys if isinstance(keys, list) else [])
        if not isinstance(keys, list):
            data["item_sources"] = keys
        response = client.post(reverse(ADD_URL), data)
        assert response.status_code == 200
        assert "item_sources" in _errors(response)

    response = client.post(reverse(ADD_URL), _category_post([f"src:{ok.pk}", f"src:{ok.pk}"]))
    assert "twice" in str(_errors(response)["item_sources"])
    assert not Category.objects.exists()


@pytest.mark.django_db
def test_widget_config_lists_own_services_and_current_items(setup_a):
    client, user, org, ogc, sta = setup_a
    _, user_b = _staff_client("writer-b", "org-b")
    _service(_org("org-b"), user_b, "other")
    category = _category(org, user, "mobility")
    item = CategoryItem.objects.create(
        category=category,
        service=ogc,
        title="Stations",
        dataset_title="StadtRAD",
        ogc_dataset_id="stadtrad",
        ogc_collection_id="stations",
        created_by=user,
    )

    response = client.get(_change_url(category))

    html = response.content.decode()
    config = json.loads(html.split('id="category-picker-config" type="application/json">')[1].split("</script>")[0])
    assert [service["title"] for service in config["services"]] == ["Ogc", "Sta"]
    assert config["entries"] == [
        {
            "key": f"item:{item.pk}",
            "match": f"{ogc.pk}|stadtrad|stations",
            "title": "Stations",
            "datasetTitle": "StadtRAD",
            "serviceTitle": "Ogc",
            "settingsUrl": reverse("admin:external_catalog_categoryitem_change", args=[item.pk]),
        }
    ]
    assert "external_catalog/admin/category-picker.js" in html


@pytest.mark.django_db
def test_rerendered_form_keeps_unsaved_picks(setup_a):
    client, _, _, ogc, _ = setup_a
    picked = _source(ogc, "d", "picked", title="Picked")

    response = client.post(reverse(ADD_URL), _category_post([f"src:{picked.pk}"], slug=""))

    assert response.status_code == 200
    assert '"key": "src:%d"' % picked.pk in response.content.decode()


# ---------------------------------------------------------------------------
# Category from a theme
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_category_from_theme_creates_private_inactive_category(setup_a):
    client, user, org, ogc, _ = setup_a
    ogc.catalog_loaded_at = ogc.created_at
    ExternalService.objects.filter(pk=ogc.pk).update(catalog_loaded_at=ogc.created_at)
    _source(ogc, "b", "two", dataset_title="B", themes=["TRAN"])
    _source(ogc, "a", "one", dataset_title="A", themes=["TRAN", "ENVI"])
    _source(ogc, "a", "cityjson", dataset_title="A", themes=["TRAN"], geojson=False)
    _source(ogc, "c", "env", themes=["ENVI"])
    Category.objects.create(organization=org, slug="transport", title="Taken", created_by=user)
    url = reverse("admin:external_catalog_category_from_theme")

    page = client.get(url)
    response = client.post(url, {"choice": f"{ogc.pk}:TRAN"})

    assert "Ogc — Transport (2 collections)" in page.content.decode()
    category = Category.objects.get(slug="transport-2")
    assert response.status_code == 302 and response["Location"] == _change_url(category)
    assert (category.title, category.visibility, category.is_active, category.organization) == (
        "Transport",
        Visibility.PRIVATE,
        False,
        org,
    )
    assert list(category.items.order_by("display_order").values_list("ogc_collection_id", flat=True)) == ["one", "two"]


@pytest.mark.django_db
def test_category_from_theme_needs_add_permission():
    reader, _ = _staff_client("reader-a", "org-a", level="READER")

    assert reader.get(reverse("admin:external_catalog_category_from_theme")).status_code == 403
