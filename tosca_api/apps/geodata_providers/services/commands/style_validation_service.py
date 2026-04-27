import json
import xml.etree.ElementTree as ET


class StyleValidationService:
    SUPPORTED_MBSTYLE_LAYER_TYPES = {
        "fill",
        "line",
        "symbol",
        "circle",
        "heatmap",
        "fill-extrusion",
        "raster",
        "hillshade",
        "background",
    }

    @classmethod
    def validate(cls, *, content: str, style_format: str) -> dict:
        if style_format == "sld":
            return cls.validate_sld(content=content)
        if style_format == "mbstyle":
            return cls.validate_mbstyle(content=content)
        return cls._invalid(f"Unsupported style format: {style_format}")

    @classmethod
    def validate_sld(cls, *, content: str) -> dict:
        if not content:
            return cls._invalid("Style content is required.")
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            return cls._invalid(f"Malformed XML: {exc}")

        if cls._local_name(root.tag) != "StyledLayerDescriptor":
            return cls._invalid("SLD root element must be StyledLayerDescriptor.")

        has_layer = any(
            cls._local_name(element.tag) in {"NamedLayer", "UserLayer"}
            for element in root.iter()
        )
        if not has_layer:
            return cls._invalid("SLD must contain at least one NamedLayer or UserLayer.")

        return cls._valid("sld")

    @classmethod
    def validate_mbstyle(cls, *, content: str) -> dict:
        if not content:
            return cls._invalid("Style content is required.")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            return cls._invalid(f"Malformed JSON: {exc}")

        if not isinstance(payload, dict):
            return cls._invalid("MBStyle root must be a JSON object.")
        if payload.get("version") != 8:
            return cls._invalid("MBStyle version must be 8.")
        sources = payload.get("sources")
        if sources is not None and not isinstance(sources, dict):
            return cls._invalid("MBStyle sources must be an object when provided.")
        layers = payload.get("layers")
        if not isinstance(layers, list):
            return cls._invalid("MBStyle layers must be a list.")

        errors = []
        for index, layer in enumerate(layers):
            if not isinstance(layer, dict):
                errors.append(f"Layer {index} must be an object.")
                continue
            if not layer.get("id"):
                errors.append(f"Layer {index} is missing id.")
            layer_type = layer.get("type")
            if not layer_type:
                errors.append(f"Layer {index} is missing type.")
            elif layer_type not in cls.SUPPORTED_MBSTYLE_LAYER_TYPES:
                errors.append(f"Layer {layer.get('id', index)} has unsupported type: {layer_type}.")

        if errors:
            return {"valid": False, "errors": errors, "warnings": [], "metadata": {"format": "mbstyle"}}
        return cls._valid("mbstyle")

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    @staticmethod
    def _valid(style_format: str) -> dict:
        return {"valid": True, "errors": [], "warnings": [], "metadata": {"format": style_format}}

    @staticmethod
    def _invalid(error: str) -> dict:
        return {"valid": False, "errors": [error], "warnings": [], "metadata": {}}
