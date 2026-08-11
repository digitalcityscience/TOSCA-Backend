from django.urls import path

from .views import (
    GlobalLayerListV1View,
    LayerGroupDetailV1View,
    LayerGroupLegendV1View,
    LayerDetailV1View,
    LayerInfoV1View,
    ProviderListV1View,
    StyleDetailV1View,
    SpriteAssetV1View,
    SpriteStemV1View,
    WorkspaceLayerListV1View,
    WorkspaceListV1View,
)

urlpatterns = [
    path(
        "providers",
        ProviderListV1View.as_view(),
        name="catalog-v1-provider-list",
    ),
    path(
        "providers/<uuid:provider_id>/workspaces",
        WorkspaceListV1View.as_view(),
        name="catalog-v1-provider-workspace-list",
    ),
    path(
        "providers/<uuid:provider_id>/layers",
        GlobalLayerListV1View.as_view(),
        name="catalog-v1-provider-layer-list",
    ),
    path(
        "providers/<uuid:provider_id>/workspaces/<str:workspace_name>/layers",
        WorkspaceLayerListV1View.as_view(),
        name="catalog-v1-provider-workspace-layer-list",
    ),
    path(
        "providers/<uuid:provider_id>/workspaces/<str:workspace_name>/layers/<str:layer_name>",
        LayerInfoV1View.as_view(),
        name="catalog-v1-provider-layer-info",
    ),
    path(
        "providers/<uuid:provider_id>/workspaces/<str:workspace_name>/resources/<str:layer_name>",
        LayerDetailV1View.as_view(),
        name="catalog-v1-provider-layer-detail",
    ),
    path(
        "providers/<uuid:provider_id>/workspaces/<str:workspace_name>/groups/<str:group_name>",
        LayerGroupDetailV1View.as_view(),
        name="catalog-v1-provider-workspace-group-detail",
    ),
    path(
        "providers/<uuid:provider_id>/workspaces/<str:workspace_name>/groups/<str:group_name>/legend",
        LayerGroupLegendV1View.as_view(),
        name="catalog-v1-provider-workspace-group-legend",
    ),
    path(
        "providers/<uuid:provider_id>/styles",
        StyleDetailV1View.as_view(),
        name="catalog-v1-provider-style-list",
    ),
    path(
        "providers/<uuid:provider_id>/styles/<str:style_ref>",
        StyleDetailV1View.as_view(),
        name="catalog-v1-provider-style-detail",
    ),
    path(
        "providers/<uuid:provider_id>/sprites/<uuid:sprite_id>",
        SpriteStemV1View.as_view(),
        name="catalog-v1-provider-sprite-stem",
    ),
    path(
        "providers/<uuid:provider_id>/sprites/<uuid:sprite_id>.json",
        SpriteAssetV1View.as_view(),
        {"asset_format": "json", "pixel_ratio": 1},
        name="catalog-v1-provider-sprite-json",
    ),
    path(
        "providers/<uuid:provider_id>/sprites/<uuid:sprite_id>.png",
        SpriteAssetV1View.as_view(),
        {"asset_format": "png", "pixel_ratio": 1},
        name="catalog-v1-provider-sprite-png",
    ),
    path(
        "providers/<uuid:provider_id>/sprites/<uuid:sprite_id>@2x.json",
        SpriteAssetV1View.as_view(),
        {"asset_format": "json", "pixel_ratio": 2},
        name="catalog-v1-provider-sprite-json-2x",
    ),
    path(
        "providers/<uuid:provider_id>/sprites/<uuid:sprite_id>@2x.png",
        SpriteAssetV1View.as_view(),
        {"asset_format": "png", "pixel_ratio": 2},
        name="catalog-v1-provider-sprite-png-2x",
    ),
    path(
        "providers/<uuid:provider_id>/sprites/<uuid:sprite_id>/<str:content_hash>@2x.json",
        SpriteAssetV1View.as_view(),
        {"asset_format": "json", "pixel_ratio": 2},
        name="catalog-v1-provider-sprite-json-2x-versioned",
    ),
    path(
        "providers/<uuid:provider_id>/sprites/<uuid:sprite_id>/<str:content_hash>@2x.png",
        SpriteAssetV1View.as_view(),
        {"asset_format": "png", "pixel_ratio": 2},
        name="catalog-v1-provider-sprite-png-2x-versioned",
    ),
    path(
        "providers/<uuid:provider_id>/sprites/<uuid:sprite_id>/<str:content_hash>.json",
        SpriteAssetV1View.as_view(),
        {"asset_format": "json", "pixel_ratio": 1},
        name="catalog-v1-provider-sprite-json-versioned",
    ),
    path(
        "providers/<uuid:provider_id>/sprites/<uuid:sprite_id>/<str:content_hash>.png",
        SpriteAssetV1View.as_view(),
        {"asset_format": "png", "pixel_ratio": 1},
        name="catalog-v1-provider-sprite-png-versioned",
    ),
    path(
        "providers/<uuid:provider_id>/sprites/<uuid:sprite_id>/<str:content_hash>",
        SpriteStemV1View.as_view(),
        name="catalog-v1-provider-sprite-versioned-stem",
    ),
]
