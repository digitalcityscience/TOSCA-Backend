"""Seed the Hamburg services and the Smart City Explorer demo categories.

For local development and demos: gives the frontend realistic external data.
Idempotent -- existing services and categories are reused, admin edits are
never overwritten, and only missing items are added. Item titles come from the
services' catalogs (loaded first), as for items added in the admin.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from tosca_api.apps.external_catalog import harvest, remote
from tosca_api.apps.external_catalog.models import (
    Category,
    CategoryItem,
    ExternalService,
    ServiceSource,
    Visibility,
)
from tosca_api.apps.organizations.models import Organization

OGC = "hamburg-ogc"
STA = "hamburg-sensorthings"

SERVICES = {
    OGC: {
        "name": "Hamburg OGC API",
        "title": "Hamburg OGC API",
        "service_type": ExternalService.ServiceType.OGC_API_FEATURES,
        "base_url": "https://api.hamburg.de/datasets/v1",
        "attribution": "Freie und Hansestadt Hamburg",
        "description": "Open geodata of the City of Hamburg (OGC API Features).",
    },
    STA: {
        "name": "Hamburg SensorThings",
        "title": "Hamburg SensorThings",
        "service_type": ExternalService.ServiceType.SENSORTHINGS,
        "base_url": "https://iot.hamburg.de",
        "mqtt_url": "wss://iot.hamburg.de/mqtt",
        "attribution": "Urban Data Platform Hamburg",
        "description": "Live sensor data of the City of Hamburg (OGC SensorThings API).",
    },
}

# (slug, title, colour, items). Item: (service, dataset id / serviceName,
# collection id / layerName, min zoom). A layer name of None means "every
# layer of this serviceName".
CATEGORIES = [
    (
        "hamburg-traffic-situation",
        "Traffic situation",
        "#e53935",
        [
            (OGC, "baustellen", "baustelle", None),
            (OGC, "verkehrslage", "verkehrslage", 14),
            (OGC, "verkehrsinformation", "hauptmeldungen_aktuell", None),
        ],
    ),
    (
        "hamburg-traffic-live",
        "Traffic live",
        "#fb8c00",
        [
            (STA, "HH_STA_Verkehrsdaten_Kfz_Infrarotdetektoren", "Anzahl_Kfz_Zaehlstelle_15-Min", None),
            (STA, "HH_STA_Verkehrsdaten_Rad_Infrarotdetektoren", "Anzahl_Fahrraeder_Zaehlstelle_15-Min", None),
            (STA, "HH_STA_HamburgerRadzaehlnetz", "Anzahl_Fahrraeder_Zaehlstelle_15-Min", None),
        ],
    ),
    (
        "hamburg-traffic-monitoring",
        "Traffic monitoring",
        "#5e35b1",
        [
            (OGC, "verkehrskameras", "verkehr_kameras_internet", None),
            (OGC, "verkehrszaehlstellen", "kfz_zaehlstellen", None),
            (OGC, "verkehrszaehlstellen", "radverkehr_zaehlstellen", None),
            (OGC, "verkehrszaehlstellen", "fussverkehr_zaehlstellen", None),
            (OGC, "dauerzaehlstellen_rad", "dauerzaehlstellen_rad", None),
        ],
    ),
    (
        "hamburg-bike-and-curbside",
        "Bike & curbside",
        "#43a047",
        [
            (OGC, "bike_und_ride", "bike_und_ride", None),
            (OGC, "escooter", "abstellflaechen_e_scooter", None),
            (OGC, "escooter", "parkverbotszonen_e_scooter", None),
            (OGC, "lastenradbuegel_eimsbuettel", "lastenradbuegel", None),
        ],
    ),
    (
        "hamburg-shared-mobility",
        "Shared mobility",
        "#0288d1",
        [
            (OGC, "stadtrad", "stadtrad_stationen", None),
            (STA, "HH_STA_E-Ladestationen", "Status_E-Ladepunkt", None),
        ],
    ),
    (
        "hamburg-environment-utilities",
        "Environment & utilities",
        "#00897b",
        [
            (OGC, "swis_sensoren", "swis_sensoren", None),
            (STA, "HH_STA_TEC_Energiedaten_HH-Bergedorf", None, None),
        ],
    ),
    (
        "hamburg-heat-planning",
        "Heat-planning overlays",
        "#d81b60",
        [
            (OGC, "abwaermequellen_kwp", "abwaermequellen", None),
            (OGC, "abwasserleitungen_waermequelle_kwp", "abwasserleitung_waermequelle", None),
            (OGC, "gebaeudestruktur_kwp", "gebaeudestruktur", 14),
        ],
    ),
    (
        "hamburg-signal-infrastructure",
        "Signal infrastructure",
        "#3949ab",
        [
            (OGC, "lichtsignalanlagen", "lsa_knotengrunddaten", None),
            (OGC, "lichtsignalanlagen_hafen", "lichtsignalanlagen_hafen", None),
            (OGC, "its_dienste_hamburg", "its_iot_registry", None),
        ],
    ),
    (
        "hamburg-routing-network",
        "Routing & network",
        "#6d4c41",
        [
            (OGC, "priobike", "ampelschaltung", None),
            (OGC, "priobike", "geschwindigkeitsempfehlung", None),
            (OGC, "priobike", "gruene_welle", None),
            (OGC, "priobike", "sicherheitshinweis", None),
            (OGC, "automatisiertes_fahren", "center_line", None),
            (OGC, "automatisiertes_fahren", "crossing_internal_lanes", None),
            (OGC, "automatisiertes_fahren", "separator_lines", None),
            (OGC, "feinkartierung_strasse", "linien", 14),
            (OGC, "bedarfsumleitungen", "bedarfsumleitungen", None),
        ],
    ),
    (
        "hamburg-traffic-statistics",
        "Traffic statistics",
        "#546e7a",
        [
            (OGC, "verkehrsstaerken", "verkehrsstaerken_dtv_dtvw", None),
            (OGC, "verkehrsstaerken", "radverkehr_dtv_dtvw", None),
            (OGC, "dbradplus", "aktuelles_jahr", 14),
            (OGC, "stadtradeln", "stadtradeln2020", 13),
        ],
    ),
]


class DryRun(Exception):
    """Raised to roll back a --dry-run after reporting."""


class Command(BaseCommand):
    help = (
        "Create the Hamburg OGC API and SensorThings services and the Smart City Explorer demo "
        "categories (private by default). Safe to run again: only missing rows are added; "
        "--public also makes the existing seeded services and categories public."
    )

    def add_arguments(self, parser):
        parser.add_argument("--organization", required=True, help="Organization slug that owns the rows.")
        parser.add_argument("--user", help="Username recorded as creator (default: first superuser).")
        parser.add_argument(
            "--public",
            action="store_true",
            help="Make the seeded services and categories public (new and existing ones).",
        )
        parser.add_argument(
            "--skip-catalog-load",
            action="store_true",
            help="Use already loaded catalogs instead of loading them now (about 2 minutes for Hamburg).",
        )
        parser.add_argument("--dry-run", action="store_true", help="Report what would happen, then roll back.")

    def handle(self, *args, **options):
        organization = Organization.objects.filter(slug=options["organization"]).first()
        if organization is None:
            raise CommandError(f"No organization with slug '{options['organization']}'.")
        user = self._user(options["user"])
        visibility = Visibility.PUBLIC if options["public"] else Visibility.PRIVATE
        self.publish = options["public"]
        try:
            with transaction.atomic():
                self._seed(organization, user, visibility, load=not options["skip_catalog_load"])
                if options["dry_run"]:
                    raise DryRun
        except DryRun:
            self.stdout.write(self.style.WARNING("Dry run: nothing was saved."))

    def _user(self, username):
        users = get_user_model().objects
        user = users.filter(username=username).first() if username else users.filter(is_superuser=True).first()
        if user is None:
            raise CommandError("No user to record as creator; pass --user.")
        return user

    def _seed(self, organization, user, visibility, *, load: bool) -> None:
        services = {slug: self._service(slug, organization, user, visibility) for slug in SERVICES}
        for service in services.values():
            if load:
                self.stdout.write(f"Loading the catalog of {service.title}…")
                harvest.claim(service)
                try:
                    harvest.run(service)
                except remote.RemoteServiceError as exc:
                    raise CommandError(f"{service.title}: {exc}") from exc
            elif not service.catalog_loaded_at:
                raise CommandError(f"{service.title}: catalog not loaded; run without --skip-catalog-load.")

        created_items = skipped = 0
        for order, (slug, title, color, entries) in enumerate(CATEGORIES):
            category = self._category(slug, title, order, organization, user, visibility)
            existing = {
                (item.service_id, *harvest.item_source_key(item)) for item in category.items.select_related("service")
            }
            position = category.items.count()
            for service_slug, dataset_id, source_id, min_zoom in entries:
                service = services[service_slug]
                sources = ServiceSource.objects.filter(service=service, dataset_id=dataset_id, geojson=True)
                if source_id is not None:
                    sources = sources.filter(source_id=source_id)
                sources = list(sources.select_related("service").order_by("title"))
                if not sources:
                    skipped += 1
                    self.stderr.write(f"  {title}: {service_slug} {dataset_id}/{source_id or '*'} not offered; skipped.")
                    continue
                for source in sources:
                    if (service.pk, source.dataset_id, source.source_id) in existing:
                        continue
                    CategoryItem(
                        category=category,
                        service=service,
                        display_order=position,
                        color=color,
                        min_zoom=Decimal(min_zoom) if min_zoom is not None else None,
                        created_by=user,
                        **harvest.source_fields(source),
                        **harvest.item_values(source),
                    ).save()
                    position += 1
                    created_items += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"{len(CATEGORIES)} categories, {created_items} items added"
                + (f", {skipped} sources skipped" if skipped else "")
                + "."
            )
        )

    def _service(self, slug, organization, user, visibility) -> ExternalService:
        service = ExternalService.objects.filter(slug=slug).first()
        if service is not None:
            if service.organization_id != organization.pk:
                raise CommandError(f"Service '{slug}' already exists in another organization.")
            if self.publish and service.visibility != Visibility.PUBLIC:
                ExternalService.objects.filter(pk=service.pk).update(visibility=Visibility.PUBLIC)
                service.visibility = Visibility.PUBLIC
                self.stdout.write(f"Service '{slug}' exists; made public.")
            else:
                self.stdout.write(f"Service '{slug}' exists; reused unchanged.")
            return service
        service = ExternalService.objects.create(
            organization=organization, slug=slug, visibility=visibility, created_by=user, **SERVICES[slug]
        )
        self.stdout.write(f"Service '{slug}' created.")
        return service

    def _category(self, slug, title, order, organization, user, visibility) -> Category:
        category = Category.objects.filter(slug=slug).first()
        if category is not None:
            if category.organization_id != organization.pk:
                raise CommandError(f"Category '{slug}' already exists in another organization.")
            if self.publish and category.visibility != Visibility.PUBLIC:
                Category.objects.filter(pk=category.pk).update(visibility=Visibility.PUBLIC)
                category.visibility = Visibility.PUBLIC
                self.stdout.write(f"Category '{slug}' made public.")
            return category
        return Category.objects.create(
            organization=organization,
            slug=slug,
            title=title,
            display_order=order,
            visibility=visibility,
            created_by=user,
        )
