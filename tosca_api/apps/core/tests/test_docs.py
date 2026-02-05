from django.urls import reverse
from rest_framework.test import APIClient
import pytest

@pytest.mark.django_db
def test_swagger_docs_accessible():
    client = APIClient()
    # /api/schema/
    response = client.get(reverse("schema"))
    assert response.status_code == 200
    assert response['Content-Type'] == 'application/vnd.oai.openapi; version=3.0.3'

    # /api/docs/
    response = client.get(reverse("swagger-ui"))
    assert response.status_code == 200
