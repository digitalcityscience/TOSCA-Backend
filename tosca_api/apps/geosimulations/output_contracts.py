"""Versioned contracts for structured simulation outputs and their views."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from django.core.exceptions import ValidationError

DATASET_SCHEMA_VERSION = 1
VIEW_SCHEMA_VERSION = 1
DATASET_PROFILES = frozenset({"tabular", "metric_set", "timeseries", "comparison"})
FIELD_TYPES = frozenset({"string", "integer", "number", "boolean", "datetime"})
FIELD_SEMANTICS = frozenset(
    {
        "dimension",
        "measure",
        "time",
        "metric",
        "unit",
        "baseline",
        "candidate",
        "delta",
        "percent_change",
    }
)
VIEW_TYPES = frozenset({"table", "metric_grid", "bar_chart", "line_chart"})
NUMERIC_TYPES = frozenset({"integer", "number"})


def _infer_type(values: list[Any]) -> str:
    values = [value for value in values if value is not None]
    if not values:
        return "string"
    if all(isinstance(value, bool) for value in values):
        return "boolean"
    if all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return "integer"
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
        return "number"
    if all(isinstance(value, str) and _is_datetime(value) for value in values):
        return "datetime"
    return "string"


def _is_datetime(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _default_semantic(profile: str, key: str, field_type: str) -> str:
    if profile == "metric_set":
        if key == "metric":
            return "metric"
        if key == "unit":
            return "unit"
    if profile == "timeseries" and (field_type == "datetime" or key in {"time", "timestamp"}):
        return "time"
    return "measure" if field_type in NUMERIC_TYPES else "dimension"


def normalize_dataset(data: Any, *, metadata: dict | None = None) -> dict:
    """Return the canonical v1 dataset, accepting legacy ``columns``/``rows`` tables."""
    if not isinstance(data, dict):
        raise ValidationError("A table output needs an object dataset.")
    if data.get("schema_version", DATASET_SCHEMA_VERSION) != DATASET_SCHEMA_VERSION:
        raise ValidationError("Only structured dataset schema version 1 is supported.")
    metadata = metadata if isinstance(metadata, dict) else {}
    rows = data.get("rows")
    if not isinstance(rows, list) or not all(isinstance(row, list) for row in rows):
        raise ValidationError("Dataset rows must be an array of arrays.")

    declared_fields = data.get("fields", metadata.get("fields"))
    columns = data.get("columns")
    if declared_fields is not None:
        if not isinstance(declared_fields, list) or not declared_fields:
            raise ValidationError("Dataset fields must be a non-empty array.")
        keys = [field.get("key") if isinstance(field, dict) else None for field in declared_fields]
        if not all(isinstance(key, str) and key for key in keys) or len(keys) != len(set(keys)):
            raise ValidationError("Dataset field keys must be non-empty and unique.")
        if columns is not None and columns != keys:
            raise ValidationError("Dataset columns must match the declared field keys in order.")
        columns = keys
    elif not isinstance(columns, list) or not columns or not all(isinstance(key, str) and key for key in columns):
        raise ValidationError("A dataset needs non-empty string columns or fields.")
    if len(columns) != len(set(columns)):
        raise ValidationError("Dataset column keys must be unique.")
    if any(len(row) != len(columns) for row in rows):
        raise ValidationError("Every dataset row must have one value per field.")

    profile = data.get("profile", metadata.get("profile"))
    if profile is None:
        profile = "metric_set" if {"metric", "value"} <= set(columns) else "tabular"
    if profile not in DATASET_PROFILES:
        raise ValidationError(f"Unknown dataset profile: {profile}.")

    fields = []
    for index, key in enumerate(columns):
        declared = declared_fields[index] if declared_fields is not None else {}
        field_type = declared.get("type") or _infer_type([row[index] for row in rows])
        if field_type not in FIELD_TYPES:
            raise ValidationError(f"Field {key} has unknown type {field_type}.")
        semantic = declared.get("semantic") or _default_semantic(profile, key, field_type)
        if semantic not in FIELD_SEMANTICS:
            raise ValidationError(f"Field {key} has unknown semantic {semantic}.")
        field = {
            "key": key,
            "label": declared.get("label") or key.replace("_", " ").strip().title(),
            "type": field_type,
            "semantic": semantic,
        }
        unit = declared.get("unit")
        if unit not in (None, ""):
            if not isinstance(unit, str):
                raise ValidationError(f"Field {key} unit must be a string.")
            field["unit"] = unit
        fields.append(field)

    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            if value is not None and not _matches_type(value, fields[column_index]["type"]):
                raise ValidationError(
                    f"Dataset row {row_index + 1} field {fields[column_index]['key']} "
                    f"does not match type {fields[column_index]['type']}."
                )

    return {
        "schema_version": DATASET_SCHEMA_VERSION,
        "profile": profile,
        "fields": fields,
        # Retained for the existing table/KPI consumers during the contract migration.
        "columns": list(columns),
        "rows": deepcopy(rows),
    }


def _matches_type(value: Any, field_type: str) -> bool:
    if field_type == "string":
        return isinstance(value, str)
    if field_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if field_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if field_type == "boolean":
        return isinstance(value, bool)
    return isinstance(value, str) and _is_datetime(value)


def _field_map(dataset: dict) -> dict[str, dict]:
    return {field["key"]: field for field in dataset["fields"]}


def _require_field(fields: dict[str, dict], key: Any, purpose: str) -> dict:
    if not isinstance(key, str) or key not in fields:
        raise ValidationError(f"{purpose} must reference a dataset field.")
    return fields[key]


def _validate_series(fields: dict[str, dict], series: Any) -> None:
    if not isinstance(series, list) or not series:
        raise ValidationError("A chart needs at least one series.")
    for item in series:
        if not isinstance(item, dict):
            raise ValidationError("Chart series entries must be objects.")
        field = _require_field(fields, item.get("field"), "Chart series")
        if field["type"] not in NUMERIC_TYPES:
            raise ValidationError(f"Chart series field {field['key']} must be numeric.")
        if item.get("axis", "primary") not in {"primary", "secondary"}:
            raise ValidationError("Chart series axis must be primary or secondary.")


def validate_view(dataset: dict, view: Any) -> dict:
    """Validate and return a defensive copy of a v1 presentation view."""
    dataset = normalize_dataset(dataset)
    if not isinstance(view, dict):
        raise ValidationError("A simulation result view must be an object.")
    view_type = view.get("type")
    if view_type not in VIEW_TYPES:
        raise ValidationError(f"Unknown simulation result view: {view_type}.")
    if view.get("version", VIEW_SCHEMA_VERSION) != VIEW_SCHEMA_VERSION:
        raise ValidationError("Only simulation result view version 1 is supported.")
    fields = _field_map(dataset)

    if view_type == "table":
        columns = view.get("columns", list(fields))
        if not isinstance(columns, list) or not columns:
            raise ValidationError("A table view needs at least one column.")
        for key in columns:
            _require_field(fields, key, "Table column")
        sort = view.get("sort")
        if sort is not None:
            if not isinstance(sort, dict):
                raise ValidationError("Table sort must be an object.")
            _require_field(fields, sort.get("field"), "Table sort")
            if sort.get("direction") not in {"ascending", "descending"}:
                raise ValidationError("Table sort direction must be ascending or descending.")
    elif view_type == "metric_grid":
        if dataset["profile"] != "metric_set":
            raise ValidationError("Metric grids require a metric_set dataset.")
        if not {"metric", "value"} <= set(fields):
            raise ValidationError("Metric grids require metric and value fields.")
        metrics = view.get("metrics")
        if metrics is not None and (
            not isinstance(metrics, list) or not all(isinstance(item, str) for item in metrics)
        ):
            raise ValidationError("Metric grid metrics must be an array of metric keys.")
    elif view_type == "bar_chart":
        _require_field(fields, view.get("category"), "Bar chart category")
        _validate_series(fields, view.get("series"))
    elif view_type == "line_chart":
        x_field = _require_field(fields, view.get("x"), "Line chart x-axis")
        if x_field["type"] not in {"datetime", "integer", "number"}:
            raise ValidationError("A line chart x-axis must be datetime or numeric.")
        _validate_series(fields, view.get("series"))

    normalized = deepcopy(view)
    normalized["version"] = VIEW_SCHEMA_VERSION
    return normalized


def default_views(dataset: dict, declared: Any = None) -> list[dict]:
    """Validate declared views, or return the safe default for the dataset profile."""
    dataset = normalize_dataset(dataset)
    if declared is None:
        declared = [{"type": "metric_grid", "version": 1}] if dataset["profile"] == "metric_set" else [
            {"type": "table", "version": 1}
        ]
    if not isinstance(declared, list) or not declared:
        raise ValidationError("Dataset views must be a non-empty array.")
    return [validate_view(dataset, view) for view in declared]
