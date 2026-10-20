from django import forms
from django.db.models import Q

from tosca_api.apps.geodata_providers.admin import (
    LayerStyleAssignmentChoiceField,
    LayerStyleAssignmentSelect,
)
from tosca_api.apps.geodata_providers.models import LayerStyleAssignment

from .models import GeoStoryResultPresentation, GeoStorySceneSimulation, GeoStorySceneLayer


class GeoStorySceneLayerForm(forms.ModelForm):
    style_assignment = LayerStyleAssignmentChoiceField(
        queryset=LayerStyleAssignment.objects.none(),
        required=False,
        label="Style",
        empty_label="Select a layer to use its default style",
        widget=LayerStyleAssignmentSelect,
    )

    class Meta:
        model = GeoStorySceneLayer
        exclude = ("source_kind",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "simulation-TOTAL_FORMS" in self.data:
            # The simulation form validates the submitted final state of both
            # inline groups. A database lookup here would reject valid swaps.
            self.instance._skip_stack_order_database_check = True
        visible_assignments = Q(is_active=True)
        # Keep a pinned assignment selectable even if a provider sync marked it
        # inactive; otherwise the browser submits an empty value and the model
        # silently swaps in the current default style.
        if self.instance.style_assignment_id:
            visible_assignments |= Q(pk=self.instance.style_assignment_id)
        self.fields["style_assignment"].queryset = (
            LayerStyleAssignment.objects.filter(visible_assignments)
            .select_related("style", "layer")
            .order_by("layer__name", "style__title", "style__name")
        )
        self.fields["display_order"].label = "Stack order (0 = bottom)"
        self.fields["display_order"].help_text = (
            "The simulation group cannot use the same position as a catalog layer."
        )
        self.fields["feature_id_attribute"].label = "Feature ID attribute"
        self.fields["feature_id_attribute"].widget = forms.Select(
            choices=self._attribute_choices()
        )
        self.fields["feature_ids"].help_text = (
            "Values of the feature ID attribute. Use “Pick features on map” to fill this in."
        )

    def _attribute_choices(self) -> list[tuple[str, str]]:
        """The layer's attributes; the scene editor refreshes them when the layer changes."""
        attributes = self.instance.layer.attributes if self.instance.layer_id else []
        choices = [("", "— choose an attribute —")]
        for item in attributes or []:
            name, kind = item.get("name"), item.get("type")
            choices.append((name, f"{name} ({kind})" if kind else name))
        names = {value for value, _ in choices}
        # Keep the saved or just-submitted value selectable even if GeoServer
        # no longer reports it, so a re-rendered form never drops it silently.
        submitted = self.data.get(self.add_prefix("feature_id_attribute")) if self.is_bound else None
        for value in (self.instance.feature_id_attribute, submitted):
            if value and value not in names:
                choices.append((value, value))
                names.add(value)
        return choices


class SimulationOutputChoiceField(forms.ModelMultipleChoiceField):
    def label_from_instance(self, obj):
        metadata = obj.metadata or {}
        details = [f"role: {obj.role}"]
        if metadata.get("geometry_type"):
            details.append(metadata["geometry_type"])
        if obj.asset_id and obj.asset.original_name:
            details.append(obj.asset.original_name)
        return f"{obj.run.title} · {obj.title} — {' · '.join(details)}"


class GeoStorySceneSimulationForm(forms.ModelForm):
    """Choose a published run, its map layers, and the replay window for a scene."""

    map_outputs = SimulationOutputChoiceField(
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=(
            "Select a run first, then choose its map outputs. The selected outputs render "
            "together at the simulation stack order."
        ),
    )

    class Meta:
        model = GeoStorySceneSimulation
        fields = (
            "run",
            "map_outputs",
            "display_order",
            "show_replay",
            "replay_start_s",
            "replay_end_s",
            "playback_rate",
            "loop",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "scene_layers-TOTAL_FORMS" in self.data:
            # Validate the submitted final state below instead of comparing
            # against rows that may also be changing in this admin request.
            self.instance._skip_stack_order_database_check = True
        from tosca_api.apps.geosimulations.models import SimulationOutput, SimulationRun, Status

        published_runs = SimulationRun.objects.filter(
            status=Status.PUBLISHED, model__status=Status.PUBLISHED
        )
        self.fields["run"].queryset = published_runs.select_related("model")
        selected_run_id = (
            self.data.get(self.add_prefix("run")) if self.is_bound else self.instance.run_id
        )
        selected_run = None
        if selected_run_id:
            try:
                selected_run = published_runs.filter(pk=selected_run_id).first()
            except (TypeError, ValueError, forms.ValidationError):
                pass
        outputs = SimulationOutput.objects.none()
        if selected_run is not None:
            outputs = (
                SimulationOutput.objects.filter(
                    kind=SimulationOutput.Kind.VECTOR_LAYER,
                    run=selected_run,
                )
                .select_related("run", "asset")
                .order_by("display_order", "created_at")
            )
        self.fields["map_outputs"].queryset = outputs
        self.fields["display_order"].label = "Stack order (0 = bottom)"
        self.fields["display_order"].help_text = (
            "The simulation group cannot use the same position as a catalog layer."
        )

    def clean(self):
        cleaned_data = super().clean()
        run = cleaned_data.get("run")
        outputs = cleaned_data.get("map_outputs") or []
        display_order = cleaned_data.get("display_order")
        if (
            display_order is not None
            and not cleaned_data.get("DELETE")
            and "scene_layers-TOTAL_FORMS" in self.data
            and display_order in self._submitted_layer_orders()
        ):
            self.add_error(
                "display_order",
                "This stack order is already used by a scene layer.",
            )
        if run is not None:
            self.instance.run = run
            try:
                self.instance.clean_map_outputs(outputs)
            except forms.ValidationError as exc:
                self.add_error("map_outputs", exc)
        return cleaned_data

    def _submitted_layer_orders(self) -> set[int]:
        """Active catalog-layer orders in the same scene-admin submission."""
        try:
            total = int(self.data.get("scene_layers-TOTAL_FORMS", 0))
        except (TypeError, ValueError):
            return set()
        orders = set()
        for index in range(total):
            prefix = f"scene_layers-{index}"
            if self.data.get(f"{prefix}-DELETE"):
                continue
            if not (
                self.data.get(f"{prefix}-layer")
                or self.data.get(f"{prefix}-id")
            ):
                continue
            try:
                orders.add(int(self.data.get(f"{prefix}-display_order")))
            except (TypeError, ValueError):
                continue
        return orders


class GeoStoryResultPresentationForm(forms.ModelForm):
    """Human-friendly editor for the versioned result-view configuration."""

    table_columns = forms.MultipleChoiceField(
        required=False,
        label="Columns",
        widget=forms.SelectMultiple(attrs={"size": 8}),
        help_text="Columns shown in the table. Leave all selected to show the complete dataset.",
    )
    sort_field = forms.ChoiceField(required=False, label="Sort by")
    sort_direction = forms.ChoiceField(
        required=False,
        choices=(("ascending", "Ascending"), ("descending", "Descending")),
        initial="ascending",
    )
    metrics = forms.MultipleChoiceField(
        required=False,
        label="Metrics",
        widget=forms.SelectMultiple(attrs={"size": 8}),
        help_text="Metrics shown as cards. Leave empty to show every metric.",
    )
    category_field = forms.ChoiceField(required=False, label="Category field")
    x_field = forms.ChoiceField(required=False, label="X-axis field")
    series_fields = forms.MultipleChoiceField(
        required=False,
        label="Value series",
        widget=forms.SelectMultiple(attrs={"size": 8}),
        help_text="Choose one or more numeric fields to plot.",
    )

    class Meta:
        model = GeoStoryResultPresentation
        fields = ("story", "output", "caption", "view_type")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["output"].help_text = (
            "A structured table produced by a published run or comparison. "
            "After choosing it, the available fields appear below."
        )
        self.fields["output"].label_from_instance = self._output_label
        self.fields["view_type"].help_text = (
            "This only changes how the same output data is presented; it does not select another file."
        )
        output = self._selected_output()
        dataset = self._dataset(output)
        fields = dataset.get("fields", []) if dataset else []
        field_choices = [(item["key"], self._field_label(item)) for item in fields]
        numeric_choices = [
            choice
            for choice, item in zip(field_choices, fields, strict=True)
            if item["type"] in {"integer", "number"}
        ]
        x_choices = [
            choice
            for choice, item in zip(field_choices, fields, strict=True)
            if item["type"] in {"datetime", "integer", "number"}
        ]
        optional_fields = [("", "— none —"), *field_choices]
        self.fields["table_columns"].choices = field_choices
        self.fields["sort_field"].choices = optional_fields
        self.fields["category_field"].choices = [("", "— choose a category —"), *field_choices]
        self.fields["x_field"].choices = [("", "— choose an axis —"), *x_choices]
        self.fields["series_fields"].choices = numeric_choices
        self.fields["metrics"].choices = self._metric_choices(dataset)
        if not self.is_bound:
            self._set_configuration_initials(dataset)

    @staticmethod
    def _output_label(output):
        from tosca_api.apps.geosimulations.output_contracts import normalize_dataset

        owner = output.owner
        source = "Run" if output.run_id else "Comparison"
        profile = normalize_dataset(output.data, metadata=output.metadata)["profile"]
        return (
            f"{source}: {owner.title} — {output.title} "
            f"(role: {output.role}; profile: {profile})"
        )

    @staticmethod
    def _field_label(field):
        unit = f"; {field['unit']}" if field.get("unit") else ""
        return f"{field['label']} ({field['key']}; {field['type']}{unit})"

    def _selected_output(self):
        selected_id = (
            self.data.get(self.add_prefix("output"))
            if self.is_bound
            else self.instance.output_id
        )
        if not selected_id:
            return None
        try:
            return self.fields["output"].queryset.filter(pk=selected_id).first()
        except (TypeError, ValueError, forms.ValidationError):
            return None

    @staticmethod
    def _dataset(output):
        if output is None:
            return None
        from tosca_api.apps.geosimulations.output_contracts import normalize_dataset

        return normalize_dataset(output.data, metadata=output.metadata)

    @staticmethod
    def _metric_choices(dataset):
        if not dataset or dataset.get("profile") != "metric_set":
            return []
        metric_index = next(
            (
                index
                for index, field in enumerate(dataset["fields"])
                if field.get("semantic") == "metric" or field["key"] == "metric"
            ),
            None,
        )
        if metric_index is None:
            return []
        return [
            (str(row[metric_index]), str(row[metric_index]))
            for row in dataset["rows"]
            if row[metric_index] is not None
        ]

    def _set_configuration_initials(self, dataset):
        configuration = self.instance.configuration or {}
        all_columns = dataset.get("columns", []) if dataset else []
        self.initial.update(
            table_columns=configuration.get("columns", all_columns),
            sort_field=(configuration.get("sort") or {}).get("field", ""),
            sort_direction=(configuration.get("sort") or {}).get("direction", "ascending"),
            metrics=configuration.get("metrics", []),
            category_field=configuration.get("category", ""),
            x_field=configuration.get("x", ""),
            series_fields=[item["field"] for item in configuration.get("series", [])],
        )

    def clean(self):
        cleaned = super().clean()
        view_type = cleaned.get("view_type")
        configuration = {}
        if view_type == GeoStoryResultPresentation.ViewType.TABLE:
            columns = cleaned.get("table_columns") or []
            if columns:
                configuration["columns"] = columns
            sort_field = cleaned.get("sort_field")
            if sort_field:
                configuration["sort"] = {
                    "field": sort_field,
                    "direction": cleaned.get("sort_direction") or "ascending",
                }
        elif view_type == GeoStoryResultPresentation.ViewType.METRIC_GRID:
            metrics = cleaned.get("metrics") or []
            if metrics:
                configuration["metrics"] = metrics
        elif view_type == GeoStoryResultPresentation.ViewType.BAR_CHART:
            configuration["category"] = self._required(
                cleaned, "category_field", "Choose the category field for the bar chart."
            )
            configuration["series"] = self._series(cleaned)
        elif view_type == GeoStoryResultPresentation.ViewType.LINE_CHART:
            configuration["x"] = self._required(
                cleaned, "x_field", "Choose the X-axis field for the line chart."
            )
            configuration["series"] = self._series(cleaned)
        self.instance.configuration = configuration
        return cleaned

    def _series(self, cleaned):
        fields = cleaned.get("series_fields") or []
        if not fields:
            self.add_error("series_fields", "Choose at least one numeric value series.")
        return [{"field": field} for field in fields]

    def _required(self, cleaned, name, message):
        value = cleaned.get(name)
        if not value:
            self.add_error(name, message)
        return value or ""
