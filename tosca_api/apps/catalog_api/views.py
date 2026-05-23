from django.http import HttpResponse
from rest_framework.exceptions import NotAcceptable, NotFound
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from tosca_api.apps.geodata_providers.services.queries import (
    ProviderQueryService,
    StyleQueryService,
)

from .services.v1.geoserver_v1_builder import GeoServerV1Builder
from .services.v1.geoserver_remote_service import GeoServerRemoteService
from .services.v1.visibility_service import CatalogVisibilityService


class ProviderListV1View(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        providers = ProviderQueryService.list_providers()
        payload = [
            {
                "id": provider["id"],
                "name": provider["name"],
                "base_url": provider["base_url"],
            }
            for provider in providers
        ]
        return Response(payload)


class WorkspaceListV1View(APIView):
    permission_classes = [AllowAny]

    def get(self, request, provider_id):
        try:
            workspaces = CatalogVisibilityService.list_visible_workspaces(
                provider_id=provider_id,
            )
        except Exception as exc:
            raise NotFound("Provider not found.") from exc
        payload = GeoServerV1Builder.build_workspace_list(
            request=request,
            workspaces=workspaces,
            provider_id=provider_id,
        )
        return Response(payload)


class GlobalLayerListV1View(APIView):
    permission_classes = [AllowAny]

    def get(self, request, provider_id):
        try:
            layers = CatalogVisibilityService.list_visible_layers(provider_id=provider_id)
        except Exception as exc:
            raise NotFound("Provider not found.") from exc
        payload = GeoServerV1Builder.build_layer_list(
            request=request,
            layers=layers,
            provider_id=provider_id,
        )
        return Response(payload)


class WorkspaceLayerListV1View(APIView):
    permission_classes = [AllowAny]

    def get(self, request, workspace_name: str, provider_id):
        try:
            CatalogVisibilityService.get_visible_workspace(
                workspace_name=workspace_name,
                provider_id=provider_id,
            )
        except Exception as exc:
            raise NotFound("Workspace not found.") from exc

        layers = CatalogVisibilityService.list_visible_layers(
            workspace_name=workspace_name,
            provider_id=provider_id,
        )
        payload = GeoServerV1Builder.build_layer_list(
            request=request,
            layers=layers,
            provider_id=provider_id,
        )
        return Response(payload)


class LayerInfoV1View(APIView):
    permission_classes = [AllowAny]

    def get(self, request, workspace_name: str, layer_name: str, provider_id):
        try:
            layer = CatalogVisibilityService.get_visible_layer(
                workspace_name=workspace_name,
                layer_name=layer_name,
                provider_id=provider_id,
            )
        except Exception as exc:
            raise NotFound("Layer not found.") from exc

        remote_layer_info = GeoServerRemoteService.get_layer_info(layer=layer)
        payload = GeoServerV1Builder.build_layer_info(
            request=request,
            layer=layer,
            remote_layer_info=remote_layer_info,
            provider_id=provider_id,
        )
        return Response(payload)


class LayerDetailV1View(APIView):
    permission_classes = [AllowAny]

    def get(self, request, workspace_name: str, layer_name: str, provider_id):
        try:
            layer = CatalogVisibilityService.get_visible_layer(
                workspace_name=workspace_name,
                layer_name=layer_name,
                provider_id=provider_id,
            )
        except Exception as exc:
            raise NotFound("Layer resource not found.") from exc

        remote_layer_detail = GeoServerRemoteService.get_layer_resource_detail(layer=layer)
        payload = GeoServerV1Builder.build_layer_detail(
            request=request,
            layer=layer,
            remote_layer_detail=remote_layer_detail,
        )
        return Response(payload)


class StyleDetailV1View(APIView):
    permission_classes = [AllowAny]
    renderer_classes = [JSONRenderer]

    def perform_content_negotiation(self, request, force=False):
        """
        Style responses are mixed-mode:
        - MBStyle returns JSON via DRF Response
        - SLD returns raw XML via Django HttpResponse

        DRF's default content negotiation rejects `Accept: application/vnd.ogc.sld+xml`
        before the view executes because no XML renderer is registered. For this
        endpoint we handle SLD acceptability manually inside `get()`, so we keep
        negotiation permissive here.
        """
        return (self.renderer_classes[0](), self.renderer_classes[0].media_type)

    def get(self, request, provider_id, style_ref: str | None = None):
        try:
            CatalogVisibilityService.get_visible_provider(provider_id=provider_id)
        except Exception as exc:
            raise NotFound("Provider not found.") from exc

        if style_ref is None:
            styles = StyleQueryService.list_styles(
                provider_id=provider_id,
                valid_only=True,
            )
            return Response(styles)

        try:
            style = StyleQueryService.resolve_style_reference(
                style_ref=style_ref,
                provider_id=provider_id,
            )
        except Exception as exc:
            raise NotFound("Style not found.") from exc

        if style.format == "mbstyle":
            return Response(StyleQueryService.get_style_content(style_id=style.id))

        accepted = request.headers.get("Accept", "*/*")
        if "application/json" in accepted and "xml" not in accepted and "*/*" not in accepted:
            raise NotAcceptable("SLD styles are not available as JSON.")

        return HttpResponse(
            StyleQueryService.get_style_content(style_id=style.id),
            content_type="application/vnd.ogc.sld+xml",
        )
