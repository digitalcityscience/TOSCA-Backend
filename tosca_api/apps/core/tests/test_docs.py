from django.urls import reverse
from rest_framework.test import APIClient
import pytest

@pytest.mark.django_db
def test_swagger_docs_accessible():
    client = APIClient()
    # /api/schema/
    response = client.get(reverse("schema"))
    assert response.status_code == 200
    assert "application/vnd.oai.openapi" in response['Content-Type']

    # /api/docs/
    response = client.get(reverse("swagger-ui"))
    assert response.status_code == 200


def test_phase_image_schema_surfaces_are_documented(client):
    response = client.get(reverse("schema"))
    assert response.status_code == 200

    schema = response.content.decode()
    assert "hero_image_url" in schema
    assert "hero_image_alt" in schema
    assert "/api/v1/geocontext/editorjs/upload-by-file/" in schema
    assert "/api/v1/geocontext/editorjs/upload-by-url/" in schema
    assert "/api/v1/media/derivative/" in schema


def test_phase_image_decisions_are_recorded():
    decisions = open("docs/features-to-add_local/decisions.md", encoding="utf-8").read()
    for header in (
        "### [9.1]",
        "### [9.2]",
        "### [9.2b]",
        "### [9.4]",
        "### [9.5]",
        "### [9.6]",
        "### [9.7]",
    ):
        assert header in decisions
