"""Public catalog API for external services and categories (ticket 04)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from tosca_api.apps.external_catalog.models import Category, CategoryItem, ExternalService, Visibility
from tosca_api.apps.organizations.models import Organization

SERVICES = reverse("catalog-v1-external-service-list")
CATEGORIES = reverse("catalog-v1-external-category-list")


def _detail(slug):
    return reverse("catalog-v1-external-category-detail", args=[slug])


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="api-user")


@pytest.fixture
def org(db):
    org, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    return org


@pytest.fixture
def anonymous():
    return APIClient()


@pytest.fixture
def signed_in(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _service(org, user, slug, **fields):
    values = {
        "organization": org,
        "name": slug,
        "slug": slug,
        "service_type": ExternalService.ServiceType.OGC_API_FEATURES,
        "base_url": f"https://{slug}.example.test",
        "title": slug.title(),
        "visibility": Visibility.PUBLIC,
        "created_by": user,
    }
    values.update(fields)
    if values["service_type"] == ExternalService.ServiceType.SENSORTHINGS:
        values.setdefault("mqtt_url", f"wss://{slug}.example.test/mqtt")
    return ExternalService.objects.create(**values)


def _category(org, user, slug, **fields):
    values = {"organization": org, "slug": slug, "title": slug.title(), "visibility": Visibility.PUBLIC}
    values.update(fields)
    return Category.objects.create(created_by=user, **values)


def _item(category, service, collection="c", **fields):
    values = {"title": collection.title(), "created_by": category.created_by}
    if service.service_type == ExternalService.ServiceType.SENSORTHINGS:
        values.update(sta_service_name="S", sta_layer_name=collection)
    else:
        values.update(ogc_collection_id=collection)
    values.update(fields)
    return CategoryItem.objects.create(category=category, service=service, **values)


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_services_list_public_active_rows_to_anonymous_and_private_to_signed_in(org, user, anonymous, signed_in):
    sta = _service(
        org,
        user,
        "hamburg-sensorthings",
        title="Hamburg SensorThings",
        service_type=ExternalService.ServiceType.SENSORTHINGS,
        attribution="Urban Data Platform Hamburg",
        allow_full_load=False,
        max_features=5000,
    )
    _service(org, user, "private", visibility=Visibility.PRIVATE)
    _service(org, user, "inactive", is_active=False)

    public = anonymous.get(SERVICES).json()
    everything = signed_in.get(SERVICES).json()

    assert public == [
        {
            "id": str(sta.pk),
            "slug": "hamburg-sensorthings",
            "service_type": "sensorthings",
            "title": "Hamburg SensorThings",
            "base_url": "https://hamburg-sensorthings.example.test",
            "attribution": "Urban Data Platform Hamburg",
            "capabilities": {
                "show_uncurated": False,
                "full_load": False,
                "live_updates": True,
                "server_filters": True,
                "max_features": 5000,
            },
            "mqtt_url": "wss://hamburg-sensorthings.example.test/mqtt",
        }
    ]
    assert [service["slug"] for service in everything] == ["hamburg-sensorthings", "private"]
    assert "mqtt_url" not in everything[1]


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_category_list_hides_private_inactive_and_empty_categories(org, user, anonymous, signed_in):
    public_service = _service(org, user, "public")
    private_service = _service(org, user, "private-svc", visibility=Visibility.PRIVATE)
    mixed = _category(org, user, "mixed", display_order=2, description="Both")
    _item(mixed, public_service, "a")
    _item(mixed, private_service, "b")
    only_private_items = _category(org, user, "only-private-items", display_order=1)
    _item(only_private_items, private_service, "c")
    _item(_category(org, user, "private-cat", visibility=Visibility.PRIVATE), public_service)
    _item(_category(org, user, "inactive-cat", is_active=False), public_service)
    _category(org, user, "empty")
    inactive_service = _service(org, user, "off", is_active=False)
    _item(_category(org, user, "dead-service"), inactive_service)

    public = anonymous.get(CATEGORIES).json()
    everything = signed_in.get(CATEGORIES).json()

    assert public == [
        {"slug": "mixed", "title": "Mixed", "description": "Both", "display_order": 2, "item_count": 1}
    ]
    assert [(row["slug"], row["item_count"]) for row in everything] == [
        ("private-cat", 1),  # display_order 0, then title
        ("only-private-items", 1),
        ("mixed", 2),
    ]


@pytest.mark.django_db
def test_category_detail_returns_ordered_items_with_map_settings(org, user, anonymous):
    ogc = _service(org, user, "hamburg-ogc-api")
    sta = _service(org, user, "hamburg-sensorthings", service_type=ExternalService.ServiceType.SENSORTHINGS)
    category = _category(org, user, "shared-mobility", title="Shared mobility", description="Bikes")
    charging = _item(
        category,
        sta,
        "Status_E-Ladepunkt",
        title="EV charging stations",
        sta_service_name="HH_STA_E-Ladestationen",
        display_order=1,
        color="#8e24aa",
        availability_state=CategoryItem.AvailabilityState.OK,
        feature_count=2455,
        last_checked_at=datetime(2026, 10, 7, 8, 0, tzinfo=UTC),
    )
    stations = _item(
        category,
        ogc,
        "stadtrad_stationen",
        title="StadtRAD stations",
        description="Bike rental",
        ogc_dataset_id="stadtrad",
        dataset_title="StadtRAD Hamburg",
        display_order=0,
        min_zoom=Decimal("14"),
        default_properties=["name"],
        default_filter=[{"property": "anzahl", "operator": "gt", "value": 0}],
    )

    response = anonymous.get(_detail("shared-mobility"))

    assert response.status_code == 200
    assert response.json() == {
        "slug": "shared-mobility",
        "title": "Shared mobility",
        "description": "Bikes",
        "items": [
            {
                "id": str(stations.pk),
                "title": "StadtRAD stations",
                "description": "Bike rental",
                "service": "hamburg-ogc-api",
                "service_type": "ogc_api_features",
                "ogc": {
                    "dataset_id": "stadtrad",
                    "dataset_title": "StadtRAD Hamburg",
                    "collection_id": "stadtrad_stationen",
                },
                "defaults": {
                    "properties": ["name"],
                    "filter": [{"property": "anzahl", "operator": "gt", "value": 0}],
                },
                "style": {"color": "#0288d1"},
                "loading": {"min_zoom": 14.0},
                "availability": {"state": "UNKNOWN", "feature_count": None, "checked_at": None},
            },
            {
                "id": str(charging.pk),
                "title": "EV charging stations",
                "description": "",
                "service": "hamburg-sensorthings",
                "service_type": "sensorthings",
                "sensorthings": {"service_name": "HH_STA_E-Ladestationen", "layer_name": "Status_E-Ladepunkt"},
                "style": {"color": "#8e24aa"},
                "loading": {"min_zoom": None},
                "availability": {"state": "OK", "feature_count": 2455, "checked_at": "2026-10-07T08:00:00+00:00"},
            },
        ],
    }


@pytest.mark.django_db
def test_category_detail_filters_items_by_service_visibility(org, user, anonymous, signed_in):
    public_service = _service(org, user, "public")
    private_service = _service(org, user, "private-svc", visibility=Visibility.PRIVATE)
    category = _category(org, user, "mixed")
    _item(category, public_service, "a")
    _item(category, private_service, "b")
    _item(category, _service(org, user, "off", is_active=False), "c")

    public = [item["ogc"]["collection_id"] for item in anonymous.get(_detail("mixed")).json()["items"]]
    everything = [item["ogc"]["collection_id"] for item in signed_in.get(_detail("mixed")).json()["items"]]

    assert public == ["a"]
    assert everything == ["a", "b"]


@pytest.mark.django_db
def test_missing_items_are_returned_flagged(org, user, anonymous):
    category = _category(org, user, "cat")
    _item(category, _service(org, user, "svc"), availability_state=CategoryItem.AvailabilityState.MISSING)

    [item] = anonymous.get(_detail("cat")).json()["items"]

    assert item["availability"]["state"] == "MISSING"


@pytest.mark.django_db
def test_unknown_private_and_empty_categories_are_404(org, user, anonymous, signed_in):
    service = _service(org, user, "svc")
    _item(_category(org, user, "private-cat", visibility=Visibility.PRIVATE), service)
    _category(org, user, "empty")

    for slug in ("unknown", "private-cat", "empty"):
        response = anonymous.get(_detail(slug))
        assert response.status_code == 404
        assert response.json() == {"detail": "Category not found."}
    assert signed_in.get(_detail("private-cat")).status_code == 200


@pytest.mark.django_db
def test_query_count_does_not_grow_with_items(org, user, anonymous, django_assert_num_queries):
    service = _service(org, user, "svc")
    category = _category(org, user, "cat")
    _item(category, service, "only")

    with django_assert_num_queries(2):
        anonymous.get(_detail("cat"))
    for index in range(10):
        _item(category, service, f"more-{index}")
    with django_assert_num_queries(2):
        anonymous.get(_detail("cat"))
    with django_assert_num_queries(1):
        anonymous.get(CATEGORIES)
    with django_assert_num_queries(1):
        anonymous.get(SERVICES)


@pytest.mark.django_db
def test_admin_session_counts_as_signed_in(org, user, client):
    _item(_category(org, user, "private-cat", visibility=Visibility.PRIVATE), _service(org, user, "svc"))
    client.force_login(user)

    assert client.get(_detail("private-cat")).status_code == 200
