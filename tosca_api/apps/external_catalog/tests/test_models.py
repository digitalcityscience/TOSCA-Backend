"""Model validation for the external catalog (external catalog ticket 01)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import override_settings

from tosca_api.apps.external_catalog.models import (
    Category,
    CategoryItem,
    ExternalService,
    Visibility,
)
from tosca_api.apps.organizations.models import Organization


@pytest.fixture
def org(db):
    org, _ = Organization.objects.get_or_create(slug="dcs", defaults={"name": "DCS"})
    return org


@pytest.fixture
def other_org(db):
    return Organization.objects.create(name="QG2", slug="qg2")


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="catalog-admin")


def make_service(org, user, **overrides) -> ExternalService:
    values = {
        "organization": org,
        "name": "Hamburg OGC API",
        "slug": "hamburg-ogc-api",
        "service_type": ExternalService.ServiceType.OGC_API_FEATURES,
        "base_url": "https://api.hamburg.de/datasets/v1",
        "title": "Hamburg OGC API",
        "created_by": user,
    }
    values.update(overrides)
    return ExternalService.objects.create(**values)


def make_sta_service(org, user, **overrides) -> ExternalService:
    values = {
        "name": "Hamburg SensorThings",
        "slug": "hamburg-sensorthings",
        "service_type": ExternalService.ServiceType.SENSORTHINGS,
        "base_url": "https://iot.hamburg.de",
        "mqtt_url": "wss://iot.hamburg.de/mqtt",
        "title": "Hamburg SensorThings",
    }
    values.update(overrides)
    return make_service(org, user, **values)


def make_category(org, user, **overrides) -> Category:
    values = {
        "organization": org,
        "slug": "shared-mobility",
        "title": "Shared mobility",
        "created_by": user,
    }
    values.update(overrides)
    return Category.objects.create(**values)


def ogc_item(category, service, user, **overrides) -> CategoryItem:
    values = {
        "category": category,
        "service": service,
        "title": "StadtRAD stations",
        "ogc_dataset_id": "stadtrad",
        "ogc_collection_ids": ["stadtrad_stationen"],
        "created_by": user,
    }
    values.update(overrides)
    return CategoryItem(**values)


def sta_item(category, service, user, **overrides) -> CategoryItem:
    values = {
        "category": category,
        "service": service,
        "title": "EV charging stations",
        "sta_service_name": "HH_STA_E-Ladestationen",
        "sta_layer_name": "Status_E-Ladepunkt",
        "created_by": user,
    }
    values.update(overrides)
    return CategoryItem(**values)


def error_fields(item) -> set[str]:
    with pytest.raises(ValidationError) as excinfo:
        item.full_clean()
    return set(excinfo.value.message_dict)


# ---------------------------------------------------------------------------
# ExternalService
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_service_defaults_are_private_with_capabilities_enabled_except_uncurated(org, user):
    service = make_service(org, user)

    assert service.visibility == Visibility.PRIVATE
    assert service.is_active is True
    assert service.show_uncurated is False
    assert service.allow_full_load is True
    assert service.allow_live_updates is True
    assert service.allow_server_filters is True
    assert service.max_features is None


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_service_requires_https_outside_debug(org, user):
    with pytest.raises(ValidationError) as excinfo:
        make_service(org, user, base_url="http://api.example.test")
    assert "base_url" in excinfo.value.message_dict


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_service_allows_http_and_ws_in_debug(org, user):
    service = make_sta_service(
        org, user, base_url="http://localhost:8080", mqtt_url="ws://localhost:9876/mqtt"
    )
    assert service.pk is not None


@pytest.mark.django_db
def test_mqtt_url_only_for_sensorthings(org, user):
    with pytest.raises(ValidationError) as excinfo:
        make_service(org, user, mqtt_url="wss://iot.hamburg.de/mqtt")
    assert "mqtt_url" in excinfo.value.message_dict


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_mqtt_url_must_be_wss_outside_debug(org, user):
    with pytest.raises(ValidationError) as excinfo:
        make_sta_service(org, user, mqtt_url="ws://iot.hamburg.de/mqtt")
    assert "mqtt_url" in excinfo.value.message_dict

    with pytest.raises(ValidationError):
        make_sta_service(org, user, slug="sta-2", mqtt_url="mqtt://iot.hamburg.de:1883")


@pytest.mark.django_db
def test_service_slug_is_unique(org, user):
    make_service(org, user)
    with pytest.raises(ValidationError):
        make_service(org, user, name="Second")


@pytest.mark.django_db
def test_max_features_must_be_positive(org, user):
    with pytest.raises(ValidationError) as excinfo:
        make_service(org, user, max_features=0)
    assert "max_features" in excinfo.value.message_dict


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_category_defaults_private_and_orders_by_display_order_then_title(org, user):
    later = make_category(org, user, slug="b", title="B", display_order=2)
    first_b = make_category(org, user, slug="z", title="Z", display_order=1)
    first_a = make_category(org, user, slug="a", title="A", display_order=1)

    assert later.visibility == Visibility.PRIVATE
    assert list(Category.objects.all()) == [first_a, first_b, later]


# ---------------------------------------------------------------------------
# CategoryItem — OGC API Features
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_ogc_item_valid_with_merged_collections_defaults_and_filter(org, user):
    service = make_service(org, user)
    category = make_category(org, user)
    item = ogc_item(
        category,
        service,
        user,
        ogc_dataset_id="priobike",
        ogc_collection_ids=["ampelschaltung", "gruene_welle"],
        default_properties=["name"],
        default_filter=[
            {"property": "breite", "operator": "gte", "value": 5},
            {"property": "strassenname", "operator": "contains", "value": "allee"},
        ],
        color="#10b981",
        min_zoom=Decimal("14"),
    )
    item.save()

    assert item.availability_state == CategoryItem.AvailabilityState.UNKNOWN
    assert item.feature_count is None


@pytest.mark.django_db
@pytest.mark.parametrize("collection_ids", [[], ["a", "a"], [""], "a", [1]])
def test_ogc_item_needs_unique_non_empty_collection_ids(org, user, collection_ids):
    item = ogc_item(make_category(org, user), make_service(org, user), user, ogc_collection_ids=collection_ids)
    assert "ogc_collection_ids" in error_fields(item)


@pytest.mark.django_db
def test_ogc_item_rejects_sensorthings_fields(org, user):
    item = ogc_item(
        make_category(org, user),
        make_service(org, user),
        user,
        sta_service_name="HH_STA_StadtRad",
        sta_layer_name="Fahrraeder",
    )
    assert {"sta_service_name", "sta_layer_name"} <= error_fields(item)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "default_filter",
    [
        {"property": "a", "operator": "eq", "value": 1},
        [{"property": "a", "operator": "like", "value": 1}],
        [{"property": "", "operator": "eq", "value": 1}],
        [{"property": "a", "operator": "eq", "value": ""}],
        [{"property": "a", "operator": "eq", "value": [1]}],
        [{"property": "a", "operator": "eq"}],
        [{"property": "a", "operator": "eq", "value": 1, "extra": True}],
    ],
)
def test_ogc_item_rejects_invalid_default_filter(org, user, default_filter):
    item = ogc_item(make_category(org, user), make_service(org, user), user, default_filter=default_filter)
    assert "default_filter" in error_fields(item)


@pytest.mark.django_db
@pytest.mark.parametrize("default_properties", ["name", ["name", "name"], [""], [None]])
def test_ogc_item_rejects_invalid_default_properties(org, user, default_properties):
    item = ogc_item(
        make_category(org, user), make_service(org, user), user, default_properties=default_properties
    )
    assert "default_properties" in error_fields(item)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field", "value"),
    [("color", "blue"), ("color", "#12345"), ("min_zoom", Decimal("24.5")), ("min_zoom", Decimal("-1"))],
)
def test_item_rejects_invalid_presentation_hints(org, user, field, value):
    item = ogc_item(make_category(org, user), make_service(org, user), user, **{field: value})
    assert field in error_fields(item)


# ---------------------------------------------------------------------------
# CategoryItem — SensorThings
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_sensorthings_item_valid(org, user):
    item = sta_item(make_category(org, user), make_sta_service(org, user), user)
    item.save()
    assert item.pk is not None


@pytest.mark.django_db
def test_sensorthings_item_requires_service_and_layer_name(org, user):
    item = sta_item(
        make_category(org, user), make_sta_service(org, user), user, sta_service_name=" ", sta_layer_name=""
    )
    assert {"sta_service_name", "sta_layer_name"} <= error_fields(item)


@pytest.mark.django_db
def test_sensorthings_item_rejects_ogc_fields(org, user):
    item = sta_item(
        make_category(org, user),
        make_sta_service(org, user),
        user,
        ogc_dataset_id="stadtrad",
        ogc_collection_ids=["stadtrad_stationen"],
        default_properties=["name"],
        default_filter=[{"property": "a", "operator": "eq", "value": 1}],
    )
    assert {"ogc_dataset_id", "ogc_collection_ids", "default_properties", "default_filter"} <= error_fields(item)


# ---------------------------------------------------------------------------
# Cross-model invariants
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_item_service_must_belong_to_the_category_organization(org, other_org, user):
    item = ogc_item(make_category(org, user), make_service(other_org, user), user)
    assert "service" in error_fields(item)


@pytest.mark.django_db
def test_service_in_use_cannot_be_deleted_but_category_deletion_cascades(org, user):
    service = make_service(org, user)
    category = make_category(org, user)
    ogc_item(category, service, user).save()

    with pytest.raises(ProtectedError):
        service.delete()

    category.delete()
    assert not CategoryItem.objects.exists()
    service.delete()
