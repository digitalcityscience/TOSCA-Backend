"""
Test to verify PostGIS extension is enabled in the database.

These tests verify the PostGIS extension is properly installed and accessible.
They run against the live database (not a test database) since they are
infrastructure verification tests, not application tests.

Usage:
    pytest tosca_api/apps/core/tests/test_postgis.py -v --ds=tosca_api.settings.base
"""

import pytest
from django.db import connection


@pytest.fixture
def db_access_without_rollback(request, django_db_setup, django_db_blocker):
    """
    Allow database access without test database creation.
    Uses the actual database for infrastructure verification.
    """
    django_db_blocker.unblock()
    request.addfinalizer(django_db_blocker.restore)


def test_postgis_extension_enabled(db_access_without_rollback):
    """Verify PostGIS extension is available and returns a valid version."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT PostGIS_Version();")
        version = cursor.fetchone()[0]
        assert version is not None, "PostGIS_Version() returned None"
        # PostGIS 3.x expected (based on docker image postgis/postgis:16-3.4)
        assert "3." in version, f"Expected PostGIS 3.x, got: {version}"


def test_postgis_full_version(db_access_without_rollback):
    """Verify PostGIS_Full_Version() returns extended version info."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT PostGIS_Full_Version();")
        full_version = cursor.fetchone()[0]
        assert full_version is not None
        # Should contain GDAL, GEOS, PROJ info
        assert "GEOS" in full_version, "GEOS not found in PostGIS_Full_Version()"
        assert "PROJ" in full_version, "PROJ not found in PostGIS_Full_Version()"
