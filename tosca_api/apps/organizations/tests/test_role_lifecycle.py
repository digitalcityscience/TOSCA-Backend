"""Tests for the Organization slug-rename / deletion role lifecycle (Epic 11).

Covers ``organizations.signals``:
- a slug rename deactivates the org's stale catalog roles (no GeoServer I/O);
- deleting an org mirrors its reader/writer role deletion to GeoServer, is
  best-effort, and never blocks the delete.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import transaction

from tosca_api.apps.authentication.models import KeycloakRole
from tosca_api.apps.geodata_providers.role_sync import (
    GeoServerRoleCleanupError,
    org_reader_writer_names,
)
from tosca_api.apps.organizations.models import Organization

_RECONCILE = "tosca_api.apps.geodata_providers.role_sync.reconcile_all_engines"


def _role(org, name, level, active=True):
    return KeycloakRole.objects.create(
        name=name,
        organization=org,
        level=level,
        source=KeycloakRole.Source.LOGIN,
        is_active=active,
    )


@pytest.mark.django_db
class TestOrgReaderWriterNames:
    def test_only_active_reader_writer_of_that_org(self):
        acme, _ = Organization.objects.get_or_create(slug="acme", defaults={"name": "ACME"})
        other, _ = Organization.objects.get_or_create(slug="beta", defaults={"name": "BETA"})
        _role(acme, "ROLE_ACME_READER", KeycloakRole.Level.READER)
        _role(acme, "ROLE_ACME_WRITER", KeycloakRole.Level.WRITER)
        _role(acme, "ROLE_ACME_ADMIN", KeycloakRole.Level.ADMIN)  # admin excluded
        _role(acme, "ROLE_ACME_OLD_READER", KeycloakRole.Level.READER, active=False)  # inactive
        _role(other, "ROLE_BETA_READER", KeycloakRole.Level.READER)  # other org

        assert org_reader_writer_names(acme) == {"ROLE_ACME_READER", "ROLE_ACME_WRITER"}


@pytest.mark.django_db
class TestSlugRename:
    def test_rename_deactivates_stale_roles(self):
        org = Organization.objects.create(slug="acme", name="ACME")
        reader = _role(org, "ROLE_ACME_READER", KeycloakRole.Level.READER)
        writer = _role(org, "ROLE_ACME_WRITER", KeycloakRole.Level.WRITER)

        org.slug = "acme2"
        org.save()

        reader.refresh_from_db()
        writer.refresh_from_db()
        assert reader.is_active is False
        assert writer.is_active is False

    def test_rename_does_not_touch_already_matching_roles(self):
        # A role that already carries the *new* prefix must stay active.
        org = Organization.objects.create(slug="acme", name="ACME")
        stale = _role(org, "ROLE_ACME_READER", KeycloakRole.Level.READER)
        already_new = _role(org, "ROLE_ACME2_READER", KeycloakRole.Level.READER)

        org.slug = "acme2"
        org.save()

        stale.refresh_from_db()
        already_new.refresh_from_db()
        assert stale.is_active is False
        assert already_new.is_active is True

    def test_non_slug_edit_is_noop(self):
        org = Organization.objects.create(slug="acme", name="ACME")
        reader = _role(org, "ROLE_ACME_READER", KeycloakRole.Level.READER)

        org.name = "ACME Renamed"
        org.save()

        reader.refresh_from_db()
        assert reader.is_active is True

    def test_create_is_noop(self):
        # Creating an org (no prior slug) must not error or deactivate anything.
        org = Organization.objects.create(slug="acme", name="ACME")
        reader = _role(org, "ROLE_ACME_READER", KeycloakRole.Level.READER)
        reader.refresh_from_db()
        assert reader.is_active is True


@pytest.mark.django_db
class TestDeletionMirroring:
    def test_delete_mirrors_reader_writer_deletion_to_geoserver(self):
        org = Organization.objects.create(slug="acme", name="ACME")
        _role(org, "ROLE_ACME_READER", KeycloakRole.Level.READER)
        _role(org, "ROLE_ACME_WRITER", KeycloakRole.Level.WRITER)
        _role(org, "ROLE_ACME_ADMIN", KeycloakRole.Level.ADMIN)  # not pushed/deleted

        # No engines reachable-with-error -> clean run, delete proceeds.
        with patch(_RECONCILE, return_value=[]) as reconcile:
            org.delete()

        reconcile.assert_called_once()
        _, kwargs = reconcile.call_args
        assert kwargs["ensure"] == set()
        assert kwargs["delete"] == {"ROLE_ACME_READER", "ROLE_ACME_WRITER"}
        assert not Organization.objects.filter(slug="acme").exists()

    def test_delete_with_no_roles_skips_reconcile(self):
        org = Organization.objects.create(slug="acme", name="ACME")

        with patch(_RECONCILE, return_value=[]) as reconcile:
            org.delete()

        reconcile.assert_not_called()
        assert not Organization.objects.filter(slug="acme").exists()

    def test_geoserver_failure_blocks_delete(self):
        # If GeoServer can't be reached, the delete is aborted and the row stays.
        org = Organization.objects.create(slug="acme", name="ACME")
        _role(org, "ROLE_ACME_READER", KeycloakRole.Level.READER)

        engine = SimpleNamespace(name="Primary")
        with patch(_RECONCILE, return_value=[(engine, None, "GeoServer down")]):
            # atomic() contains the rollback to a savepoint so the surrounding
            # test transaction stays usable after the expected raise.
            with pytest.raises(GeoServerRoleCleanupError):
                with transaction.atomic():
                    org.delete()

        assert Organization.objects.filter(slug="acme").exists()
        assert org.keycloak_roles.filter(is_active=True).exists()

    def test_delete_blocked_when_a_role_delete_fails(self):
        org = Organization.objects.create(slug="acme", name="ACME")
        _role(org, "ROLE_ACME_READER", KeycloakRole.Level.READER)

        engine = SimpleNamespace(name="Primary")
        summary = {"failed": [("ROLE_ACME_READER", "HTTP 500")]}
        with patch(_RECONCILE, return_value=[(engine, summary, None)]):
            with pytest.raises(GeoServerRoleCleanupError):
                with transaction.atomic():
                    org.delete()

        assert Organization.objects.filter(slug="acme").exists()


@pytest.mark.django_db
class TestDeletionAdminMessage:
    def test_admin_delete_cancelled_with_friendly_message_on_geoserver_error(
        self, client, django_user_model
    ):
        superuser = django_user_model.objects.create_superuser(
            username="root-del", email="root@example.com", password="x"
        )
        client.force_login(superuser)
        org = Organization.objects.create(slug="acme", name="ACME")
        _role(org, "ROLE_ACME_READER", KeycloakRole.Level.READER)

        engine = SimpleNamespace(name="Primary")
        url = f"/admin/organizations/organization/{org.pk}/delete/"
        with patch(_RECONCILE, return_value=[(engine, None, "GeoServer down")]):
            response = client.post(url, {"post": "yes"}, follow=True)

        # Delete cancelled: row survives, no 500, an error message is shown.
        assert Organization.objects.filter(pk=org.pk).exists()
        messages = [m.message for m in response.context["messages"]]
        assert any("Silme iptal edildi" in m for m in messages)
