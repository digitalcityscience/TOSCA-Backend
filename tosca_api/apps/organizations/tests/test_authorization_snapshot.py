"""Tests for UserAuthorizationSnapshot (security tickets ticket 04).

Additive only: nothing writes or reads this table at request time yet (see
module docstring on the model). Covers the model itself, the read-only admin
surface, and the TTL/invalidation seams ticket 05/06 will wire up.
"""

from __future__ import annotations

import pytest
from django.contrib.admin.sites import AdminSite
from django.db import IntegrityError
from django.test import RequestFactory
from django.utils import timezone

from tosca_api.apps.organizations.admin import UserAuthorizationSnapshotAdmin
from tosca_api.apps.organizations.models import UserAuthorizationSnapshot
from tosca_api.apps.organizations.policy import _load_valid_snapshot, invalidate_snapshot


@pytest.fixture
def user(django_user_model, db):
    return django_user_model.objects.create_user(username="snapshot-user", password="x")


@pytest.mark.django_db
def test_snapshot_stores_org_roles_and_default_org(user):
    snapshot = UserAuthorizationSnapshot.objects.create(
        user=user,
        org_roles={"dcs": "ADMIN", "qg2": "WRITER"},
        default_org="dcs",
        synced_at=timezone.now(),
    )

    snapshot.refresh_from_db()
    assert snapshot.org_roles == {"dcs": "ADMIN", "qg2": "WRITER"}
    assert snapshot.default_org == "dcs"
    assert str(snapshot) == f"{user}: dcs"


@pytest.mark.django_db
def test_snapshot_str_without_default_org(user):
    snapshot = UserAuthorizationSnapshot.objects.create(
        user=user, org_roles={}, synced_at=timezone.now()
    )

    assert str(snapshot) == f"{user}: (no default org)"


@pytest.mark.django_db
def test_snapshot_is_one_per_user(user):
    UserAuthorizationSnapshot.objects.create(user=user, synced_at=timezone.now())

    with pytest.raises(IntegrityError):
        UserAuthorizationSnapshot.objects.create(user=user, synced_at=timezone.now())


@pytest.mark.django_db
def test_load_valid_snapshot_returns_none_when_absent(user):
    assert _load_valid_snapshot(user) is None


@pytest.mark.django_db
def test_load_valid_snapshot_returns_existing_row(user):
    snapshot = UserAuthorizationSnapshot.objects.create(
        user=user, org_roles={"dcs": "READER"}, default_org="dcs", synced_at=timezone.now()
    )

    assert _load_valid_snapshot(user) == snapshot


@pytest.mark.django_db
def test_invalidate_snapshot_deletes_the_row(user):
    UserAuthorizationSnapshot.objects.create(user=user, synced_at=timezone.now())

    invalidate_snapshot(user)

    assert not UserAuthorizationSnapshot.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_invalidate_snapshot_is_a_noop_when_absent(user):
    invalidate_snapshot(user)  # must not raise

    assert not UserAuthorizationSnapshot.objects.filter(user=user).exists()


@pytest.fixture
def snapshot_admin():
    return UserAuthorizationSnapshotAdmin(UserAuthorizationSnapshot, AdminSite())


@pytest.mark.django_db
def test_snapshot_admin_denies_add(django_user_model, snapshot_admin):
    superuser = django_user_model.objects.create_superuser(
        username="root-snapshot", email="root@example.com", password="x"
    )
    request = RequestFactory().get("/fake/")
    request.user = superuser

    assert snapshot_admin.has_add_permission(request) is False


@pytest.mark.django_db
def test_snapshot_admin_denies_change(django_user_model, snapshot_admin, user):
    superuser = django_user_model.objects.create_superuser(
        username="root-snapshot-2", email="root@example.com", password="x"
    )
    request = RequestFactory().get("/fake/")
    request.user = superuser
    snapshot = UserAuthorizationSnapshot.objects.create(user=user, synced_at=timezone.now())

    assert snapshot_admin.has_change_permission(request, snapshot) is False


@pytest.mark.django_db
def test_snapshot_admin_changelist_renders(client, django_user_model, user):
    superuser = django_user_model.objects.create_superuser(
        username="root-snapshot-http", email="root@example.com", password="x"
    )
    client.force_login(superuser)
    UserAuthorizationSnapshot.objects.create(
        user=user, org_roles={"dcs": "ADMIN"}, default_org="dcs", synced_at=timezone.now()
    )

    response = client.get("/admin/organizations/userauthorizationsnapshot/")

    assert response.status_code == 200
    assert b"dcs" in response.content
