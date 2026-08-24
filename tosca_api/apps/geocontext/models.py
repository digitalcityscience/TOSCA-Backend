"""The legacy GeoContext model was retired in migration 0006.

This app temporarily remains installed because it owns the shared Editor.js
widget, static assets, and upload routes. Feature content now lives directly on
GeoStory, Event, EventSeries, and GeoFeedback.
"""

from tosca_api.apps.core.editorjs import empty_document


def empty_editorjs_document() -> dict:
    """Historical migration compatibility; not a model field default anymore."""
    return empty_document()
