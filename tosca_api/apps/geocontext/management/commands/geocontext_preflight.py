"""Compatibility alias for the renamed ``content_preflight`` command."""

from .content_preflight import Command as ContentPreflightCommand


class Command(ContentPreflightCommand):
    help = (
        "Deprecated alias for content_preflight. Scans feature-owned "
        "Editor.js content without modifying rows."
    )
