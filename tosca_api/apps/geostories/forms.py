from django import forms
from django.forms.models import BaseInlineFormSet


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
