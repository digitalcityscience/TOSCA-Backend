"""``seed_external_catalog_hamburg`` (external catalog ticket 07), offline."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from tosca_api.apps.external_catalog import harvest
from tosca_api.apps.external_catalog.management.commands import seed_external_catalog_hamburg as seed
from tosca_api.apps.external_catalog.models import Category, CategoryItem, ExternalService, ServiceSource, Visibility
from tosca_api.apps.external_catalog.tests.test_admin import _org, _service, _staff_client

STA_EXTRA_LAYERS = ["Leistung", "Spannung"]  # "every layer" of the energy campus


@pytest.fixture
def offline_catalog(monkeypatch):
    """harvest.run fills the index with every demo source (minus ``missing``)."""
    missing: set[tuple[str, str]] = set()

    def fake_run(service):
        rows = []
        for _slug, _title, _color, entries in seed.CATEGORIES:
            for service_slug, dataset_id, source_id, _zoom in entries:
                if service_slug != service.slug:
                    continue
                for layer in [source_id] if source_id else STA_EXTRA_LAYERS:
                    if (dataset_id, layer) not in missing:
                        rows.append((dataset_id, layer))
        for dataset_id, source_id in dict.fromkeys(rows):
            ServiceSource.objects.update_or_create(
                service=service,
                dataset_id=dataset_id,
                source_id=source_id,
                defaults={"title": f"{source_id} (remote)", "dataset_title": dataset_id, "harvested_at": timezone.now()},
            )
        ExternalService.objects.filter(pk=service.pk).update(catalog_loaded_at=timezone.now())
        service.catalog_loaded_at = timezone.now()
        return rows

    monkeypatch.setattr(harvest, "run", fake_run)
    return missing


@pytest.fixture
def creator():
    _, user = _staff_client("seeder", "dcs")
    user.is_superuser = True
    user.save()
    return user


@pytest.mark.django_db
def test_seed_creates_private_services_categories_and_items(offline_catalog, creator):
    offline_catalog.add(("lastenradbuegel_eimsbuettel", "lastenradbuegel"))

    call_command("seed_external_catalog_hamburg", organization="dcs")

    services = {service.slug: service for service in ExternalService.objects.all()}
    assert set(services) == {seed.OGC, seed.STA}
    assert services[seed.STA].mqtt_url == "wss://iot.hamburg.de/mqtt"
    assert all(service.visibility == Visibility.PRIVATE for service in services.values())
    assert list(Category.objects.order_by("display_order").values_list("title", flat=True)) == [
        title for _slug, title, _color, _entries in seed.CATEGORIES
    ]
    routing = Category.objects.get(slug="hamburg-routing-network")
    assert routing.items.count() == 9
    linien = routing.items.get(ogc_collection_id="linien")
    assert (linien.title, linien.min_zoom, linien.color) == ("linien (remote)", Decimal("14.0"), "#6d4c41")
    energy = Category.objects.get(slug="hamburg-environment-utilities")
    assert sorted(energy.items.exclude(sta_layer_name="").values_list("sta_layer_name", flat=True)) == STA_EXTRA_LAYERS
    assert not CategoryItem.objects.filter(ogc_collection_id="lastenradbuegel").exists()
    assert list(routing.items.order_by("display_order").values_list("display_order", flat=True)) == list(range(9))


@pytest.mark.django_db
def test_seed_is_idempotent_and_keeps_admin_edits(offline_catalog, creator):
    call_command("seed_external_catalog_hamburg", organization="dcs")
    count = CategoryItem.objects.count()
    Category.objects.filter(slug="hamburg-traffic-situation").update(title="Edited", visibility=Visibility.PUBLIC)
    CategoryItem.objects.filter(ogc_collection_id="baustelle").delete()

    call_command("seed_external_catalog_hamburg", organization="dcs", skip_catalog_load=True)

    assert CategoryItem.objects.count() == count  # only the deleted item came back
    category = Category.objects.get(slug="hamburg-traffic-situation")
    assert (category.title, category.visibility) == ("Edited", Visibility.PUBLIC)


@pytest.mark.django_db
def test_dry_run_saves_nothing(offline_catalog, creator):
    call_command("seed_external_catalog_hamburg", organization="dcs", dry_run=True)

    assert not ExternalService.objects.exists()
    assert not Category.objects.exists()


@pytest.mark.django_db
def test_seed_refuses_foreign_rows_unknown_orgs_and_unloaded_catalogs(offline_catalog, creator):
    with pytest.raises(CommandError, match="No organization"):
        call_command("seed_external_catalog_hamburg", organization="nope")
    with pytest.raises(CommandError, match="catalog not loaded"):
        call_command("seed_external_catalog_hamburg", organization="dcs", skip_catalog_load=True)
    assert not ExternalService.objects.exists()  # rolled back

    _service(_org("other"), creator, seed.OGC)
    with pytest.raises(CommandError, match="another organization"):
        call_command("seed_external_catalog_hamburg", organization="dcs")
