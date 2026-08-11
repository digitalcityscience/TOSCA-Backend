import mimetypes

from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from rest_framework.exceptions import NotAcceptable, NotFound
from rest_framework.permissions import AllowAny
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from tosca_api.apps.geodata_providers.services.queries import (
    ProviderQueryService,
    StyleQueryService,
)
from tosca_api.apps.geodata_providers.models import SpriteAsset

from .services.v1.group_v1_builder import LayerGroupV1Builder
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
        groups = CatalogVisibilityService.list_visible_groups(provider_id=provider_id)
        if groups:
            payload.update(
                LayerGroupV1Builder.build_group_list(
                    request=request,
                    groups=groups,
                    provider_id=provider_id,
                )
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
        groups = CatalogVisibilityService.list_visible_groups(
            workspace_name=workspace_name,
            provider_id=provider_id,
        )
        if groups:
            payload.update(
                LayerGroupV1Builder.build_group_list(
                    request=request,
                    groups=groups,
                    provider_id=provider_id,
                )
            )
        return Response(payload)


class LayerGroupDetailV1View(APIView):
    permission_classes = [AllowAny]

    def get(self, request, workspace_name: str, group_name: str, provider_id):
        try:
            group = CatalogVisibilityService.get_visible_group(
                workspace_name=workspace_name,
                group_name=group_name,
                provider_id=provider_id,
            )
        except Exception as exc:
            raise NotFound("Layer group not found.") from exc
        return Response(
            LayerGroupV1Builder.build_group_detail(
                request=request,
                group=group,
                provider_id=provider_id,
            )
        )


class LayerGroupLegendV1View(APIView):
    """Serve the author-curated legend for one visible layer group."""

    permission_classes = [AllowAny]

    def get(self, request, workspace_name: str, group_name: str, provider_id):
        try:
            group = CatalogVisibilityService.get_visible_group(
                workspace_name=workspace_name,
                group_name=group_name,
                provider_id=provider_id,
            )
        except Exception as exc:
            raise NotFound("Layer group not found.") from exc
        if not group.legend_image:
            raise NotFound("Layer group legend not found.")
        try:
            group.legend_image.open("rb")
        except (OSError, ValueError) as exc:
            raise Http404("Layer group legend image not found.") from exc
        content_type = mimetypes.guess_type(group.legend_image.name)[0] or "image/png"
        response = FileResponse(group.legend_image.file, content_type=content_type)
        response["ETag"] = f'"{group.legend_content_hash}"'
        response["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


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
            content = StyleQueryService.get_style_content(style_id=style.id)
            if style.sprite_asset_id:
                content = dict(content)
                content["sprite"] = request.build_absolute_uri(
                    reverse(
                        "catalog-v1-provider-sprite-versioned-stem",
                        kwargs={
                            "provider_id": provider_id,
                            "sprite_id": style.sprite_asset_id,
                            "content_hash": style.sprite_asset.content_hash,
                        },
                    )
                )
            return Response(content)

        accepted = request.headers.get("Accept", "*/*")
        if "application/json" in accepted and "xml" not in accepted and "*/*" not in accepted:
            raise NotAcceptable("SLD styles are not available as JSON.")

        return HttpResponse(
            StyleQueryService.get_style_content(style_id=style.id),
            content_type="application/vnd.ogc.sld+xml",
        )


class SpriteAssetV1View(APIView):
    """Serve one resolution/format derived from a MapLibre sprite URL stem."""

    permission_classes = [AllowAny]

    def get(
        self,
        request,
        provider_id,
        sprite_id,
        asset_format: str,
        pixel_ratio: int = 1,
        content_hash: str | None = None,
    ):
        try:
            sprite = SpriteAsset.objects.select_related("geodata_engine").get(
                id=sprite_id,
                geodata_engine_id=provider_id,
                geodata_engine__is_active=True,
                validation_state="VALID",
            )
        except SpriteAsset.DoesNotExist as exc:
            raise NotFound("Sprite not found.") from exc

        if content_hash is None:
            route_name = {
                ("json", 1): "catalog-v1-provider-sprite-json-versioned",
                ("png", 1): "catalog-v1-provider-sprite-png-versioned",
                ("json", 2): "catalog-v1-provider-sprite-json-2x-versioned",
                ("png", 2): "catalog-v1-provider-sprite-png-2x-versioned",
            }.get((asset_format, pixel_ratio))
            if route_name is None:
                raise NotFound("Sprite asset format is not supported.")
            response = HttpResponseRedirect(
                reverse(
                    route_name,
                    kwargs={
                        "provider_id": provider_id,
                        "sprite_id": sprite_id,
                        "content_hash": sprite.content_hash,
                    },
                )
            )
            response["Cache-Control"] = "no-cache"
            return response

        if content_hash != sprite.content_hash:
            raise NotFound("Sprite revision not found.")

        use_high_dpi = pixel_ratio == 2 and bool(sprite.image_2x)
        index_content = sprite.index_content_2x if use_high_dpi else sprite.index_content
        image = sprite.image_2x if use_high_dpi else sprite.image

        if asset_format == "json":
            response = JsonResponse(index_content)
        elif asset_format == "png":
            try:
                image.open("rb")
            except (OSError, ValueError) as exc:
                raise Http404("Sprite image not found.") from exc
            response = FileResponse(image.file, content_type="image/png")
        else:
            raise NotFound("Sprite asset format is not supported.")
        response["ETag"] = f'"{sprite.content_hash}"'
        response["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


class SpriteStemV1View(APIView):
    """Named route used only to build the extensionless MapLibre URL stem."""

    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        raise NotFound("Append .json or .png to the sprite URL.")
