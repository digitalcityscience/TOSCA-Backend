from django import forms
from django.db.models import Q

from tosca_api.apps.geodata_providers.admin import (
    LayerStyleAssignmentChoiceField,
    LayerStyleAssignmentSelect,
)
from tosca_api.apps.geodata_providers.models import LayerStyleAssignment

from .models import GeoStorySceneLayer


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
