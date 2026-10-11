"""Read-only API serializers for GeoSimulations (Task 12.5)."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .delivery import asset_url, file_url
from .models import SimulationComparison, SimulationModel, SimulationOutput, SimulationRun

TRIPS_METADATA = ("agents", "encoding", "encoding_version", "vertices", "stops", "time_unit", "time_origin")


def _time(run: SimulationRun) -> dict:
    """Simulated time in the run's own timezone (the database returns UTC)."""
    zone = ZoneInfo(run.timezone)

    def local(moment):
        return moment.astimezone(zone).isoformat() if moment else None

    return {"start": local(run.time_start), "end": local(run.time_end), "timezone": run.timezone}


class _ModelRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = SimulationModel
        fields = ["id", "key", "title"]
        read_only_fields = fields


class SimulationRunSummarySerializer(serializers.ModelSerializer):
    time = serializers.SerializerMethodField()

    class Meta:
        model = SimulationRun
        fields = [
            "id",
            "key",
            "title",
            "status",
            "execution_state",
            "model_version",
            "time",
            "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_time(self, run: SimulationRun) -> dict:
        return _time(run)


class SimulationComparisonSummarySerializer(serializers.ModelSerializer):
    baseline = serializers.SerializerMethodField()
    candidate = serializers.SerializerMethodField()

    class Meta:
        model = SimulationComparison
        fields = ["id", "key", "title", "status", "baseline", "candidate", "created_at"]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_baseline(self, comparison: SimulationComparison) -> dict:
        return _run_ref(comparison.baseline_run)

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_candidate(self, comparison: SimulationComparison) -> dict:
        return _run_ref(comparison.candidate_run)


def _run_ref(run: SimulationRun) -> dict:
    return {"id": str(run.pk), "key": run.key, "title": run.title}


class SimulationModelListSerializer(serializers.ModelSerializer):
    run_count = serializers.IntegerField(read_only=True)
    preview_image_url = serializers.SerializerMethodField()

    class Meta:
        model = SimulationModel
        fields = ["id", "key", "title", "summary", "status", "preview_image_url", "run_count", "updated_at"]
        read_only_fields = fields

    def get_preview_image_url(self, model: SimulationModel) -> str | None:
        image = model.preview_image
        if image is None:
            return None
        return file_url(image.storage_alias, image.storage_path, self.context.get("request")).url


class SimulationModelDetailSerializer(SimulationModelListSerializer):
    organization = serializers.SerializerMethodField()
    runs = serializers.SerializerMethodField()
    comparisons = serializers.SerializerMethodField()

    class Meta(SimulationModelListSerializer.Meta):
        fields = SimulationModelListSerializer.Meta.fields + [
            "description_content",
            "organization",
            "parameter_schema",
            "default_parameters",
            "runs",
            "comparisons",
        ]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_organization(self, model: SimulationModel) -> dict:
        return {"slug": model.organization.slug, "name": model.organization.name}

    @extend_schema_field(SimulationRunSummarySerializer(many=True))
    def get_runs(self, model: SimulationModel) -> list[dict]:
        # The view passes the runs this caller may read, newest first.
        runs = self.context.get("readable_runs", [])
        return SimulationRunSummarySerializer(runs, many=True, context=self.context).data

    @extend_schema_field(SimulationComparisonSummarySerializer(many=True))
    def get_comparisons(self, model: SimulationModel) -> list[dict]:
        # Comparisons this caller may read, newest first (both runs readable too).
        comparisons = self.context.get("readable_comparisons", [])
        return SimulationComparisonSummarySerializer(comparisons, many=True, context=self.context).data


class SimulationOutputSerializer(serializers.BaseSerializer):
    """One output, with the fields its kind needs (see the frontend contract)."""

    def to_representation(self, output: SimulationOutput) -> dict:
        request, now = self.context.get("request"), self.context.get("now")
        data = {
            "id": str(output.pk),
            "role": output.role,
            "kind": output.kind,
            "title": output.title,
            "display_order": output.display_order,
        }
        if output.asset_id:
            link = asset_url(output.asset, request, now=now)
            data["url"] = link.url
            data["url_expires_at"] = link.expires_at.isoformat() if link.expires_at else None
        metadata = output.metadata or {}
        if output.kind == SimulationOutput.Kind.VECTOR_LAYER:
            data.update(
                source_layer=metadata.get("source_layer", output.role),
                geometry_type=metadata.get("geometry_type"),
                minzoom=metadata.get("minzoom"),
                maxzoom=metadata.get("maxzoom"),
                bounds=metadata.get("bounds"),
                features=metadata.get("features"),
                style=output.style or [],
                legend=output.legend,
            )
        elif output.kind == SimulationOutput.Kind.TRIPS:
            data.update(
                size=output.asset.size,
                sha256=output.asset.sha256,
                metadata={key: metadata[key] for key in TRIPS_METADATA if key in metadata},
            )
        elif output.kind == SimulationOutput.Kind.TABLE:
            data["data"] = output.data
        elif output.kind == SimulationOutput.Kind.IMAGE:
            data["mime"] = output.asset.mime
        return data


class SimulationRunDetailSerializer(serializers.ModelSerializer):
    model = _ModelRefSerializer(read_only=True)
    time = serializers.SerializerMethodField()
    labels = serializers.SerializerMethodField()
    outputs = serializers.SerializerMethodField()

    class Meta:
        model = SimulationRun
        fields = [
            "id",
            "model",
            "key",
            "title",
            "description_content",
            "status",
            "execution_state",
            "model_version",
            "parameters",
            "seed",
            "time",
            "bounds",
            "kpis",
            "labels",
            "outputs",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_time(self, run: SimulationRun) -> dict:
        return _time(run)

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_labels(self, run: SimulationRun) -> dict:
        return (run.manifest or {}).get("labels", {})

    @extend_schema_field({"type": "array", "items": {"type": "object"}})
    def get_outputs(self, run: SimulationRun) -> list[dict]:
        return SimulationOutputSerializer(run.outputs.all(), many=True, context=self.context).data


class SimulationComparisonDetailSerializer(serializers.ModelSerializer):
    model = _ModelRefSerializer(read_only=True)
    baseline = serializers.SerializerMethodField()
    candidate = serializers.SerializerMethodField()
    labels = serializers.SerializerMethodField()
    kpi_changes = serializers.SerializerMethodField()
    outputs = serializers.SerializerMethodField()

    class Meta:
        model = SimulationComparison
        fields = [
            "id",
            "model",
            "key",
            "title",
            "description_content",
            "status",
            "baseline",
            "candidate",
            "bounds",
            "labels",
            "kpi_changes",
            "outputs",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @extend_schema_field(SimulationRunSummarySerializer)
    def get_baseline(self, comparison: SimulationComparison) -> dict:
        return SimulationRunSummarySerializer(comparison.baseline_run, context=self.context).data

    @extend_schema_field(SimulationRunSummarySerializer)
    def get_candidate(self, comparison: SimulationComparison) -> dict:
        return SimulationRunSummarySerializer(comparison.candidate_run, context=self.context).data

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_labels(self, comparison: SimulationComparison) -> dict:
        """Both runs' labels, overridden by the comparison package's own."""
        labels: dict = {}
        for source in (comparison.baseline_run.manifest, comparison.candidate_run.manifest, comparison.manifest):
            for attribute, values in ((source or {}).get("labels") or {}).items():
                labels.setdefault(attribute, {}).update(values)
        return labels

    @extend_schema_field({"type": "array", "items": {"type": "object"}})
    def get_kpi_changes(self, comparison: SimulationComparison) -> list[dict]:
        return comparison.kpi_changes()

    @extend_schema_field({"type": "array", "items": {"type": "object"}})
    def get_outputs(self, comparison: SimulationComparison) -> list[dict]:
        return SimulationOutputSerializer(comparison.outputs.all(), many=True, context=self.context).data
