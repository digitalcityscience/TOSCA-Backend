#!/usr/bin/env python
"""
Console CRUD integration script — Engine + Workspace lifecycle.

Creates "pytest-geoserver" engine, creates "pytest-workspace" under it,
then tears both down in reverse order via the DRF API.

No Django test runner needed — runs directly against the dev DB + live GeoServer.

Run inside Docker:
    make test-console-crud
"""

import os
import sys

sys.path.append("/app")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tosca_api.settings.development")

import django
django.setup()

from django.contrib.auth.models import User
from rest_framework.test import APIClient

ENGINES_URL    = "/api/geoengine/engines/"
WORKSPACES_URL = "/api/geoengine/workspaces/"

ENGINE_NAME = "pytest-geoserver"
WS_NAME     = "pytest-workspace"

GEOSERVER_URL = (
    f"http://{os.getenv('GEOSERVER_HOST', 'geoserver')}:"
    f"{os.getenv('GEOSERVER_PORT', '8080')}/geoserver"
)

PASS = "\033[32m✅\033[0m"
FAIL = "\033[31m❌\033[0m"


def assert_status(resp, *expected, label=""):
    if resp.status_code not in expected:
        print(f"{FAIL} {label} — expected {expected}, got {resp.status_code}: {resp.data}")
        sys.exit(1)
    print(f"{PASS} {label} ({resp.status_code})")


def run():
    # Use or create a temporary test user — cleaned up at the end
    user, created = User.objects.get_or_create(
        username="pytestuser",
        defaults={"email": "pytest@localhost"},
    )
    if created:
        user.set_password("pass")
        user.save()

    api = APIClient()
    api.force_authenticate(user=user)

    print(f"\n{'─'*55}")
    print(f"  Console CRUD test  |  GeoServer: {GEOSERVER_URL}")
    print(f"{'─'*55}\n")

    # 1. Create engine
    resp = api.post(ENGINES_URL, {
        "name":           ENGINE_NAME,
        "description":    "Created by pytest — safe to delete",
        "engine_type":    "geoserver",
        "base_url":       GEOSERVER_URL,
        "admin_username": os.getenv("GEOSERVER_ADMIN_USER", "admin"),
        "admin_password": os.getenv("GEOSERVER_ADMIN_PASSWORD", "geoserver"),
        "is_active":      True,
        "is_default":     False,
    }, format="json")
    assert_status(resp, 201, label=f"CREATE engine '{ENGINE_NAME}'")
    engine_id = resp.data["engine"]["id"]

    # 2. Create workspace
    resp = api.post(WORKSPACES_URL, {
        "geodata_engine": engine_id,
        "name":           WS_NAME,
        "description":    "Created by pytest — safe to delete",
    }, format="json")
    assert_status(resp, 201, 200, label=f"CREATE workspace '{WS_NAME}'")
    ws_id = resp.data["workspace"]["id"]

    # 3. Delete workspace
    resp = api.delete(f"{WORKSPACES_URL}{ws_id}/")
    assert_status(resp, 200, label=f"DELETE workspace '{WS_NAME}'")

    # 4. Delete engine
    resp = api.delete(f"{ENGINES_URL}{engine_id}/")
    assert_status(resp, 204, label=f"DELETE engine '{ENGINE_NAME}'")

    # Cleanup temp user if we created it
    if created:
        user.delete()

    print(f"\n  🎉 All steps passed — engine + workspace CRUD OK\n")


if __name__ == "__main__":
    run()

