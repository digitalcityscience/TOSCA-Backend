"""Test helpers for building geodata_providers fixtures from other apps' tests."""

from __future__ import annotations

from django.contrib.auth import get_user_model

from tosca_api.apps.geodata_providers.models import (
    GeodataEngine,
    Layer,
    Store,
    Workspace,
)

User = get_user_model()


def _get_or_create_user(user=None):
    if user is not None:
        return user
    return User.objects.get_or_create(
        username="geodata-test-fixture",
        defaults={"password": "testpass123"},
    )[0]


def make_layer(
    layer_name: str = "workspace:test_layer",
    *,
    user=None,
    geometry_type: str = "Point",
    publishing_state: str = Layer.PublishingState.PUBLISHED,
    is_public: bool = True,
) -> Layer:
    """
    Create a `geodata_providers.Layer` from a ``"workspace:name"`` string.

    Reuses or creates the engine/workspace/store ancestors as needed. Defaults
    produce a public, published layer ready to be attached to a GeoStory,
    Event, or GeoFeedback through-row.
    """
    if ":" in layer_name:
        workspace_name, name = layer_name.split(":", 1)
    else:
        workspace_name, name = "workspace", layer_name

    user = _get_or_create_user(user)

    engine, _ = GeodataEngine.objects.get_or_create(
        name="test-engine",
        defaults={
            "engine_type": "geoserver",
            "base_url": "http://example.com/geoserver",
            "public_url": "http://example.com/geoserver",
            "admin_username": "admin",
            "admin_password": "secret",
            "created_by": user,
        },
    )
    workspace, _ = Workspace.objects.get_or_create(
        geodata_engine=engine,
        name=workspace_name,
        defaults={"description": "", "created_by": user},
    )
    store, _ = Store.objects.get_or_create(
        workspace=workspace,
        name=f"{workspace_name}_store",
        defaults={
            "store_type": "postgis",
            "host": "db",
            "port": 5432,
            "database": "gis",
            "username": "postgres",
            "password": "secret",
            "schema": "public",
            "created_by": user,
        },
    )
    return Layer.objects.create(
        workspace=workspace,
        store=store,
        name=name,
        table_name=name,
        geometry_column="geom",
        geometry_type=geometry_type,
        srid=4326,
        publishing_state=publishing_state,
        is_public=is_public,
        created_by=user,
    )
