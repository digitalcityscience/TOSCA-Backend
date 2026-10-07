"""OpenAPI entries of the external catalog API match the real payloads (ticket 06)."""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework import serializers

from tosca_api.apps.external_catalog import api, api_schema
from tosca_api.apps.external_catalog.models import ExternalService
from tosca_api.apps.external_catalog.tests.test_api import _category, _item, _service, org, user  # noqa: F401


def _assert_matches(payload: dict, schema: serializers.Serializer, path: str = "") -> None:
    """Payload keys == schema fields (optional fields may be absent), recursively."""
    fields = schema.fields
    required = {name for name, field in fields.items() if field.required}
    assert required <= set(payload), f"{path}: missing {required - set(payload)}"
    assert set(payload) <= set(fields), f"{path}: undocumented {set(payload) - set(fields)}"
    for name, value in payload.items():
        field = fields[name]
        if isinstance(field, serializers.ListSerializer) and isinstance(field.child, serializers.Serializer):
            for index, entry in enumerate(value):
                _assert_matches(entry, field.child, f"{path}.{name}[{index}]")
        elif isinstance(field, serializers.Serializer):
            _assert_matches(value, field, f"{path}.{name}")


@pytest.mark.django_db
def test_payloads_use_exactly_the_documented_keys(org, user):  # noqa: F811
    ogc = _service(org, user, "ogc")
    sta = _service(org, user, "sta", service_type=ExternalService.ServiceType.SENSORTHINGS)
    category = _category(org, user, "cat")
    ogc_item = _item(category, ogc, default_filter=[{"property": "a", "operator": "eq", "value": 1}])
    sta_item = _item(category, sta, "layer")

    for service in (ogc, sta):
        _assert_matches(api.service_payload(service), api_schema.ExternalServiceSchema(), service.slug)
    for item in (ogc_item, sta_item):
        _assert_matches(api.item_payload(item), api_schema.ExternalCategoryItemSchema(), item.title)


@pytest.mark.django_db
def test_schema_documents_the_external_catalog_routes(client):
    response = client.get(reverse("schema"))

    schema = response.content.decode()
    for path in (
        "/api/v1/catalog/external-services",
        "/api/v1/catalog/external-categories",
        "/api/v1/catalog/external-categories/{slug}",
    ):
        assert f"{path}:" in schema
    for component in (
        "ExternalServiceSchema",
        "ExternalCategorySummarySchema",
        "ExternalCategoryDetailSchema",
        "ExternalCategoryItemSchema",
    ):
        assert f"    {component}:" in schema
