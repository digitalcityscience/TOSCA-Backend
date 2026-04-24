"""
Tests for LayerRef model.
"""

import pytest
from django.db import IntegrityError

from tosca_api.apps.layerrefs.models import LayerRef


@pytest.mark.django_db
def test_layerref_creation():
    """Test standard LayerRef creation."""
    ref = LayerRef.objects.create(layer_name="workspace:roads")
    assert ref.id is not None
    assert ref.layer_name == "workspace:roads"
    assert str(ref) == "workspace:roads"


@pytest.mark.django_db
def test_layerref_unique_name():
    """Test that layer_name must be unique."""
    LayerRef.objects.create(layer_name="workspace:roads")
    with pytest.raises(IntegrityError):
        LayerRef.objects.create(layer_name="workspace:roads")


@pytest.mark.django_db
def test_layerref_sanitization():
    """Test that layer_name is sanitized (Zero Trust)."""
    unsafe = "workspace:roads<script>alert(1)</script>"
    ref = LayerRef.objects.create(layer_name=unsafe)
    assert "<script>" not in ref.layer_name
    assert ref.layer_name == "workspace:roads"
