from __future__ import annotations

from typing import Any

class GeoServerRemoteService:
    """Best-effort GeoServer metadata fetchers for v1 responses."""

    @classmethod
    def get_layer_info(cls, *, layer) -> dict[str, Any] | None:
        client = cls._build_client(layer)
        if client is None:
            return None

        result = client.get_layer_info(layer.workspace.name, layer.name)
        if not result:
            return None
        details = result.get("details")
        if isinstance(details, dict):
            return details
        return None

    @classmethod
    def get_layer_resource_detail(cls, *, layer) -> dict[str, Any] | None:
        client = cls._build_client(layer)
        if client is None:
            return None

        if layer.store.store_type == "geotiff":
            path = (
                f"/rest/workspaces/{layer.workspace.name}/coveragestores/"
                f"{layer.store.name}/coverages/{layer.name}.json"
            )
        else:
            path = (
                f"/rest/workspaces/{layer.workspace.name}/datastores/"
                f"{layer.store.name}/featuretypes/{layer.name}.json"
            )
        return cls._get_json(client=client, path=path)

    @classmethod
    def get_style_detail(cls, *, style_name: str) -> dict[str, Any] | None:
        from tosca_api.apps.geodata_providers.models import GeodataEngine

        engine = GeodataEngine.objects.filter(
            is_active=True,
            engine_type="geoserver",
        ).order_by("-is_default", "name").first()
        if engine is None:
            return None

        from tosca_api.apps.geodata_providers.geoserver.client import GeoServerClient

        client = GeoServerClient(
            engine.base_url,
            engine.admin_username,
            engine.decrypted_admin_password,
        )
        for path in (
            f"/rest/styles/{style_name}.mbstyle",
            f"/rest/styles/{style_name}.json",
        ):
            payload = cls._get_json(client=client, path=path, accept_mbstyle=True)
            if payload is not None:
                return payload
        return None

    @classmethod
    def _build_client(cls, layer):
        engine = layer.workspace.geodata_engine
        if engine is None or engine.engine_type != "geoserver":
            return None
        from tosca_api.apps.geodata_providers.geoserver.client import GeoServerClient

        return GeoServerClient(
            engine.base_url,
            engine.admin_username,
            engine.decrypted_admin_password,
        )

    @classmethod
    def _get_json(
        cls,
        *,
        client,
        path: str,
        accept_mbstyle: bool = False,
    ) -> dict[str, Any] | None:
        headers = None
        if accept_mbstyle:
            headers = {"Accept": "application/vnd.geoserver.mbstyle+json, application/json"}
        try:
            response = client._request("get", path, headers=headers)
        except Exception:
            return None
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None
