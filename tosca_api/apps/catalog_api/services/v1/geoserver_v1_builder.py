from datetime import timezone as dt_timezone

from django.urls import reverse
from django.utils import timezone


class GeoServerV1Builder:
    """Compose GeoServer REST-shaped payload fragments for the v1 surface."""

    @classmethod
    def build_workspace_list(cls, *, request, workspaces, provider_id=None) -> dict:
        cls._require_provider_id(provider_id)
        return {
            "workspaces": {
                "workspace": [
                    {
                        "name": workspace.name,
                        "href": request.build_absolute_uri(
                            reverse(
                                "catalog-v1-provider-workspace-layer-list",
                                kwargs=cls._route_kwargs(
                                    {"workspace_name": workspace.name},
                                    provider_id=provider_id,
                                ),
                            )
                        ),
                    }
                    for workspace in workspaces
                ]
            }
        }

    @classmethod
    def build_layer_list(cls, *, request, layers, provider_id=None) -> dict:
        cls._require_provider_id(provider_id)
        return {
            "layers": {
                "layer": [
                    {
                        "name": layer.name,
                        "href": request.build_absolute_uri(
                            reverse(
                                "catalog-v1-provider-layer-info",
                                kwargs=cls._route_kwargs(
                                    {
                                        "workspace_name": layer.workspace.name,
                                        "layer_name": layer.name,
                                    },
                                    provider_id=provider_id,
                                ),
                            )
                        ),
                    }
                    for layer in layers
                ]
            }
        }

    @classmethod
    def build_layer_info(cls, *, request, layer, remote_layer_info: dict | None, provider_id=None) -> dict:
        cls._require_provider_id(provider_id)
        remote_layer = cls._extract_remote_layer(remote_layer_info)
        default_style = cls._get_default_style(layer=layer, remote_layer=remote_layer)
        layer_type = cls._get_layer_type(layer)
        resource_class = "coverage" if layer_type == "RASTER" else "featureType"

        payload = {
            "layer": {
                "name": layer.name,
                "type": layer_type,
                "defaultStyle": {
                    "name": default_style["name"],
                    "href": request.build_absolute_uri(
                        reverse(
                            "catalog-v1-provider-style-detail",
                            kwargs=cls._route_kwargs(
                                {"style_ref": default_style["href_ref"]},
                                provider_id=provider_id,
                            ),
                        )
                    ),
                },
                "resource": {
                    "@class": resource_class,
                    "name": layer.name,
                    "href": request.build_absolute_uri(
                        reverse(
                            "catalog-v1-provider-layer-detail",
                            kwargs=cls._route_kwargs(
                                {
                                    "workspace_name": layer.workspace.name,
                                    "layer_name": layer.name,
                                },
                                provider_id=provider_id,
                            ),
                        )
                    ),
                },
                "attribution": {
                    "logoWidth": 0,
                    "logoHeight": 0,
                },
                "dateCreated": cls._as_iso8601(layer.created_at),
                "dateModified": cls._as_iso8601(layer.updated_at),
            }
        }

        if isinstance(remote_layer, dict):
            payload["layer"].update(
                {
                    key: value
                    for key, value in remote_layer.items()
                    if key in {"defaultStyle", "styles", "attribution", "type"}
                }
            )
            payload["layer"]["defaultStyle"] = {
                "name": default_style["name"],
                "href": request.build_absolute_uri(
                    reverse(
                        "catalog-v1-provider-style-detail",
                        kwargs=cls._route_kwargs(
                            {"style_ref": default_style["href_ref"]},
                            provider_id=provider_id,
                        ),
                    )
                ),
            }
            payload["layer"]["resource"] = {
                "@class": resource_class,
                "name": layer.name,
                "href": request.build_absolute_uri(
                    reverse(
                        "catalog-v1-provider-layer-detail",
                        kwargs=cls._route_kwargs(
                            {
                                "workspace_name": layer.workspace.name,
                                "layer_name": layer.name,
                            },
                            provider_id=provider_id,
                        ),
                    )
                ),
            }

        return payload

    @classmethod
    def build_layer_detail(cls, *, request, layer, remote_layer_detail: dict | None) -> dict:
        if cls._get_layer_type(layer) == "RASTER":
            return cls._build_raster_layer_detail(layer=layer, remote_layer_detail=remote_layer_detail)
        return cls._build_vector_layer_detail(layer=layer, remote_layer_detail=remote_layer_detail)

    @classmethod
    def _build_vector_layer_detail(cls, *, layer, remote_layer_detail: dict | None) -> dict:
        if isinstance(remote_layer_detail, dict) and isinstance(remote_layer_detail.get("featureType"), dict):
            payload = dict(remote_layer_detail)
            payload["featureType"] = dict(payload["featureType"])
        else:
            payload = {
                "featureType": {
                    "name": layer.name,
                    "nativeName": layer.table_name,
                    "namespace": {
                        "name": layer.workspace.name,
                        "href": "",
                    },
                    "title": layer.title or layer.name,
                    "abstract": layer.description or "",
                    "keywords": {"string": []},
                    "nativeCRS": f"EPSG:{layer.srid}",
                    "srs": f"EPSG:{layer.srid}",
                    "nativeBoundingBox": cls._empty_bbox(layer.srid),
                    "latLonBoundingBox": cls._empty_bbox(layer.srid),
                    "projectionPolicy": "FORCE_DECLARED",
                    "enabled": True,
                    "store": {
                        "@class": "dataStore",
                        "name": layer.store.name,
                        "href": "",
                    },
                    "serviceConfiguration": False,
                    "simpleConversionEnabled": False,
                    "internationalTitle": "",
                    "internationalAbstract": "",
                    "maxFeatures": 0,
                    "numDecimals": 0,
                    "padWithZeros": False,
                    "forcedDecimal": False,
                    "overridingServiceSRS": False,
                    "skipNumberMatched": False,
                    "circularArcPresent": False,
                    "attributes": {"attribute": []},
                }
            }

        payload["featureType"]["name"] = layer.name
        payload["featureType"]["nativeName"] = payload["featureType"].get("nativeName") or layer.table_name
        payload["featureType"]["namespace"] = {
            "name": layer.workspace.name,
            "href": "",
        }
        payload["featureType"]["title"] = payload["featureType"].get("title") or layer.title or layer.name
        payload["featureType"]["abstract"] = payload["featureType"].get("abstract") or layer.description or ""
        payload["featureType"]["nativeCRS"] = payload["featureType"].get("nativeCRS") or f"EPSG:{layer.srid}"
        payload["featureType"]["srs"] = payload["featureType"].get("srs") or f"EPSG:{layer.srid}"
        payload["featureType"]["nativeBoundingBox"] = payload["featureType"].get("nativeBoundingBox") or cls._empty_bbox(layer.srid)
        payload["featureType"]["latLonBoundingBox"] = payload["featureType"].get("latLonBoundingBox") or cls._empty_bbox(layer.srid)
        payload["featureType"]["store"] = {
            "@class": "dataStore",
            "name": layer.store.name,
            "href": "",
        }
        payload["featureType"]["attributes"] = payload["featureType"].get("attributes") or {"attribute": []}
        return payload

    @classmethod
    def _build_raster_layer_detail(cls, *, layer, remote_layer_detail: dict | None) -> dict:
        if isinstance(remote_layer_detail, dict) and isinstance(remote_layer_detail.get("coverage"), dict):
            payload = dict(remote_layer_detail)
            payload["coverage"] = dict(payload["coverage"])
        else:
            payload = {
                "coverage": {
                    "name": layer.name,
                    "nativeName": layer.table_name,
                    "namespace": {
                        "name": layer.workspace.name,
                        "href": "",
                    },
                    "title": layer.title or layer.name,
                    "description": layer.description or "",
                    "keywords": {"string": []},
                    "nativeCRS": f"EPSG:{layer.srid}",
                    "srs": f"EPSG:{layer.srid}",
                    "nativeBoundingBox": cls._empty_bbox(layer.srid),
                    "latLonBoundingBox": cls._empty_bbox(layer.srid),
                    "projectionPolicy": "FORCE_DECLARED",
                    "enabled": True,
                    "metadata": {"entry": []},
                    "store": {
                        "@class": "coverageStore",
                        "name": layer.store.name,
                        "href": "",
                    },
                    "serviceConfiguration": False,
                    "simpleConversionEnabled": False,
                    "internationalTitle": "",
                    "internationalAbstract": "",
                    "nativeFormat": "GeoTIFF",
                    "grid": {
                        "@dimension": 2,
                        "range": {"low": "0 0", "high": "0 0"},
                        "transform": {
                            "scaleX": "1.0",
                            "scaleY": "-1.0",
                            "shearX": 0,
                            "shearY": 0,
                            "translateX": 0,
                            "translateY": 0,
                        },
                        "crs": f"EPSG:{layer.srid}",
                    },
                    "supportedFormats": {"string": []},
                    "interpolationMethods": {"string": []},
                    "defaultInterpolationMethod": "nearest neighbor",
                    "dimensions": {"coverageDimension": []},
                    "requestSRS": {"string": f"EPSG:{layer.srid}"},
                    "responseSRS": {"string": f"EPSG:{layer.srid}"},
                    "parameters": {"entry": []},
                    "nativeCoverageName": layer.table_name,
                }
            }

        payload["coverage"]["name"] = layer.name
        payload["coverage"]["nativeName"] = payload["coverage"].get("nativeName") or layer.table_name
        payload["coverage"]["namespace"] = {
            "name": layer.workspace.name,
            "href": "",
        }
        payload["coverage"]["title"] = payload["coverage"].get("title") or layer.title or layer.name
        payload["coverage"]["description"] = payload["coverage"].get("description") or layer.description or ""
        payload["coverage"]["nativeCRS"] = payload["coverage"].get("nativeCRS") or f"EPSG:{layer.srid}"
        payload["coverage"]["srs"] = payload["coverage"].get("srs") or f"EPSG:{layer.srid}"
        payload["coverage"]["nativeBoundingBox"] = payload["coverage"].get("nativeBoundingBox") or cls._empty_bbox(layer.srid)
        payload["coverage"]["latLonBoundingBox"] = payload["coverage"].get("latLonBoundingBox") or cls._empty_bbox(layer.srid)
        payload["coverage"]["store"] = {
            "@class": "coverageStore",
            "name": layer.store.name,
            "href": "",
        }
        return payload

    @staticmethod
    def _empty_bbox(srid: int) -> dict:
        return {
            "minx": 0,
            "maxx": 0,
            "miny": 0,
            "maxy": 0,
            "crs": f"EPSG:{srid}",
        }

    @staticmethod
    def _require_provider_id(provider_id) -> None:
        if provider_id is None:
            raise ValueError("Catalog v1 responses require provider_id-scoped routes.")

    @staticmethod
    def _route_kwargs(kwargs: dict, *, provider_id=None) -> dict:
        return {"provider_id": provider_id, **kwargs}

    @staticmethod
    def _extract_remote_layer(remote_layer_info: dict | None) -> dict | None:
        if not isinstance(remote_layer_info, dict):
            return None
        layer = remote_layer_info.get("layer")
        if isinstance(layer, dict):
            return layer
        return remote_layer_info

    @staticmethod
    def _get_layer_type(layer) -> str:
        return "RASTER" if layer.store.store_type == "geotiff" else "VECTOR"

    @staticmethod
    def _get_style_name(remote_layer: dict | None) -> str:
        if isinstance(remote_layer, dict):
            default_style = remote_layer.get("defaultStyle")
            if isinstance(default_style, dict) and default_style.get("name"):
                return default_style["name"]
        return "default"

    @classmethod
    def _get_default_style(cls, *, layer, remote_layer: dict | None) -> dict:
        active_default = next(
            (
                assignment for assignment in layer.style_assignments.all()
                if assignment.is_active and assignment.role == "default"
            ),
            None,
        )
        if active_default is not None:
            return {
                "name": active_default.style.name,
                "href_ref": str(active_default.style.id),
            }
        style_name = cls._get_style_name(remote_layer)
        return {
            "name": style_name,
            "href_ref": style_name,
        }

    @staticmethod
    def _as_iso8601(value) -> str:
        if value is None:
            return ""
        current = value
        if timezone.is_naive(current):
            current = timezone.make_aware(current, dt_timezone.utc)
        return current.isoformat().replace("+00:00", "Z")
