"""
GeoSimulations — simulation models, their runs, and the runs' outputs.

A ``SimulationModel`` is the catalog item (what can be run). A ``SimulationRun``
is one execution with concrete parameters. A run's ``SimulationOutput`` rows are
static map layers (ordinary ``geodata_providers.Layer`` rows), an animated
replay or images (``SimulationAsset`` files), or small tables.

Runs enter TOSCA as simulation packages; see
``docs/implementation/geosimulations-plan.md``.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from tosca_api.apps.core.editorjs import (
    description_document_from_text,
    description_document_to_text,
    empty_document,
    validate_description_document,
)
from tosca_api.apps.core.models import MediaAsset, TimeStampedModel
from tosca_api.apps.core.sanitization import sanitize_simple

SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class Status(models.TextChoices):
    """Reader visibility, shared by models and runs (same values as GeoStory)."""

    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"
    ARCHIVED = "archived", "Archived"


def _clean_description(instance, errors: dict) -> None:
    """Validate the rich description and refresh its plain-text projection."""
    existing_blocks = (
        instance.description_content.get("blocks")
        if isinstance(instance.description_content, dict)
        else None
    )
    if instance._state.adding and not existing_blocks and instance.description:
        instance.description_content = description_document_from_text(instance.description)
    try:
        instance.description_content = validate_description_document(instance.description_content)
        instance.description = description_document_to_text(instance.description_content)
    except ValidationError as exc:
        errors["description_content"] = exc.messages


def validate_timezone_name(value: str) -> None:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError(f"Unknown IANA timezone: {value!r}.") from exc


def validate_bounds(value) -> None:
    """``[west, south, east, north]`` in WGS84 degrees."""
    if value is None:
        return
    if (
        not isinstance(value, list)
        or len(value) != 4
        or not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
    ):
        raise ValidationError("Bounds must be a list of four numbers [west, south, east, north].")
    west, south, east, north = value
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValidationError(
            "Bounds must satisfy -180 ≤ west < east ≤ 180 and -90 ≤ south < north ≤ 90."
        )


def validate_sha256(value: str) -> None:
    if not SHA256_PATTERN.match(value or ""):
        raise ValidationError("Checksum must look like 'sha256:<64 lowercase hex digits>'.")


class SimulationModelQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status=Status.PUBLISHED)

    def readable_by(self, *, org_slug: str | None, exempt: bool = False):
        """Published models, plus every model of the caller's own organization."""
        if exempt:
            return self
        if not org_slug:
            return self.published()
        return self.filter(Q(status=Status.PUBLISHED) | Q(organization__slug=org_slug))


