"""Tests for the GeoServer role reconciliation (epic-11 Phase 2).

Reader/writer only, catalog-driven, ensure + delete. Admin is never pushed and
non-catalog GeoServer roles are never touched.
"""

from unittest.mock import MagicMock

import pytest

from tosca_api.apps.geodata_providers.results import OperationResult
from tosca_api.apps.geodata_providers.role_sync import (
    GeoServerRoleSyncService,
    reader_writer_reconcile_names,
    reconcile_all_engines,
)
from tosca_api.apps.authentication.models import KeycloakRole
from tosca_api.apps.organizations.models import Organization


def _fake_engine(client, name="Primary"):
    engine = MagicMock()
    engine.name = name
    engine.get_client.return_value = client
    return engine


class TestReconcile:
    def test_creates_missing_and_deletes_present(self):
        client = MagicMock()
        client.get_roles.return_value = ["ROLE_A_READER", "ROLE_A_WRITER", "ADMIN"]
        client.create_role.return_value = OperationResult(success=True)
        client.delete_role.return_value = OperationResult(success=True)

        service = GeoServerRoleSyncService(_fake_engine(client))
        summary = service.reconcile(
            ensure_names={"ROLE_A_READER", "ROLE_B_READER"},
            delete_names={"ROLE_A_WRITER"},
        )

        assert summary["existed"] == ["ROLE_A_READER"]
        assert summary["created"] == ["ROLE_B_READER"]
        assert summary["deleted"] == ["ROLE_A_WRITER"]
        client.create_role.assert_called_once_with("ROLE_B_READER")
        client.delete_role.assert_called_once_with("ROLE_A_WRITER")

    def test_delete_of_absent_role_is_noop(self):
        client = MagicMock()
        client.get_roles.return_value = []

        service = GeoServerRoleSyncService(_fake_engine(client))
        summary = service.reconcile(ensure_names=set(), delete_names={"ROLE_GONE_READER"})

        assert summary["absent"] == ["ROLE_GONE_READER"]
        client.delete_role.assert_not_called()

    def test_dry_run_writes_nothing(self):
        client = MagicMock()
        client.get_roles.return_value = ["ROLE_OLD_WRITER"]

        service = GeoServerRoleSyncService(_fake_engine(client))
        summary = service.reconcile(
            ensure_names={"ROLE_NEW_READER"},
            delete_names={"ROLE_OLD_WRITER"},
            dry_run=True,
        )

        assert summary["created"] == ["ROLE_NEW_READER"]
        assert summary["deleted"] == ["ROLE_OLD_WRITER"]
        client.create_role.assert_not_called()
        client.delete_role.assert_not_called()

    def test_failed_write_is_recorded_not_raised(self):
        client = MagicMock()
        client.get_roles.return_value = []
        client.create_role.return_value = OperationResult(success=False, error="HTTP 500")

        service = GeoServerRoleSyncService(_fake_engine(client))
        summary = service.reconcile(ensure_names={"ROLE_A_READER"}, delete_names=set())

        assert summary["created"] == []
        assert summary["failed"] == [("ROLE_A_READER", "HTTP 500")]


@pytest.mark.django_db
class TestReaderWriterReconcileNames:
    def test_splits_active_and_inactive_readerwriter_excludes_admin(self):
        org, _ = Organization.objects.get_or_create(slug="acme", defaults={"name": "ACME"})

        def mk(name, level, active=True):
            KeycloakRole.objects.create(
                name=name, organization=org, level=level,
                source=KeycloakRole.Source.LOGIN, is_active=active,
            )

        mk("ROLE_ACME_READER", KeycloakRole.Level.READER)
        mk("ROLE_ACME_WRITER", KeycloakRole.Level.WRITER)
        mk("ROLE_ACME_ADMIN", KeycloakRole.Level.ADMIN)          # excluded (admin)
        mk("ROLE_ACME_OLD_READER", KeycloakRole.Level.READER, active=False)  # to delete

        ensure, delete = reader_writer_reconcile_names()

        assert ensure == {"ROLE_ACME_READER", "ROLE_ACME_WRITER"}
        assert delete == {"ROLE_ACME_OLD_READER"}
        assert "ROLE_ACME_ADMIN" not in ensure | delete


@pytest.mark.django_db
class TestReconcileAllEngines:
    def _catalog(self):
        org, _ = Organization.objects.get_or_create(slug="acme", defaults={"name": "ACME"})
        KeycloakRole.objects.create(
            name="ROLE_ACME_READER", organization=org,
            level=KeycloakRole.Level.READER, source=KeycloakRole.Source.LOGIN,
        )

    def test_runs_each_engine(self):
        self._catalog()
        client = MagicMock()
        client.get_roles.return_value = []
        client.create_role.return_value = OperationResult(success=True)

        results = reconcile_all_engines(engines=[_fake_engine(client, "E1")])

        (engine, summary, error) = results[0]
        assert error is None
        assert summary["created"] == ["ROLE_ACME_READER"]

    def test_one_engine_failure_isolated(self):
        self._catalog()
        down = MagicMock()
        down.get_roles.side_effect = RuntimeError("GeoServer down")
        ok = MagicMock()
        ok.get_roles.return_value = ["ROLE_ACME_READER"]

        results = reconcile_all_engines(
            engines=[_fake_engine(down, "Down"), _fake_engine(ok, "Ok")]
        )

        errors = {e.name: err for e, _, err in results}
        assert errors["Down"] == "GeoServer down"
        assert errors["Ok"] is None
