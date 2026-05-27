"""
Admin form for GeoContext.

Parses and validates the raw JSON submitted through the Editor.js-enhanced
textarea, surfacing malformed JSON and block-level schema issues as clear
form errors.
"""

from __future__ import annotations

import json

from django import forms

from tosca_api.apps.core.editorjs import validate_and_normalize

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
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as exc:
                raise forms.ValidationError(f"Invalid JSON: {exc.msg}")
        else:
            raise forms.ValidationError("Content must be a JSON object.")

        try:
            return validate_and_normalize(parsed)
        except forms.ValidationError as exc:
            raise forms.ValidationError(exc.messages)
