from django.urls import path

from .views import (
    GlobalLayerListV1View,
    LayerDetailV1View,
    LayerInfoV1View,
    ProviderListV1View,
    StyleDetailV1View,
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
        "providers/<uuid:provider_id>/styles",
        StyleDetailV1View.as_view(),
        name="catalog-v1-provider-style-list",
    ),
    path(
        "providers/<uuid:provider_id>/styles/<str:style_ref>",
        StyleDetailV1View.as_view(),
        name="catalog-v1-provider-style-detail",
    ),
]
