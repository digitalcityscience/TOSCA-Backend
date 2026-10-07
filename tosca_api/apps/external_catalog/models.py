"""
External catalog domain models.

Curated access to *remote*, publicly reachable geodata services (OGC API
Features, SensorThings) that TOSCA does not publish itself. Deliberately
separate from ``geodata_providers``: nothing here has a store, a table, a
publishing state machine or a GeoServer ACL -- the browser fetches features
and observations straight from the remote service, and these rows only
describe *what TOSCA lists* and *what the app lets users do with it*.

Visibility is presentation control, not data protection: the remote data is
public. ``PRIVATE`` rows are not listed to anonymous users; signed-in users
see them (external catalog tickets, 2026-10-07).

As in ``geodata_providers``, every ``save`` runs ``full_clean`` so
programmatic creates surface the same errors as the admin, and cross-FK
invariants live in ``clean``.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

from tosca_api.apps.core.models import TimeStampedModel

HEX_COLOR_VALIDATOR = RegexValidator(
    regex=r"^#[0-9a-fA-F]{6}$",
    message="Use a 6-digit hex color such as #0288d1.",
)

# Operators of the frontend's OGC filter builder (``ogcCql2.ts``). Stored
# conditions must use the same vocabulary so the frontend can apply them
# as the layer's initial query unchanged.
FILTER_OPERATORS = frozenset({"eq", "neq", "lt", "lte", "gt", "gte", "contains"})


class Visibility(models.TextChoices):
    PUBLIC = "PUBLIC", "Public"
    PRIVATE = "PRIVATE", "Private (signed-in users only)"


VISIBILITY_HELP_TEXT = (
    "PUBLIC: listed to everyone. PRIVATE: not listed to anonymous users, listed "
    "to signed-in users. This controls what TOSCA lists; the remote data itself "
    "stays publicly reachable."
)


def _require_url_scheme(value: str, allowed: tuple[str, ...], dev_allowed: tuple[str, ...]):
    """Return an error message unless ``value`` uses an allowed scheme.

    ``dev_allowed`` schemes (plain http/ws) are only accepted with
    ``DEBUG`` so production never lists a service over an insecure channel.
    """
    scheme = value.split("://", 1)[0].lower() if "://" in value else ""
    permitted = allowed + (dev_allowed if settings.DEBUG else ())
    if scheme not in permitted:
        return f"Use a {' or '.join(f'{s}://' for s in permitted)} URL."
    return None


class ExternalService(TimeStampedModel):
    """A remote service whose data TOSCA lists (one per base URL)."""

    class ServiceType(models.TextChoices):
        OGC_API_FEATURES = "ogc_api_features", "OGC API Features"
        SENSORTHINGS = "sensorthings", "OGC SensorThings API"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="external_services",
        help_text="Owning organization; scopes who may manage this service.",
    )
    name = models.CharField(max_length=100, help_text="Admin label")
    slug = models.SlugField(
        max_length=100,
        unique=True,
        help_text="Stable public identifier used by the catalog API.",
    )
    service_type = models.CharField(
        max_length=32,
        choices=ServiceType.choices,
        help_text="Supported service type. New types need dedicated frontend support.",
    )
    base_url = models.URLField(
        max_length=500,
        help_text=(
            "OGC API: landing page or multi-API catalog root. SensorThings: service "
            "root, with or without the /v1.x version segment."
        ),
    )
    mqtt_url = models.CharField(
        max_length=500,
        blank=True,
        help_text="SensorThings only: MQTT-over-WebSocket endpoint (wss://…) for live values.",
    )
    title = models.CharField(max_length=200, help_text="Title shown to users")
    attribution = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    visibility = models.CharField(
        max_length=20,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
        db_index=True,
        help_text=VISIBILITY_HELP_TEXT,
    )
    is_active = models.BooleanField(default=True, db_index=True)

    # Capabilities: what the app offers for this service.
    show_uncurated = models.BooleanField(
        default=False,
        help_text="Also offer the service's full dataset/layer list besides curated categories.",
    )
    allow_full_load = models.BooleanField(
        default=True,
        help_text="OGC API: allow 'Load all' (downloading every matching feature).",
    )
    allow_live_updates = models.BooleanField(
        default=True,
        help_text="SensorThings: allow live MQTT updates (requires an MQTT URL).",
    )
    allow_server_filters = models.BooleanField(
        default=True,
        help_text="OGC API: allow attribute selection and server-side filters.",
    )
    max_features = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text="Optional upper bound of features per layer load. Empty = app default.",
    )

    # Source index load ("Load catalog" in the admin, see harvest.py).
    catalog_load_started_at = models.DateTimeField(null=True, blank=True, editable=False)
    catalog_loaded_at = models.DateTimeField(null=True, blank=True, editable=False)
    catalog_load_error = models.TextField(blank=True, editable=False)
    catalog_load_note = models.TextField(
        blank=True, editable=False, help_text="Problems of the last load that did not stop it."
    )

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        verbose_name = "External Service"
        verbose_name_plural = "External Services"
        ordering = ["title", "id"]
        indexes = [models.Index(fields=["is_active", "visibility"])]

    def __str__(self) -> str:
        return f"{self.title} ({self.get_service_type_display()})"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}
        if self.base_url:
            message = _require_url_scheme(self.base_url, ("https",), ("http",))
            if message:
                errors["base_url"] = message
        if self.mqtt_url:
            if self.service_type != self.ServiceType.SENSORTHINGS:
                errors["mqtt_url"] = "Only SensorThings services have an MQTT URL."
            else:
                message = _require_url_scheme(self.mqtt_url, ("wss",), ("ws",))
                if message:
                    errors["mqtt_url"] = message
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


class Category(TimeStampedModel):
    """An admin-curated group of external items, listed like a workspace."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="external_categories",
        help_text="Owning organization; scopes who may manage this category.",
    )
    slug = models.SlugField(max_length=100, unique=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    display_order = models.PositiveIntegerField(default=0, help_text="Lower values are listed first.")
    visibility = models.CharField(
        max_length=20,
        choices=Visibility.choices,
        default=Visibility.PRIVATE,
        db_index=True,
        help_text=VISIBILITY_HELP_TEXT,
    )
    is_active = models.BooleanField(default=True, db_index=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        verbose_name = "External Category"
        verbose_name_plural = "External Categories"
        ordering = ["display_order", "title", "id"]
        indexes = [models.Index(fields=["is_active", "visibility"])]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


class CategoryItem(TimeStampedModel):
    """One map layer of a category: one OGC collection or one SensorThings layer.

    Which source fields apply is decided by ``service.service_type``:

    * OGC API Features: ``ogc_collection_id``, optional ``ogc_dataset_id``
      (the API id inside a multi-API catalog), ``default_properties``,
      ``default_filter``. Collections are never merged into one layer
      (points and polygons cannot share one); related collections are
      separate items.
    * SensorThings: ``sta_service_name`` and ``sta_layer_name``, matched
      against ``Datastream.properties.serviceName`` / ``layerName``.
    """

    class AvailabilityState(models.TextChoices):
        UNKNOWN = "UNKNOWN", "Not checked yet"
        OK = "OK", "Available"
        MISSING = "MISSING", "Missing at the service"
        ERROR = "ERROR", "Check failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="items")
    service = models.ForeignKey(ExternalService, on_delete=models.PROTECT, related_name="items")
    display_order = models.PositiveIntegerField(default=0, help_text="Lower values are listed first.")
    # Copied from the source index (ServiceSource) when the item is added and
    # refreshed by "Update catalog"; not typed by admins.
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    dataset_title = models.CharField(
        max_length=500, blank=True, help_text="OGC dataset title or SensorThings serviceName."
    )

    # OGC API Features
    ogc_dataset_id = models.CharField(
        max_length=200,
        blank=True,
        help_text="OGC API only: dataset (API) id inside a multi-API catalog; empty for a single landing page.",
    )
    ogc_collection_id = models.CharField(
        max_length=200,
        blank=True,
        help_text="OGC API only: the collection shown by this item.",
    )
    default_properties = models.JSONField(
        default=list,
        blank=True,
        help_text="OGC API only: attributes requested by default (empty = all).",
    )
    default_filter = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "OGC API only: initial filter conditions, a list of "
            '{"property", "operator", "value"} (operators: eq, neq, lt, lte, gt, gte, contains).'
        ),
    )

    # SensorThings
    sta_service_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="SensorThings only: Datastream properties.serviceName.",
    )
    sta_layer_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="SensorThings only: Datastream properties.layerName.",
    )

    # Presentation & loading hints
    color = models.CharField(
        max_length=7,
        default="#0288d1",
        validators=[HEX_COLOR_VALIDATOR],
        help_text="Default map color.",
    )
    min_zoom = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("24"))],
        help_text="Load features only from this zoom level. Empty = always.",
    )

    # Written by the health check (ticket 05).
    availability_state = models.CharField(
        max_length=20,
        choices=AvailabilityState.choices,
        default=AvailabilityState.UNKNOWN,
        editable=False,
    )
    feature_count = models.PositiveIntegerField(null=True, blank=True, editable=False)
    last_checked_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_check_error = models.TextField(blank=True, editable=False)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    class Meta:
        verbose_name = "External Category Item"
        verbose_name_plural = "External Category Items"
        ordering = ["display_order", "title", "id"]

    def __str__(self) -> str:
        return f"{self.category} -> {self.title}"

    def _related_or_none(self, name: str):
        """Return a FK target, preferring the in-memory instance.

        Admin inline validation attaches the (still unsaved) parent category
        object while ``category_id`` is not set yet; checking ids alone would
        silently skip every rule below until ``save()``.
        """
        field = self._meta.get_field(name)
        if field.is_cached(self):
            return field.get_cached_value(self)
        return getattr(self, name) if getattr(self, field.attname) else None

    def clean(self) -> None:
        super().clean()
        errors: dict[str, str] = {}

        category = self._related_or_none("category")
        service = self._related_or_none("service")
        if category is not None and service is not None:
            # Phase 1: no cross-organization references.
            if category.organization_id != service.organization_id:
                errors["service"] = "The service must belong to the category's organization."

            if service.service_type == ExternalService.ServiceType.OGC_API_FEATURES:
                errors.update(self._ogc_errors())
            else:
                errors.update(self._sensorthings_errors())

        if errors:
            raise ValidationError(errors)

    def _ogc_errors(self) -> dict[str, str]:
        errors: dict[str, str] = {}
        if not self.ogc_collection_id.strip():
            errors["ogc_collection_id"] = "Required for OGC API items."
        if not _is_unique_string_list(self.default_properties):
            errors["default_properties"] = "Use a list of unique, non-empty attribute names."
        filter_error = _default_filter_error(self.default_filter)
        if filter_error:
            errors["default_filter"] = filter_error
        if self.sta_service_name or self.sta_layer_name:
            message = "Only SensorThings items use this field."
            if self.sta_service_name:
                errors["sta_service_name"] = message
            if self.sta_layer_name:
                errors["sta_layer_name"] = message
        return errors

    def _sensorthings_errors(self) -> dict[str, str]:
        errors: dict[str, str] = {}
        if not self.sta_service_name.strip():
            errors["sta_service_name"] = "Required for SensorThings items."
        if not self.sta_layer_name.strip():
            errors["sta_layer_name"] = "Required for SensorThings items."
        message = "Only OGC API items use this field."
        for field in ("ogc_dataset_id", "ogc_collection_id", "default_properties", "default_filter"):
            if getattr(self, field):
                errors[field] = message
        return errors

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


