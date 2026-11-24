"""Migration core.0006 creates the database cache table on ``migrate``."""

from importlib import import_module

import pytest
from django.db import connection
from django.test import override_settings

pytestmark = pytest.mark.django_db

migration = import_module("tosca_api.apps.core.migrations.0006_create_cache_table")


DB_CACHE = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "tosca_cache"}}


@override_settings(CACHES=DB_CACHE)
def test_creates_the_cache_table_and_is_idempotent():
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS tosca_cache")

    migration.create_cache_table(None, _Editor())
    migration.create_cache_table(None, _Editor())  # second run: no error

    assert "tosca_cache" in connection.introspection.table_names()


class _Editor:
    connection = connection


def test_is_a_no_op_for_other_cache_backends():
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS tosca_cache")

    with override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}):
        migration.create_cache_table(None, _Editor())

    assert "tosca_cache" not in connection.introspection.table_names()
