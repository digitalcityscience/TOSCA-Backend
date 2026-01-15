from __future__ import annotations

from uuid import uuid4


def generate_unique_identifier() -> str:
    """Return a simple UUID4 hex string."""

    return uuid4().hex
