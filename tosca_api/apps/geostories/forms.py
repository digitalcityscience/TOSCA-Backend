from django import forms
from django.db import models
from django.forms.models import BaseInlineFormSet

from tosca_api.apps.geodata_providers.models import LayerStyleAssignment


class GeoStoryLayerStyleChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, assignment):
        style = assignment.style
        label = style.title or style.name
        return label if assignment.is_active else f"{label} (inactive)"


class GeoStoryLayerForm(forms.ModelForm):
    style_assignment = GeoStoryLayerStyleChoiceField(
        queryset=LayerStyleAssignment.objects.none(),
        required=False,
        label="Style",
        empty_label="Select a layer to use its default style",
    )

    class Meta:
        from .models import GeoStoryLayer

        model = GeoStoryLayer
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = LayerStyleAssignment.objects.filter(is_active=True)
        if self.instance.style_assignment_id:
            queryset = LayerStyleAssignment.objects.filter(
                models.Q(is_active=True) | models.Q(pk=self.instance.style_assignment_id)
            )
        self.fields["style_assignment"].queryset = queryset.select_related(
            "style", "layer"
        ).order_by("layer__name", "style__title", "style__name")

    def clean(self):
        cleaned_data = super().clean()
        layer = cleaned_data.get("layer")
        assignment = cleaned_data.get("style_assignment")
        if layer and assignment is None:
            assignment = layer.style_assignments.filter(
                role=LayerStyleAssignment.Role.DEFAULT, is_active=True
            ).first()
            cleaned_data["style_assignment"] = assignment
            self.instance.style_assignment = assignment
        if layer and assignment and assignment.layer_id != layer.id:
            self.add_error("style_assignment", "Select a style belonging to this layer.")
        return cleaned_data


class GeoStoryLayerFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        orders = []
        for form in self.forms:
            if self.can_delete and self._should_delete_form(form):
                continue

            cleaned_data = form.cleaned_data
            if not cleaned_data or not cleaned_data.get("layer"):
                continue

            display_order = cleaned_data.get("display_order")
            if display_order in orders:
                raise forms.ValidationError(
                    "Duplicate display order values detected. Each layer must have a unique order."
                )
            orders.append(display_order)