def _is_unique_string_list(value) -> bool:
    if not isinstance(value, list):
        return False
    if not all(isinstance(item, str) and item.strip() for item in value):
        return False
    return len(set(value)) == len(value)


def _default_filter_error(value) -> str | None:
    """Validate the stored filter conditions; return a message or ``None``."""
    if not isinstance(value, list):
        return "Use a list of filter conditions."
    for index, condition in enumerate(value, start=1):
        if not isinstance(condition, dict) or set(condition) != {"property", "operator", "value"}:
            return f'Condition {index}: use exactly the keys "property", "operator" and "value".'
        if not isinstance(condition["property"], str) or not condition["property"].strip():
            return f"Condition {index}: property must be a non-empty string."
        if condition["operator"] not in FILTER_OPERATORS:
            allowed = ", ".join(sorted(FILTER_OPERATORS))
            return f"Condition {index}: operator must be one of {allowed}."
        if not isinstance(condition["value"], str | int | float | bool) or condition["value"] == "":
            return f"Condition {index}: value must be a non-empty string, number or boolean."
    return None


class ServiceSource(models.Model):
    """Local index of everything a service offers for category items.

    One row per OGC collection (``dataset_id``/``source_id`` = dataset and
    collection id) or SensorThings layer (``dataset_id``/``source_id`` =
    ``serviceName``/``layerName``). Filled by "Load catalog" (:mod:`.harvest`)
    so composing a category never waits for the remote service. Only an
    admin aid: the catalog API reads the copies stored on ``CategoryItem``.
    """

    id = models.BigAutoField(primary_key=True)
    service = models.ForeignKey(ExternalService, on_delete=models.CASCADE, related_name="sources")
    dataset_id = models.CharField(max_length=200, blank=True)
    dataset_title = models.CharField(max_length=500, blank=True)
    source_id = models.CharField(max_length=200)
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    item_type = models.CharField(max_length=50, blank=True)
    geojson = models.BooleanField(default=True, help_text="OGC: offers GeoJSON items (can be shown on the map).")
    count = models.PositiveIntegerField(null=True, blank=True, help_text="Features (OGC) or datastreams (STA).")
    themes = models.JSONField(default=list, blank=True, help_text="EU data theme codes, e.g. TRAN.")
    harvested_at = models.DateTimeField()

    class Meta:
        verbose_name = "Indexed source"
        ordering = ["dataset_title", "title", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["service", "dataset_id", "source_id"], name="external_catalog_unique_service_source"
            )
        ]

    def __str__(self) -> str:
        return self.title
