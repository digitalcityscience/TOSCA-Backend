"""
GeoSimulations — simulation models, their runs, and the runs' outputs.

A ``SimulationModel`` is the catalog item (what can be run). A ``SimulationRun``
is one execution with concrete parameters. A run's ``SimulationOutput`` rows are
map layers (PMTiles), an animated replay, or images, each backed by a
``SimulationAsset`` file in object storage, or small tables kept in the row.
A ``SimulationComparison`` sets two runs of one model side by side; it owns its
own outputs (difference layers, tables) and assets the same way a run does.

Runs users start (Phase 4) are executed by a ``SimulationRunner``, an external
HTTP service; each such run has one ``SimulationJob`` that the worker
(``run_simulation_worker``) moves from dispatch to import.

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
from django.core.files.storage import storages
from django.db import models
from django.db.models import Q
from django.utils.timezone import now as timezone_now

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


def validate_service_url(value: str) -> None:
    """An http(s) URL with a host; internal names without a TLD are fine."""
    from urllib.parse import urlsplit

    parts = urlsplit(value or "")
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.query or parts.fragment:
        raise ValidationError("Enter an http(s) URL with a host, e.g. http://bike-runner:8080.")


def validate_sha256(value: str) -> None:
    if not SHA256_PATTERN.match(value or ""):
        raise ValidationError("Checksum must look like 'sha256:<64 lowercase hex digits>'.")


class SimulationModelQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status=Status.PUBLISHED)

    def readable_by(self, *, org_slug: str | None, exempt: bool = False, user=None):
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
    runner = models.ForeignKey(
        "SimulationRunner",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="models",
        help_text="Service that runs this model when users start runs. Without one, runs are only imported.",
    )
    runner_version = models.CharField(
        max_length=50,
        blank=True,
        help_text="Model version the runner reports (synced from its /models); recorded on started runs.",
    )
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
        if (
            self.runner_id is not None
            and self.organization_id is not None
            and self.runner.organization_id != self.organization_id
        ):
            errors["runner"] = "The runner must belong to the model's organization."
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

    def readable_by(self, *, org_slug: str | None, exempt: bool = False, user=None):
        """Published runs, the caller's own runs, and the organization's shared runs.

        A run with ``organization`` visibility (imported runs) is readable by
        every member of its model's organization; a ``private`` run (runs users
        start) only by the user who started it, until staff publish it.
        Unreadable runs never enter the queryset, so detail views answer 404
        rather than 403 (same rule as GeoStories).
        """
        if exempt:
            return self
        readable = Q(status=Status.PUBLISHED, model__status=Status.PUBLISHED)
        if org_slug:
            readable |= Q(
                visibility=SimulationRun.Visibility.ORGANIZATION, model__organization__slug=org_slug
            )
        if user is not None and getattr(user, "is_authenticated", False):
            readable |= Q(created_by=user)
        return self.filter(readable)


class SimulationRun(TimeStampedModel):
    """One execution of a simulation model with concrete parameters."""

    class ExecutionState(models.TextChoices):
        IMPORTED = "imported", "Imported"
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    COMPLETE_STATES = frozenset({ExecutionState.IMPORTED, ExecutionState.SUCCEEDED})
    ACTIVE_STATES = frozenset({ExecutionState.QUEUED, ExecutionState.RUNNING})

    class Visibility(models.TextChoices):
        ORGANIZATION = "organization", "Organization (members of the model's organization)"
        PRIVATE = "private", "Private (only the user who started it)"
        # Anonymous runs (until the frontend has sign-in): readable by anyone who
        # has the run id, never listed.
        LINK = "link", "Anyone with the link (anonymous runs, not listed)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    objects = SimulationRunQuerySet.as_manager()

    model = models.ForeignKey(SimulationModel, on_delete=models.CASCADE, related_name="runs")
    key = models.SlugField(
        max_length=200, help_text="Producer run key from the package manifest (run.key)."
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    description_content = models.JSONField(default=empty_document, blank=True)
    model_version = models.CharField(
        max_length=50, blank=True, help_text="From the package; empty while a started run is queued."
    )
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
    visibility = models.CharField(
        max_length=20,
        choices=Visibility.choices,
        default=Visibility.ORGANIZATION,
        help_text="Who reads the run before it is published. Publishing makes it public either way.",
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
    source_package = models.ForeignKey(
        "SimulationAsset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="The imported package archive, kept so outputs can be regenerated.",
    )
    runner = models.ForeignKey(
        "SimulationRunner",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
        help_text="Service that executed the run; empty for imported runs.",
    )
    submitted_org = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Organization the starting user acted for (organization quotas); empty for imports.",
    )
    submitter_key = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        editable=False,
        help_text="Salted hash of the client address of an anonymous run (anonymous quotas); no address is stored.",
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


class SimulationComparisonQuerySet(models.QuerySet):
    def published(self):
        """Comparisons a reader sees: the comparison, its model and both runs published."""
        return self.filter(
            status=Status.PUBLISHED,
            model__status=Status.PUBLISHED,
            baseline_run__status=Status.PUBLISHED,
            candidate_run__status=Status.PUBLISHED,
        )

    def readable_by(self, *, org_slug: str | None, exempt: bool = False, user=None):
        """Published comparisons, the caller's own, and the organization's shared ones.

        Same visibility rules as runs: ``organization`` comparisons (imported) are
        read by the model's organization, ``private`` ones only by their creator;
        anonymous ``link`` comparisons are opened by id (views add them).
        """
        if exempt:
            return self
        readable = Q(pk__in=self.published().values("pk"))
        if org_slug:
            readable |= Q(
                visibility=SimulationRun.Visibility.ORGANIZATION, model__organization__slug=org_slug
            )
        if user is not None and getattr(user, "is_authenticated", False):
            readable |= Q(created_by=user)
        return self.filter(readable)


class SimulationComparison(TimeStampedModel):
    """Two runs of one model compared: a baseline and a candidate.

    The producer computes what changed (difference layers, tables) and ships it
    as a comparison package; KPI changes are derived from the two runs' KPIs.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    objects = SimulationComparisonQuerySet.as_manager()

    model = models.ForeignKey(SimulationModel, on_delete=models.CASCADE, related_name="comparisons")
    key = models.SlugField(
        max_length=200, help_text="Producer comparison key from the package manifest (comparison.key)."
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    description_content = models.JSONField(default=empty_document, blank=True)
    # PROTECT: a comparison is computed from both runs' data, so a run cannot
    # be deleted (or replaced by a re-import) while a comparison uses it.
    baseline_run = models.ForeignKey(
        SimulationRun, on_delete=models.PROTECT, related_name="comparisons_as_baseline"
    )
    candidate_run = models.ForeignKey(
        SimulationRun, on_delete=models.PROTECT, related_name="comparisons_as_candidate"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    visibility = models.CharField(
        max_length=20,
        choices=SimulationRun.Visibility.choices,
        default=SimulationRun.Visibility.ORGANIZATION,
        help_text="Who reads the comparison before it is published (same rules as runs).",
    )
    execution_state = models.CharField(
        max_length=20,
        choices=SimulationRun.ExecutionState.choices,
        default=SimulationRun.ExecutionState.IMPORTED,
        help_text="Comparisons users start wait for both runs, then the runner computes them.",
    )
    execution_error = models.TextField(blank=True, default="")
    bounds = models.JSONField(
        null=True,
        blank=True,
        validators=[validate_bounds],
        help_text="[west, south, east, north] in WGS84.",
    )
    manifest = models.JSONField(
        default=dict, blank=True, help_text="Package manifest, stored verbatim for provenance."
    )
    source_package = models.ForeignKey(
        "SimulationAsset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="The imported package archive, kept so outputs can be regenerated.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="simulation_comparisons",
    )
    submitted_org = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    submitter_key = models.CharField(max_length=64, blank=True, db_index=True, editable=False)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Simulation comparison"
        verbose_name_plural = "Simulation comparisons"
        constraints = [
            models.UniqueConstraint(fields=["model", "key"], name="geosim_comparison_key_per_model"),
            models.CheckConstraint(
                condition=~Q(baseline_run=models.F("candidate_run")),
                name="geosim_comparison_distinct_runs",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    def clean(self) -> None:
        super().clean()
        errors: dict = {}
        _clean_description(self, errors)
        if self.baseline_run_id is not None and self.baseline_run_id == self.candidate_run_id:
            errors["candidate_run"] = "Compare two different runs."
        for field_name in ("baseline_run", "candidate_run"):
            run = getattr(self, field_name) if getattr(self, f"{field_name}_id") else None
            if run is not None and self.model_id is not None and run.model_id != self.model_id:
                errors[field_name] = "Both runs must belong to the comparison's model."
        if self.status == Status.PUBLISHED and "baseline_run" not in errors and "candidate_run" not in errors:
            unpublished = [
                run.title
                for run in (self.baseline_run, self.candidate_run)
                if run is not None and run.status != Status.PUBLISHED
            ]
            if unpublished:
                errors["status"] = "Publish both runs first: " + ", ".join(unpublished) + "."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        self.title = sanitize_simple(self.title)
        self.full_clean()
        super().save(*args, **kwargs)

    def kpi_changes(self) -> list[dict]:
        """KPIs both runs report, with the candidate's change against the baseline."""
        baseline, candidate = self.baseline_run.kpis or {}, self.candidate_run.kpis or {}
        changes = []
        for metric, before in baseline.items():
            after = candidate.get(metric)
            if not _is_number(before) or not _is_number(after):
                continue
            changes.append(
                {
                    "metric": metric,
                    "baseline": before,
                    "candidate": after,
                    "delta": after - before,
                    "delta_percent": (after - before) / abs(before) * 100 if before else None,
                }
            )
        return changes


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class SimulationAsset(TimeStampedModel):
    """A file of a run or comparison in object storage: map tiles, replay, image, or the package.

    ``core.MediaAsset`` is image-shaped (required width/height, image policy),
    so simulation files get their own row with a checksum instead. Deleting the
    row deletes the stored object after the transaction commits (``signals.py``).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    run = models.ForeignKey(
        SimulationRun,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="assets",
    )
    comparison = models.ForeignKey(
        SimulationComparison,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="assets",
    )
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

    @property
    def storage(self):
        return storages[self.storage_alias]

    @property
    def owner(self) -> SimulationRun | SimulationComparison | None:
        return self.run if self.run_id else (self.comparison if self.comparison_id else None)

    def clean(self) -> None:
        super().clean()
        if self.run_id is not None and self.comparison_id is not None:
            raise ValidationError("An asset belongs to a run or to a comparison, not both.")

    def save(self, *args, **kwargs) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


class SimulationOutput(TimeStampedModel):
    """One typed output of a run or comparison, addressed by its stable ``role``."""

    class Kind(models.TextChoices):
        VECTOR_LAYER = "vector_layer", "Vector layer"
        TRIPS = "trips", "Trips replay"
        TABLE = "table", "Table"
        IMAGE = "image", "Image"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    run = models.ForeignKey(
        SimulationRun, on_delete=models.CASCADE, null=True, blank=True, related_name="outputs"
    )
    comparison = models.ForeignKey(
        SimulationComparison,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="outputs",
    )
    role = models.SlugField(max_length=100, help_text="Stable output key, e.g. bike_flow.")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    title = models.CharField(max_length=255)
    display_order = models.PositiveIntegerField(default=0)
    asset = models.ForeignKey(
        SimulationAsset,
        # RESTRICT, not PROTECT: deleting the run deletes its outputs and assets
        # in one cascade, while deleting an asset on its own stays blocked.
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="outputs",
    )
    style = models.JSONField(
        null=True,
        blank=True,
        help_text="MapLibre style layers (without source) for a map layer output.",
    )
    legend = models.JSONField(
        null=True,
        blank=True,
        help_text="Legend built from the same classes as the style: {type, title, items, ...}.",
    )
    data = models.JSONField(null=True, blank=True, help_text="Rows of a table output.")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["display_order", "created_at"]
        verbose_name = "Simulation output"
        verbose_name_plural = "Simulation outputs"
        constraints = [
            models.UniqueConstraint(fields=["run", "role"], name="geosim_output_role_per_run"),
            models.UniqueConstraint(
                fields=["comparison", "role"], name="geosim_output_role_per_comparison"
            ),
            models.CheckConstraint(
                condition=Q(run__isnull=False, comparison__isnull=True)
                | Q(run__isnull=True, comparison__isnull=False),
                name="geosim_output_one_owner",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.owner} · {self.role}"

    @property
    def owner(self) -> SimulationRun | SimulationComparison | None:
        return self.run if self.run_id else (self.comparison if self.comparison_id else None)

    def clean(self) -> None:
        super().clean()
        errors: dict = {}
        has_asset = self.asset_id is not None
        if (self.run_id is None) == (self.comparison_id is None):
            errors["run"] = "An output belongs to exactly one run or comparison."
        elif self.comparison_id is not None and self.kind == self.Kind.TRIPS:
            errors["kind"] = "Comparisons have no replay; compare the runs' replays instead."
        if self.kind == self.Kind.TABLE:
            if has_asset:
                errors["asset"] = "A table output holds its rows in data, not an asset."
            if not isinstance(self.data, (dict, list)):
                errors["data"] = "A table output needs its rows in data."
        elif not has_asset:
            errors["asset"] = "Map layer, replay and image outputs need an asset."
        if self.style is not None:
            if self.kind != self.Kind.VECTOR_LAYER:
                errors["style"] = "Only map layer outputs have a style."
            elif not isinstance(self.style, list) or not all(
                isinstance(layer, dict) and layer.get("id") and layer.get("type")
                for layer in self.style
            ):
                errors["style"] = "Style must be a list of MapLibre layers with id and type."
        if self.legend is not None and (
            self.kind != self.Kind.VECTOR_LAYER
            or not isinstance(self.legend, dict)
            or not isinstance(self.legend.get("items"), list)
        ):
            errors["legend"] = "Only map layer outputs have a legend: an object with an items list."
        if has_asset and (
            (self.run_id is not None and self.asset.run_id not in (None, self.run_id))
            or (self.comparison_id is not None and self.asset.comparison_id not in (None, self.comparison_id))
            or (self.run_id is not None and self.asset.comparison_id is not None)
            or (self.comparison_id is not None and self.asset.run_id is not None)
        ):
            errors["asset"] = "The asset belongs to another run or comparison."
        if not isinstance(self.metadata, dict):
            errors["metadata"] = "Metadata must be a JSON object."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        self.title = sanitize_simple(self.title)
        self.full_clean()
        super().save(*args, **kwargs)


class SimulationRunner(TimeStampedModel):
    """An HTTP service that runs simulations for TOSCA.

    Contract (``docs/implementation/geosimulations-plan.md``, Phase 4): TOSCA
    ``POST``s a job to ``{base_url}/jobs`` with the bearer ``token``; the runner
    uploads the result package to a presigned URL and reports back to the job's
    callback URL. One runner can serve several models (``SimulationModel.runner``).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT, related_name="simulation_runners"
    )
    name = models.CharField(max_length=255)
    # Not URLField: Docker service names such as "bike-runner" have no TLD.
    base_url = models.CharField(
        max_length=500,
        validators=[validate_service_url],
        help_text="Runner root URL as TOSCA's worker reaches it, e.g. http://bike-runner:8080.",
    )
    token_encrypted = models.TextField(
        blank=True, help_text="Bearer token TOSCA sends to the runner (stored encrypted)."
    )
    is_active = models.BooleanField(default=True)
    max_concurrent_jobs = models.PositiveSmallIntegerField(
        default=1, help_text="Jobs sent to the runner at the same time; the rest wait in TOSCA's queue."
    )
    timeout_minutes = models.PositiveIntegerField(
        default=360, help_text="A job not finished this long after dispatch is marked failed."
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "Simulation runner"
        verbose_name_plural = "Simulation runners"

    def __str__(self) -> str:
        return self.name

    def set_token(self, token: str) -> None:
        from tosca_api.apps.geodata_providers.encryption import encrypt_value

        self.token_encrypted = encrypt_value(token) if token else ""

    def get_token(self) -> str:
        from tosca_api.apps.geodata_providers.encryption import decrypt_value

        return decrypt_value(self.token_encrypted) if self.token_encrypted else ""

    def save(self, *args, **kwargs) -> None:
        self.base_url = self.base_url.rstrip("/")
        self.full_clean()
        super().save(*args, **kwargs)


class SimulationJob(TimeStampedModel):
    """The background work for one run that a runner executes.

    The worker dispatches ``pending`` jobs to the runner and imports
    ``uploaded`` packages; the runner's callbacks move the job in between.
    A comparison job (``comparison`` instead of ``run``) starts ``waiting`` until
    both runs succeeded. A row is also the queue entry (Postgres,
    ``SELECT … FOR UPDATE SKIP LOCKED``);
    a Celery + Redis worker is planned to replace the polling loop later.
    """

    class State(models.TextChoices):
        WAITING = "waiting", "Waiting for its runs (comparisons)"
        PENDING = "pending", "Waiting to be sent to the runner"
        DISPATCHED = "dispatched", "Accepted by the runner"
        RUNNING = "running", "Running"
        UPLOADED = "uploaded", "Package uploaded, waiting for import"
        IMPORTING = "importing", "Importing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    TERMINAL_STATES = frozenset({State.SUCCEEDED, State.FAILED})
    AT_RUNNER_STATES = frozenset({State.DISPATCHED, State.RUNNING})

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    run = models.OneToOneField(
        SimulationRun, on_delete=models.CASCADE, null=True, blank=True, related_name="job"
    )
    comparison = models.OneToOneField(
        SimulationComparison, on_delete=models.CASCADE, null=True, blank=True, related_name="job"
    )
    runner = models.ForeignKey(SimulationRunner, on_delete=models.PROTECT, related_name="jobs")
    state = models.CharField(max_length=20, choices=State.choices, default=State.PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0, help_text="Dispatch attempts so far.")
    runner_job_id = models.CharField(max_length=255, blank=True)
    callback_token_hash = models.CharField(max_length=64, blank=True, editable=False)
    upload_path = models.CharField(
        max_length=1024, blank=True, help_text="Where the runner uploads the package (default storage)."
    )
    message = models.CharField(max_length=500, blank=True, help_text="Latest status message from the runner.")
    error = models.TextField(blank=True)
    available_at = models.DateTimeField(
        default=timezone_now, db_index=True, help_text="The worker picks the job up from this moment."
    )
    locked_by = models.CharField(max_length=255, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    last_callback_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Simulation job"
        verbose_name_plural = "Simulation jobs"
        constraints = [
            models.CheckConstraint(
                condition=Q(run__isnull=False, comparison__isnull=True)
                | Q(run__isnull=True, comparison__isnull=False),
                name="geosim_job_one_owner",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.owner} · {self.get_state_display()}"

    @property
    def owner(self) -> SimulationRun | SimulationComparison:
        """The run or comparison this job computes."""
        return self.run if self.run_id else self.comparison

    @property
    def is_terminal(self) -> bool:
        return self.state in self.TERMINAL_STATES