class SimulationModel(TimeStampedModel):
    """A simulation catalog item, e.g. "Hamburg bicycle agent simulation"."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    objects = SimulationModelQuerySet.as_manager()

    key = models.SlugField(
        max_length=100,
        unique=True,
        help_text="Stable producer key from the package manifest (model.key).",
    )
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    description = models.TextField(
        blank=True,
        help_text="Generated plain-text projection of the authored rich description.",
    )
    description_content = models.JSONField(
        default=empty_document,
        blank=True,
        help_text="Public rich description authored in TOSCA.",
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="simulation_models",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    preview_image = models.ForeignKey(
        "core.MediaAsset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="simulation_model_previews",
    )
    parameter_schema = models.JSONField(
        null=True,
        blank=True,
        help_text="JSON Schema of runnable parameters (user-triggered runs, Phase 4).",
    )
    default_parameters = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="simulation_models",
    )

    class Meta:
        ordering = ["title"]
        verbose_name = "Simulation model"
        verbose_name_plural = "Simulation models"

    def __str__(self) -> str:
        return self.title

    def clean(self) -> None:
        super().clean()
        errors: dict = {}
        _clean_description(self, errors)
        if self.parameter_schema is not None and not isinstance(self.parameter_schema, dict):
            errors["parameter_schema"] = "Parameter schema must be a JSON object."
        if not isinstance(self.default_parameters, dict):
            errors["default_parameters"] = "Default parameters must be a JSON object."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        self.title = sanitize_simple(self.title)
        self.summary = sanitize_simple(self.summary)
        self.full_clean()
        super().save(*args, **kwargs)


class SimulationRunQuerySet(models.QuerySet):
    def published(self):
        """Runs a reader with no elevated access sees: run *and* model published."""
        return self.filter(status=Status.PUBLISHED, model__status=Status.PUBLISHED)

    def readable_by(self, *, org_slug: str | None, exempt: bool = False):
        """Published runs, plus every run of the caller's own organization.

        Cross-organization drafts never enter the queryset, so detail views
        answer 404 rather than 403 (same rule as GeoStories).
        """
        if exempt:
            return self
        if not org_slug:
            return self.published()
        return self.filter(
            Q(status=Status.PUBLISHED, model__status=Status.PUBLISHED)
            | Q(model__organization__slug=org_slug)
        )


class SimulationRun(TimeStampedModel):
    """One execution of a simulation model with concrete parameters."""

    class ExecutionState(models.TextChoices):
        IMPORTED = "imported", "Imported"
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    COMPLETE_STATES = frozenset({ExecutionState.IMPORTED, ExecutionState.SUCCEEDED})

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    objects = SimulationRunQuerySet.as_manager()

    model = models.ForeignKey(SimulationModel, on_delete=models.CASCADE, related_name="runs")
    key = models.SlugField(
        max_length=200, help_text="Producer run key from the package manifest (run.key)."
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    description_content = models.JSONField(default=empty_document, blank=True)
    model_version = models.CharField(max_length=50)
    parameters = models.JSONField(default=dict, blank=True)
    params_hash = models.CharField(
        max_length=64,
        editable=False,
        db_index=True,
        help_text="sha256 of canonical JSON (model key, model version, parameters).",
    )
    seed = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    execution_state = models.CharField(
        max_length=20, choices=ExecutionState.choices, default=ExecutionState.IMPORTED
    )
    execution_error = models.TextField(blank=True, default="")
    time_start = models.DateTimeField(null=True, blank=True)
    time_end = models.DateTimeField(null=True, blank=True)
    timezone = models.CharField(
        max_length=64,
        default="UTC",
        validators=[validate_timezone_name],
        help_text="IANA timezone of the simulated clock, e.g. Europe/Berlin.",
    )
    bounds = models.JSONField(
        null=True,
        blank=True,
        validators=[validate_bounds],
        help_text="[west, south, east, north] in WGS84.",
    )
    kpis = models.JSONField(default=dict, blank=True)
    manifest = models.JSONField(
        default=dict, blank=True, help_text="Package manifest, stored verbatim for provenance."
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="simulation_runs",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Simulation run"
        verbose_name_plural = "Simulation runs"
        constraints = [
            models.UniqueConstraint(fields=["model", "key"], name="geosim_run_key_per_model"),
        ]

    def __str__(self) -> str:
        return self.title

    @staticmethod
    def compute_params_hash(model_key: str, model_version: str, parameters: dict) -> str:
        canonical = json.dumps(
            {"model": model_key, "version": model_version, "parameters": parameters},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def duration_seconds(self) -> float | None:
        if self.time_start is None or self.time_end is None:
            return None
        return (self.time_end - self.time_start).total_seconds()

    def clean(self) -> None:
        super().clean()
        errors: dict = {}
        _clean_description(self, errors)
        if not isinstance(self.parameters, dict):
            errors["parameters"] = "Parameters must be a JSON object."
        if not isinstance(self.kpis, dict):
            errors["kpis"] = "KPIs must be a JSON object."
        if (self.time_start is None) != (self.time_end is None):
            errors["time_end"] = "Set both the start and the end of the simulated time, or neither."
        elif self.time_start is not None and self.time_end < self.time_start:
            errors["time_end"] = "The simulated time cannot end before it starts."
        if self.status == Status.PUBLISHED:
            if self.execution_state not in self.COMPLETE_STATES:
                errors["status"] = "Only imported or succeeded runs can be published."
            elif self.time_start is None:
                errors["status"] = "A published run needs its simulated time window."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        self.title = sanitize_simple(self.title)
        if self.model_id is not None and isinstance(self.parameters, dict):
            # Derived, so computed before validation (the field must not be blank).
            self.params_hash = self.compute_params_hash(
                self.model.key, self.model_version, self.parameters
            )
        self.full_clean()
        super().save(*args, **kwargs)


class SimulationAsset(TimeStampedModel):
    """A non-image file of a run (replay bundle, preview) in object storage.

    ``core.MediaAsset`` is image-shaped (required width/height, image policy),
    so simulation files get their own row with a checksum instead.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    storage_path = models.CharField(max_length=1024, unique=True)
    original_name = models.CharField(max_length=255, blank=True)
    mime = models.CharField(max_length=100)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=71, validators=[validate_sha256])
    storage_alias = models.CharField(
        max_length=20,
        choices=MediaAsset.StorageAlias.choices,
        default=MediaAsset.StorageAlias.DEFAULT,
        help_text="Storage alias (bucket) currently holding the object.",
    )
    owner_org = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="simulation_assets",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Simulation asset"
        verbose_name_plural = "Simulation assets"

    def __str__(self) -> str:
        return self.original_name or self.storage_path

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


