"""Admin behaviour for the external catalog (external catalog ticket 02)."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tosca_api.apps.external_catalog.models import Category, CategoryItem, ExternalService
from tosca_api.apps.organizations.models import (
    Organization,
    OrganizationAppEntitlement,
    UserAuthorizationSnapshot,
)

User = get_user_model()


def _org(slug):
    organization, _ = Organization.objects.get_or_create(slug=slug, defaults={"name": slug.upper()})
    OrganizationAppEntitlement.objects.get_or_create(organization=organization, app_label="external_catalog")
    return organization


def _staff_client(username, org_slug, level="WRITER"):
    _org(org_slug)
    user = User.objects.create_user(username=username, password="testpass123", is_staff=True)
    UserAuthorizationSnapshot.objects.create(
        user=user, org_roles={org_slug: level}, default_org=org_slug, synced_at=timezone.now()
    )
    client = Client()
    client.force_login(user)
    return client, user


def _service(org, user, slug, service_type=ExternalService.ServiceType.OGC_API_FEATURES, **extra):
    values = {
        "organization": org,
        "name": slug,
        "slug": slug,
        "service_type": service_type,
        "base_url": "https://example.test/api",
        "title": slug.title(),
        "created_by": user,
    }
    if service_type == ExternalService.ServiceType.SENSORTHINGS:
        values["mqtt_url"] = "wss://example.test/mqtt"
    values.update(extra)
    return ExternalService.objects.create(**values)


def _category(org, user, slug):
    return Category.objects.create(organization=org, slug=slug, title=slug.title(), created_by=user)


def _category_post(items, **fields):
    data = {
        "title": "Shared mobility",
        "slug": "shared-mobility",
        "description": "",
        "display_order": "1",
        "visibility": "PUBLIC",
        "is_active": "on",
        "items-TOTAL_FORMS": str(len(items)),
        "items-INITIAL_FORMS": "0",
        "items-MIN_NUM_FORMS": "0",
        "items-MAX_NUM_FORMS": "1000",
    }
    data.update(fields)
    for index, item in enumerate(items):
        defaults = {
            "id": "",
            "category": "",
            "title": f"Item {index}",
            "display_order": str(index),
            "description": "",
            "color": "#0288d1",
            "min_zoom": "",
            "ogc_dataset_id": "",
            "ogc_collection_ids": "",
            "default_properties": "",
            "default_filter": "",
            "sta_service_name": "",
            "sta_layer_name": "",
        }
        defaults.update(item)
        data.update({f"items-{index}-{key}": value for key, value in defaults.items()})
    return data


# ---------------------------------------------------------------------------
# Row scoping (gate C)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_staff_only_see_their_own_organization_rows():
    client_a, user_a = _staff_client("writer-a", "org-a")
    _, user_b = _staff_client("writer-b", "org-b")
    own = _service(_org("org-a"), user_a, "own-service")
    other = _service(_org("org-b"), user_b, "other-service")
    own_category = _category(_org("org-a"), user_a, "own-category")
    other_category = _category(_org("org-b"), user_b, "other-category")

    services = client_a.get(reverse("admin:external_catalog_externalservice_changelist"))
    categories = client_a.get(reverse("admin:external_catalog_category_changelist"))

    assert list(services.context["cl"].queryset) == [own]
    assert list(categories.context["cl"].queryset) == [own_category]
    for url in (
        reverse("admin:external_catalog_externalservice_change", args=[other.pk]),
        reverse("admin:external_catalog_category_change", args=[other_category.pk]),
    ):
        response = client_a.get(url)
        assert response.status_code == 302  # admin redirects unknown/out-of-scope objects


@pytest.mark.django_db
def test_reader_cannot_add_services():
    client, _ = _staff_client("reader-a", "org-a", level="READER")

    response = client.get(reverse("admin:external_catalog_externalservice_add"))

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Derived organization / created_by
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_service_add_derives_organization_and_creator():
    client, user = _staff_client("writer-a", "org-a")

    response = client.post(
        reverse("admin:external_catalog_externalservice_add"),
        {
            "name": "Hamburg SensorThings",
            "slug": "hamburg-sensorthings",
            "title": "Hamburg SensorThings",
            "service_type": "sensorthings",
            "base_url": "https://iot.hamburg.de",
            "mqtt_url": "wss://iot.hamburg.de/mqtt",
            "attribution": "",
            "description": "",
            "visibility": "PRIVATE",
            "is_active": "on",
            "allow_live_updates": "on",
            "max_features": "",
        },
    )

    assert response.status_code == 302, response.context["adminform"].form.errors
    service = ExternalService.objects.get(slug="hamburg-sensorthings")
    assert service.organization.slug == "org-a"
    assert service.created_by == user
    assert service.show_uncurated is False
    assert service.allow_full_load is False  # unchecked box in the form


@pytest.mark.django_db
def test_category_add_with_ogc_and_sensorthings_items():
    client, user = _staff_client("writer-a", "org-a")
    org = _org("org-a")
    ogc = _service(org, user, "hamburg-ogc-api")
    sta = _service(org, user, "hamburg-sta", service_type=ExternalService.ServiceType.SENSORTHINGS)

    response = client.post(
        reverse("admin:external_catalog_category_add"),
        _category_post(
            [
                {
                    "service": str(ogc.pk),
                    "title": "StadtRAD stations",
                    "ogc_dataset_id": "stadtrad",
                    "ogc_collection_ids": '["stadtrad_stationen"]',
                    "default_filter": '[{"property": "anzahl", "operator": "gt", "value": 0}]',
                },
                {
                    "service": str(sta.pk),
                    "title": "EV charging stations",
                    "sta_service_name": "HH_STA_E-Ladestationen",
                    "sta_layer_name": "Status_E-Ladepunkt",
                },
            ]
        ),
    )

    assert response.status_code == 302, response.context and response.context.get("errors")
    category = Category.objects.get(slug="shared-mobility")
    assert category.organization == org
    assert category.created_by == user
    items = list(category.items.all())
    assert [item.title for item in items] == ["StadtRAD stations", "EV charging stations"]
    assert all(item.created_by == user for item in items)
    assert items[0].default_properties == []
    assert items[1].ogc_collection_ids == []


@pytest.mark.django_db
def test_item_validation_errors_are_form_errors_not_server_errors():
    client, user = _staff_client("writer-a", "org-a")
    ogc = _service(_org("org-a"), user, "hamburg-ogc-api")

    response = client.post(
        reverse("admin:external_catalog_category_add"),
        _category_post(
            [
                {
                    "service": str(ogc.pk),
                    "ogc_collection_ids": "",
                    "sta_service_name": "HH_STA_StadtRad",
                }
            ]
        ),
    )

    assert response.status_code == 200
    item_errors = response.context["inline_admin_formsets"][0].formset.errors[0]
    assert {"ogc_collection_ids", "sta_service_name"} <= set(item_errors)
    assert not Category.objects.exists()


@pytest.mark.django_db
def test_inline_service_choices_are_limited_to_own_organization():
    client, user_a = _staff_client("writer-a", "org-a")
    _, user_b = _staff_client("writer-b", "org-b")
    own = _service(_org("org-a"), user_a, "own-service")
    _service(_org("org-b"), user_b, "other-service")

    response = client.get(reverse("admin:external_catalog_category_add"))

    service_field = response.context["inline_admin_formsets"][0].formset.empty_form.fields["service"]
    assert list(service_field.queryset) == [own]


# ---------------------------------------------------------------------------
# Item overview
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_item_overview_is_read_only_and_scoped():
    client, user_a = _staff_client("writer-a", "org-a")
    _, user_b = _staff_client("writer-b", "org-b")
    own_item = CategoryItem.objects.create(
        category=_category(_org("org-a"), user_a, "a"),
        service=_service(_org("org-a"), user_a, "svc-a"),
        title="Own",
        ogc_collection_ids=["c"],
        created_by=user_a,
    )
    CategoryItem.objects.create(
        category=_category(_org("org-b"), user_b, "b"),
        service=_service(_org("org-b"), user_b, "svc-b"),
        title="Other",
        ogc_collection_ids=["c"],
        created_by=user_b,
    )

    changelist = client.get(reverse("admin:external_catalog_categoryitem_changelist"))
    add = client.get(reverse("admin:external_catalog_categoryitem_add"))

    assert list(changelist.context["cl"].queryset) == [own_item]
    assert add.status_code == 403
