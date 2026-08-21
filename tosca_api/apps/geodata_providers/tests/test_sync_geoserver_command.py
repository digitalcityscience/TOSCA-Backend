"""Tests for selecting GeoServer engines in the startup sync command."""

from unittest.mock import Mock, patch

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command

from tosca_api.apps.geodata_providers.models import GeodataEngine

pytestmark = pytest.mark.django_db

_MODULE = "tosca_api.apps.geodata_providers.management.commands.sync_geoserver"


def make_engine(*, name, is_default):
    user = User.objects.get_or_create(username="sync-admin", is_superuser=True)[0]
    return GeodataEngine.objects.create(
        name=name,
        description="test",
        engine_type="geoserver",
        base_url="http://gs.internal/geoserver",
        public_url="http://gs.example/geoserver",
        admin_username="admin",
        admin_password="secret",
        is_active=True,
        is_default=is_default,
        created_by=user,
    )


def sync_result():
    section = {"synced": 0, "created": 0, "deleted": 0, "errors": []}
    return {
        "success": True,
        "workspaces": section,
        "stores": section,
        "layers": section,
    }


@patch(f"{_MODULE}.EngineClientFactory.create_sync_service")
def test_default_option_syncs_only_the_default_active_engine(create_sync_service):
    default_engine = make_engine(name="Default", is_default=True)
    make_engine(name="Secondary", is_default=False)
    service = Mock()
    service.sync_all_resources.return_value = sync_result()
    create_sync_service.return_value = service

    call_command("sync_geoserver", "--default")

    create_sync_service.assert_called_once_with(default_engine)
    service.sync_all_resources.assert_called_once()
