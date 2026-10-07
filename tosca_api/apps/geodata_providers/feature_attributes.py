"""Normalize GeoServer feature-type attribute descriptors."""

from __future__ import annotations

GEOMETRY_BINDINGS = frozenset(
    {
        "Geometry",
        "GeometryCollection",
        "Point",
        "LineString",
        "LinearRing",
        "Polygon",
        "MultiPoint",
        "MultiLineString",
        "MultiPolygon",
    }
)


def normalize_feature_attributes(raw_attributes) -> list[dict]:
    """Return ``[{"name", "type"}]`` for the non-geometry attributes, in order.

    ``raw_attributes`` is GeoServer's ``featureType.attributes.attribute``
    value: a list of ``{"name", "binding", ...}`` dicts, or a single dict when
    the feature type has one attribute. ``type`` is the short Java binding
    name (``"java.lang.String"`` → ``"String"``).
    """
    if isinstance(raw_attributes, dict):
        raw_attributes = [raw_attributes]
    if not isinstance(raw_attributes, list):
        return []

    attributes = []
    seen = set()
    for item in raw_attributes:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip() or name in seen:
            continue
        binding = item.get("binding")
        attribute_type = binding.rsplit(".", 1)[-1] if isinstance(binding, str) else ""
        if attribute_type in GEOMETRY_BINDINGS:
            continue
        seen.add(name)
        attributes.append({"name": name, "type": attribute_type})
    return attributes
