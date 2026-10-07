"""Tests for the layer feature-attribute catalog (Layer.attributes)."""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from tosca_api.apps.geodata_providers.feature_attributes import normalize_feature_attributes
from tosca_api.apps.geodata_providers.models import Layer, Store
from tosca_api.apps.geodata_providers.services.commands.layer_service import LayerService
from tosca_api.apps.geodata_providers.sync import LayerSyncer
from tosca_api.apps.geodata_providers.test_helpers import make_layer
from tosca_api.apps.geodata_providers.tests.test_geoserver_client import make_client

User = get_user_model()

RAW_ATTRIBUTES = [
    {"name": "geom", "binding": "org.locationtech.jts.geom.MultiPolygon"},
    {"name": "objectid", "binding": "java.lang.Long"},
    {"name": "name", "binding": "java.lang.String"},
]
NORMALIZED = [{"name": "objectid", "type": "Long"}, {"name": "name", "type": "String"}]

FACTORY = (
    "tosca_api.apps.geodata_providers.services.commands.layer_service."
    "EngineClientFactory.create_client"
)


@pytest.fixture
def user():
    return User.objects.create_user(username="attr-user", password="x")


@pytest.fixture
def vector_layer(user):
    return make_layer("ws:districts", user=user, geometry_type="MultiPolygon")


@pytest.fixture
def raster_layer(user):
    layer = make_layer("ras:ortho", user=user)
    Store.objects.filter(pk=layer.store_id).update(
        store_type=Store.StoreType.GEOTIFF, file_path="/data/ortho.tif"
    )
    layer.refresh_from_db()
    Layer.objects.filter(pk=layer.pk).update(attributes=NORMALIZED)
    layer.refresh_from_db()
    return layer


# --- normalize_feature_attributes ------------------------------------------


def test_normalize_drops_geometry_and_shortens_bindings():
    assert normalize_feature_attributes(RAW_ATTRIBUTES) == NORMALIZED


def test_normalize_accepts_single_attribute_dict():
    raw = {"name": "id", "binding": "java.lang.Integer"}

    assert normalize_feature_attributes(raw) == [{"name": "id", "type": "Integer"}]


@pytest.mark.parametrize("raw", [None, "", "x", 5, {"binding": "java.lang.String"}])
def test_normalize_ignores_malformed_input(raw):
    assert normalize_feature_attributes(raw) == []


def test_normalize_skips_duplicates_blank_names_and_keeps_unknown_bindings():
    raw = [
        {"name": "id", "binding": "java.lang.Integer"},
        {"name": "id", "binding": "java.lang.String"},
        {"name": " ", "binding": "java.lang.String"},
        "junk",
        {"name": "note"},
    ]

    assert normalize_feature_attributes(raw) == [
        {"name": "id", "type": "Integer"},
        {"name": "note", "type": ""},
    ]


# --- GeoServerClient.get_layers --------------------------------------------


def _client_listing_one_featuretype(featuretype):
    client = make_client()
    client._client.get_datastores.return_value = {"dataStores": {"dataStore": [{"name": "gis"}]}}
    client._client.get_featuretypes.return_value = ["mobility:districts"]
    client._client.get_featuretype.return_value = featuretype
    return client


def test_get_layers_includes_normalized_attributes():
    client = _client_listing_one_featuretype(
        {"name": "districts", "attributes": {"attribute": RAW_ATTRIBUTES}}
    )

    with patch.object(client, "get_layer_settings", return_value={}), patch.object(
        client, "get_coverage_layers", return_value=[]
    ):
        [layer] = client.get_layers("mobility")

    assert layer["attributes"] == NORMALIZED
    assert layer["geometry_type"] == "MultiPolygon"


def test_get_layers_omits_attributes_when_detail_fails():
    client = _client_listing_one_featuretype(None)
    client._client.get_featuretype.side_effect = RuntimeError("boom")

    with patch.object(client, "get_layer_settings", return_value={}), patch.object(
        client, "get_coverage_layers", return_value=[]
    ):
        [layer] = client.get_layers("mobility")

    assert "attributes" not in layer


# --- LayerSyncer ------------------------------------------------------------


def _sync(layer, layer_data):
    client = MagicMock()
    client.get_layers.return_value = [
        {"name": layer.name, "store_name": layer.store.name, "geometry_type": "Point", **layer_data}
    ]
    engine = layer.workspace.geodata_engine
    LayerSyncer(engine, client).sync_layers_for_workspace(layer.workspace, created_by=layer.created_by)
    layer.refresh_from_db()


@pytest.mark.django_db
def test_sync_stores_reported_attributes(vector_layer):
    _sync(vector_layer, {"attributes": NORMALIZED})

    assert vector_layer.attributes == NORMALIZED


