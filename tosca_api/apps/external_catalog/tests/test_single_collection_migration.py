"""0003: items with several collections become one item per collection.

Runs inside the normal test transaction (rolled back afterwards; a
transactional test would flush the reused test database). Deferred FK
checks are forced between steps because Postgres refuses ALTER TABLE while
inserted rows still have pending trigger events.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = [("external_catalog", "0002_seed_external_catalog_entitlements")]
SPLIT = [("external_catalog", "0003_single_collection")]
AFTER = [("external_catalog", "0004_remove_categoryitem_ogc_collection_ids")]


def _migrate(targets):
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.mark.django_db
def test_merged_items_are_split_into_one_item_per_collection():
    old = _migrate(BEFORE)
    user = old.get_model("auth", "User").objects.create(username="migrator")
    org = old.get_model("organizations", "Organization").objects.create(name="Org", slug="org")
    service = old.get_model("external_catalog", "ExternalService").objects.create(
        organization=org,
        name="ogc",
        slug="ogc",
        service_type="ogc_api_features",
        base_url="https://api.example.test",
        title="OGC",
        created_by=user,
    )
    category = old.get_model("external_catalog", "Category").objects.create(
        organization=org, slug="mobility", title="Mobility", created_by=user
    )
    Item = old.get_model("external_catalog", "CategoryItem")
    common = {"category": category, "service": service, "created_by": user}
    Item.objects.create(title="PrioBike", display_order=5, ogc_collection_ids=["ampel", "welle"], **common)
    Item.objects.create(title="Single", display_order=1, ogc_collection_ids=["one"], **common)

    _migrate(SPLIT)
    new = _migrate(AFTER)

    rows = sorted(
        new.get_model("external_catalog", "CategoryItem").objects.values_list(
            "title", "display_order", "ogc_collection_id"
        )
    )
    assert rows == [("PrioBike", 5, "ampel"), ("PrioBike (welle)", 6, "welle"), ("Single", 1, "one")]
