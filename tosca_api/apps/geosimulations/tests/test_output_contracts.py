import pytest
from django.core.exceptions import ValidationError

from ..output_contracts import default_views, normalize_dataset, validate_view


def test_legacy_metric_table_normalizes_to_versioned_dataset():
    dataset = normalize_dataset(
        {"columns": ["metric", "value", "unit"], "rows": [["agents", 12, "count"]]}
    )

    assert dataset["schema_version"] == 1
    assert dataset["profile"] == "metric_set"
    assert [field["type"] for field in dataset["fields"]] == ["string", "integer", "string"]
    assert [field["semantic"] for field in dataset["fields"]] == [
        "metric",
        "measure",
        "unit",
    ]
    assert default_views(dataset) == [{"type": "metric_grid", "version": 1}]


def test_line_chart_accepts_numeric_series_and_time_axis():
    dataset = normalize_dataset(
        {
            "profile": "timeseries",
            "fields": [
                {"key": "time", "label": "Time", "type": "datetime", "semantic": "time"},
                {"key": "flow", "label": "Flow", "type": "number", "semantic": "measure"},
            ],
            "rows": [["2026-10-20T09:00:00Z", 12.5]],
        }
    )

    assert validate_view(
        dataset,
        {"type": "line_chart", "x": "time", "series": [{"field": "flow"}]},
    ) == {
        "type": "line_chart",
        "version": 1,
        "x": "time",
        "series": [{"field": "flow"}],
    }


def test_chart_rejects_unknown_or_non_numeric_series():
    dataset = normalize_dataset(
        {"columns": ["district", "value"], "rows": [["Altona", 12]]}
    )

    with pytest.raises(ValidationError, match="must be numeric"):
        validate_view(
            dataset,
            {
                "type": "bar_chart",
                "category": "district",
                "series": [{"field": "district"}],
            },
        )


def test_declared_field_type_is_enforced():
    with pytest.raises(ValidationError, match="does not match type number"):
        normalize_dataset(
            {
                "fields": [{"key": "value", "label": "Value", "type": "number"}],
                "rows": [["not a number"]],
            }
        )