@pytest.mark.django_db
def test_sync_keeps_attributes_when_engine_omits_them(vector_layer):
    Layer.objects.filter(pk=vector_layer.pk).update(attributes=NORMALIZED)

    _sync(vector_layer, {})

    assert vector_layer.attributes == NORMALIZED


@pytest.mark.django_db
def test_sync_clears_attributes_for_raster_layers(raster_layer):
    _sync(raster_layer, {"attributes": NORMALIZED})

    assert raster_layer.attributes == []


# --- LayerService.refresh_attributes ----------------------------------------


@pytest.mark.django_db
def test_refresh_attributes_stores_engine_attributes(vector_layer):
    client = MagicMock()
    client.get_featuretype_detail.return_value = {"attributes": RAW_ATTRIBUTES}

    with patch(FACTORY, return_value=client):
        result = LayerService.refresh_attributes(vector_layer)

    assert result == {"success": True, "attributes": NORMALIZED}
    client.get_featuretype_detail.assert_called_once_with("ws", "ws_store", "districts")
    vector_layer.refresh_from_db()
    assert vector_layer.attributes == NORMALIZED


@pytest.mark.django_db
def test_refresh_attributes_clears_raster_without_engine_call(raster_layer):
    with patch(FACTORY) as factory:
        result = LayerService.refresh_attributes(raster_layer)

    factory.assert_not_called()
    assert result == {"success": True, "attributes": []}
    raster_layer.refresh_from_db()
    assert raster_layer.attributes == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "configure",
    [
        lambda client: setattr(client.get_featuretype_detail, "return_value", {"name": "x"}),
        lambda client: setattr(client.get_featuretype_detail, "side_effect", RuntimeError("down")),
    ],
    ids=["no-attributes", "engine-error"],
)
def test_refresh_attributes_keeps_existing_on_failure(vector_layer, configure):
    Layer.objects.filter(pk=vector_layer.pk).update(attributes=NORMALIZED)
    client = MagicMock()
    configure(client)

    with patch(FACTORY, return_value=client):
        result = LayerService.refresh_attributes(vector_layer)

    assert result["success"] is False
    assert "districts" in result["error"]
    vector_layer.refresh_from_db()
    assert vector_layer.attributes == NORMALIZED


@pytest.mark.django_db
def test_publish_existing_layer_refreshes_attributes(user):
    layer = make_layer("ws:to_publish", user=user, publishing_state="DRAFT")
    client = MagicMock()
    client.verify_featuretype.side_effect = [False, True]
    client.get_featuretype_detail.return_value = {"attributes": RAW_ATTRIBUTES}

    with patch(FACTORY, return_value=client):
        result = LayerService.publish_existing_layer(layer)

    assert result["success"] is True
    layer.refresh_from_db()
    assert layer.attributes == NORMALIZED


@pytest.mark.django_db
def test_publish_succeeds_even_if_attribute_refresh_fails(user):
    layer = make_layer("ws:publish_no_attrs", user=user, publishing_state="DRAFT")
    client = MagicMock()
    client.verify_featuretype.side_effect = [False, True]
    client.get_featuretype_detail.side_effect = RuntimeError("down")

    with patch(FACTORY, return_value=client):
        result = LayerService.publish_existing_layer(layer)

    assert result["success"] is True
    layer.refresh_from_db()
    assert layer.publishing_state == "PUBLISHED"
    assert layer.attributes == []


# --- Admin -------------------------------------------------------------------


@pytest.fixture
def admin_client(db):
    superuser = User.objects.create_user(
        username="attr-admin", password="x", is_staff=True, is_superuser=True
    )
    client = Client()
    client.force_login(superuser)
    return client


@pytest.mark.django_db
def test_admin_refresh_action_updates_selected_layers(admin_client, vector_layer):
    client = MagicMock()
    client.get_featuretype_detail.return_value = {"attributes": RAW_ATTRIBUTES}

    with patch(FACTORY, return_value=client):
        response = admin_client.post(
            reverse("admin:geodata_providers_layer_changelist"),
            {"action": "refresh_layer_attributes", "_selected_action": [str(vector_layer.pk)]},
            follow=True,
        )

    assert "Refreshed attributes for 1 layer(s)." in response.content.decode()
    vector_layer.refresh_from_db()
    assert vector_layer.attributes == NORMALIZED


@pytest.mark.django_db
def test_admin_change_page_lists_attributes(admin_client, vector_layer):
    Layer.objects.filter(pk=vector_layer.pk).update(attributes=NORMALIZED)

    response = admin_client.get(
        reverse("admin:geodata_providers_layer_change", args=[vector_layer.pk])
    )

    content = response.content.decode()
    assert "<code>objectid</code>" in content
    assert "<code>name</code>" in content