class SimulationOutput(TimeStampedModel):
    """One typed output of a run, addressed by its stable ``role``."""

    class Kind(models.TextChoices):
        VECTOR_LAYER = "vector_layer", "Vector layer"
        TRIPS = "trips", "Trips replay"
        TABLE = "table", "Table"
        IMAGE = "image", "Image"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    run = models.ForeignKey(SimulationRun, on_delete=models.CASCADE, related_name="outputs")
    role = models.SlugField(max_length=100, help_text="Stable output key, e.g. bike_flow.")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.CharField(max_length=255)
    display_order = models.PositiveIntegerField(default=0)
    layer = models.ForeignKey(
        "geodata_providers.Layer",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="simulation_uses",
    )
    style_assignment = models.ForeignKey(
        "geodata_providers.LayerStyleAssignment",
        # RESTRICT, not PROTECT: deleting the whole layer must still cascade
        # (same reasoning as GeoStorySceneLayer, decision 10.10).
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="simulation_uses",
        help_text="Pinned style; defaults to the layer's active default assignment.",
    )
    asset = models.ForeignKey(
        SimulationAsset,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="outputs",
    )
    data = models.JSONField(null=True, blank=True, help_text="Rows of a table output.")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["display_order", "created_at"]
        verbose_name = "Simulation output"
        verbose_name_plural = "Simulation outputs"
        constraints = [
            models.UniqueConstraint(fields=["run", "role"], name="geosim_output_role_per_run"),
        ]

    def __str__(self) -> str:
        return f"{self.run} · {self.role}"

    def clean(self) -> None:
        super().clean()
        errors: dict = {}
        has_layer = self.layer_id is not None
        has_asset = self.asset_id is not None
        if self.kind == self.Kind.VECTOR_LAYER:
            if not has_layer:
                errors["layer"] = "A vector layer output needs a layer."
            if has_asset:
                errors["asset"] = "A vector layer output cannot have an asset."
        elif self.kind in (self.Kind.TRIPS, self.Kind.IMAGE):
            if not has_asset:
                errors["asset"] = "Replay and image outputs need an asset."
            if has_layer:
                errors["layer"] = "Only vector layer outputs can have a layer."
        elif self.kind == self.Kind.TABLE:
            if has_layer or has_asset:
                errors["kind"] = "A table output holds its rows in data, not a layer or asset."
            if not isinstance(self.data, (dict, list)):
                errors["data"] = "A table output needs its rows in data."
        if self.style_assignment_id is not None:
            if not has_layer:
                errors["style_assignment"] = "A style needs a layer."
            elif self.style_assignment.layer_id != self.layer_id:
                errors["style_assignment"] = "Style assignment must belong to the selected layer."
        if has_layer and self.run_id is not None:
            if self.layer.workspace.organization_id != self.run.model.organization_id:
                errors["layer"] = "The layer must belong to the simulation model's organization."
        if not isinstance(self.metadata, dict):
            errors["metadata"] = "Metadata must be a JSON object."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        self.title = sanitize_simple(self.title)
        if self.layer_id and self.style_assignment_id is None:
            from tosca_api.apps.geodata_providers.models import LayerStyleAssignment

            self.style_assignment = self.layer.style_assignments.filter(
                role=LayerStyleAssignment.Role.DEFAULT, is_active=True
            ).first()
        self.full_clean()
        super().save(*args, **kwargs)
