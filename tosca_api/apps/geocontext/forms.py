"""
Admin form for GeoContext.

Parses the raw JSON submitted through the Editor.js-enhanced textarea,
surfacing malformed JSON as a clear form error. Block-level schema
validation continues to happen in the model's ``save()`` via the
Editor.js layer in :mod:`tosca_api.apps.core.editorjs`.
"""

from __future__ import annotations

import json

from django import forms

from .models import GeoContext
from .widgets import EditorJsWidget


class GeoContextAdminForm(forms.ModelForm):
    """GeoContext admin form wiring the Editor.js widget into ``content``."""

    class Meta:
        model = GeoContext
        fields = "__all__"
        widgets = {"content": EditorJsWidget()}

    def clean_content(self):
        value = self.cleaned_data.get("content")
        if value in (None, "", {}):
            return {"blocks": []}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                raise forms.ValidationError(f"Invalid JSON: {exc.msg}")
        raise forms.ValidationError("Content must be a JSON object.")
