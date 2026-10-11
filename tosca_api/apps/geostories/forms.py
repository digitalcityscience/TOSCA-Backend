from django import forms
from django.db.models import Q

from tosca_api.apps.geodata_providers.admin import (
    LayerStyleAssignmentChoiceField,
    LayerStyleAssignmentSelect,
)
from tosca_api.apps.geodata_providers.models import LayerStyleAssignment

from .models import GeoStorySceneSimulation, GeoStorySceneLayer


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
        self.fields["display_order"].label = "Order (0 = bottom)"
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
        return f"{obj.run.title} · {obj.title}"


class GeoStorySceneSimulationForm(forms.ModelForm):
    """Choose a published run, its map layers, and the replay window for a scene."""

    map_outputs = SimulationOutputChoiceField(
        queryset=None,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Map layers of the selected run. Shown above the scene's catalog layers.",
    )

    class Meta:
        model = GeoStorySceneSimulation
        fields = (
            "run",
            "map_outputs",
            "show_replay",
            "replay_start_s",
            "replay_end_s",
            "playback_rate",
            "loop",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from tosca_api.apps.geosimulations.models import SimulationOutput, SimulationRun, Status

        published_runs = SimulationRun.objects.filter(
            status=Status.PUBLISHED, model__status=Status.PUBLISHED
        )
        self.fields["run"].queryset = published_runs.select_related("model")
        outputs = SimulationOutput.objects.filter(
            kind=SimulationOutput.Kind.VECTOR_LAYER, run__in=published_runs
        ).select_related("run")
        if self.instance.pk and self.instance.run_id:
            outputs = outputs.filter(run_id=self.instance.run_id)
        self.fields["map_outputs"].queryset = outputs.order_by("run__title", "display_order")

    def clean(self):
        cleaned_data = super().clean()
        run = cleaned_data.get("run")
        outputs = cleaned_data.get("map_outputs") or []
        if run is not None:
            self.instance.run = run
            try:
                self.instance.clean_map_outputs(outputs)
            except forms.ValidationError as exc:
                self.add_error("map_outputs", exc)
        return cleaned_data
