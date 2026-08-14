"""Tests for the sync_geoserver_roles management command (epic-11 Phase 2)."""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command

from tosca_api.apps.geodata_providers.models import GeodataEngine

pytestmark = pytest.mark.django_db

_MODULE = "tosca_api.apps.geodata_providers.management.commands.sync_geoserver_roles"


@pytest.fixture
def engine():
    user = User.objects.create_user(username="role-sync", password="x")
    return GeodataEngine.objects.create(
        name="Primary",
        description="test",
        engine_type="geoserver",
        base_url="http://gs.internal/geoserver",
        public_url="http://gs.example/geoserver",
        admin_username="admin",
        admin_password="secret",
        is_active=True,
        is_default=True,
        created_by=user,
    )


def _summary(**over):
    base = {"created": [], "existed": [], "deleted": [], "absent": [], "failed": []}
    base.update(over)
    return base


def test_command_reports_reconcile(engine):
    result = [(engine, _summary(created=["ROLE_DCS_READER"], deleted=["ROLE_OLD_WRITER"]), None)]
    with patch(f"{_MODULE}.reconcile_all_engines", return_value=result) as mock_recon:
        call_command("sync_geoserver_roles")

    _, kwargs = mock_recon.call_args
    assert kwargs["dry_run"] is False


def test_command_dry_run_passes_flag(engine):
    with patch(f"{_MODULE}.reconcile_all_engines", return_value=[(engine, _summary(), None)]) as mock_recon:
        call_command("sync_geoserver_roles", "--dry-run")

    _, kwargs = mock_recon.call_args
    assert kwargs["dry_run"] is True


def test_command_unknown_engine_reports_and_skips(engine):
    with patch(f"{_MODULE}.reconcile_all_engines") as mock_recon:
        call_command("sync_geoserver_roles", "--engine", "does-not-exist")

    mock_recon.assert_not_called()


def test_command_engine_error_does_not_abort(engine):
    result = [(engine, None, "GeoServer down")]
    with patch(f"{_MODULE}.reconcile_all_engines", return_value=result):
        # Reports the per-engine error but completes without raising.
        call_command("sync_geoserver_roles")
