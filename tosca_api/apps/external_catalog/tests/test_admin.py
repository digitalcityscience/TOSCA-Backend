"""Admin behaviour for the external catalog (external catalog ticket 02)."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from tosca_api.apps.external_catalog.models import Category, CategoryItem, ExternalService, ServiceSource
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


def _category_post(keys=(), **fields):
    """Category form data; ``keys`` is the picker's item list (item:/src: keys)."""
    data = {
        "title": "Shared mobility",
        "slug": "shared-mobility",
        "description": "",
        "display_order": "1",
        "visibility": "PUBLIC",
        "is_active": "on",
        "item_sources": json.dumps(list(keys)),
    }
    data.update(fields)
    return data


def _source(service, dataset_id, source_id, **fields):
    values = {
        "title": source_id.replace("_", " ").title(),
        "dataset_title": dataset_id.title(),
        "description": f"About {source_id}",
        "harvested_at": timezone.now(),
    }
    values.update(fields)
    return ServiceSource.objects.create(service=service, dataset_id=dataset_id, source_id=source_id, **values)


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


# ---------------------------------------------------------------------------
# Item overview / settings
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_item_overview_is_scoped_and_items_cannot_be_added_there():
    client, user_a = _staff_client("writer-a", "org-a")
    _, user_b = _staff_client("writer-b", "org-b")
    own_item = CategoryItem.objects.create(
        category=_category(_org("org-a"), user_a, "a"),
        service=_service(_org("org-a"), user_a, "svc-a"),
        title="Own",
        ogc_collection_id="c",
        created_by=user_a,
    )
    CategoryItem.objects.create(
        category=_category(_org("org-b"), user_b, "b"),
        service=_service(_org("org-b"), user_b, "svc-b"),
        title="Other",
        ogc_collection_id="c",
        created_by=user_b,
    )

    changelist = client.get(reverse("admin:external_catalog_categoryitem_changelist"))
    add = client.get(reverse("admin:external_catalog_categoryitem_add"))

    assert list(changelist.context["cl"].queryset) == [own_item]
    assert add.status_code == 403


@pytest.mark.django_db
def test_item_settings_are_editable_but_source_and_title_are_not():
    client, user = _staff_client("writer-a", "org-a")
    item = CategoryItem.objects.create(
        category=_category(_org("org-a"), user, "a"),
        service=_service(_org("org-a"), user, "svc-a"),
        title="From the service",
        ogc_collection_id="c",
        created_by=user,
    )

    response = client.post(
        reverse("admin:external_catalog_categoryitem_change", args=[item.pk]),
        {
            "color": "#10b981",
            "min_zoom": "14",
            "default_properties": '["name"]',
            "default_filter": "",
            "title": "Typed by hand",
            "ogc_collection_id": "other",
        },
    )

    assert response.status_code == 302, response.context["adminform"].form.errors
    item.refresh_from_db()
    assert (item.color, str(item.min_zoom), item.default_properties, item.default_filter) == (
        "#10b981",
        "14.0",
        ["name"],
        [],
    )
    assert (item.title, item.ogc_collection_id) == ("From the service", "c")
