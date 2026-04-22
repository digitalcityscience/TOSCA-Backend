"""
Editor.js admin widget for GeoContext.

Renders a JSON textarea as the no-JS fallback; a vendored Editor.js bundle
enhances it into a WYSIWYG authoring surface when JavaScript loads. Write
submission remains standard Django POST — see ``init.js``.
"""

from __future__ import annotations

import json

from django import forms


class EditorJsWidget(forms.Textarea):
    """Textarea widget enhanced by vendored Editor.js."""

    template_name = "geocontext/widgets/editorjs.html"

    class Media:
        css = {"all": ("geocontext/editorjs/editor.css",)}
        js = (
            "geocontext/editorjs/vendor/editorjs.umd.js",
            "geocontext/editorjs/vendor/header.umd.js",
            "geocontext/editorjs/vendor/list.umd.js",
            "geocontext/editorjs/vendor/quote.umd.js",
            "geocontext/editorjs/vendor/delimiter.umd.js",
            "geocontext/editorjs/vendor/code.umd.js",
            "geocontext/editorjs/init.js",
        )

    def format_value(self, value) -> str:
        if value in (None, ""):
            return json.dumps({"blocks": []})
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)
