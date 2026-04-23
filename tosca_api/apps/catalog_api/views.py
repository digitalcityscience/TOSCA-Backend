from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .services.v1.geoserver_v1_builder import GeoServerV1Builder
from .services.v1.geoserver_remote_service import GeoServerRemoteService
from .services.v1.visibility_service import CatalogVisibilityService


class WorkspaceListV1View(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        workspaces = CatalogVisibilityService.list_visible_workspaces()
        payload = GeoServerV1Builder.build_workspace_list(
            request=request,
            workspaces=workspaces,
        )
        return Response(payload)


class GlobalLayerListV1View(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        layers = CatalogVisibilityService.list_visible_layers()
        payload = GeoServerV1Builder.build_layer_list(
            request=request,
            layers=layers,
        )
        return Response(payload)


class WorkspaceLayerListV1View(APIView):
    permission_classes = [AllowAny]

    def get(self, request, workspace_name: str):
        try:
            CatalogVisibilityService.get_visible_workspace(workspace_name=workspace_name)
        except Exception as exc:
            raise NotFound("Workspace not found.") from exc

        layers = CatalogVisibilityService.list_visible_layers(workspace_name=workspace_name)
        payload = GeoServerV1Builder.build_layer_list(
            request=request,
            layers=layers,
        )
        return Response(payload)


class LayerInfoV1View(APIView):
    permission_classes = [AllowAny]

    def get(self, request, workspace_name: str, layer_name: str):
        try:
            layer = CatalogVisibilityService.get_visible_layer(
                workspace_name=workspace_name,
                layer_name=layer_name,
            )
        except Exception as exc:
            raise NotFound("Layer not found.") from exc

        remote_layer_info = GeoServerRemoteService.get_layer_info(layer=layer)
        payload = GeoServerV1Builder.build_layer_info(
            request=request,
            layer=layer,
            remote_layer_info=remote_layer_info,
        )
        return Response(payload)


class LayerDetailV1View(APIView):
    permission_classes = [AllowAny]

    def get(self, request, workspace_name: str, layer_name: str):
        try:
            layer = CatalogVisibilityService.get_visible_layer(
                workspace_name=workspace_name,
                layer_name=layer_name,
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

    def get(self, request, style_name: str):
        style_payload = GeoServerRemoteService.get_style_detail(style_name=style_name)
        if style_payload is None:
            raise NotFound("Style not found.")
        return Response(style_payload)
