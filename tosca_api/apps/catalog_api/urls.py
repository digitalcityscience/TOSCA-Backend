from django.urls import path

from .views import (
    GlobalLayerListV1View,
    LayerDetailV1View,
    LayerInfoV1View,
    StyleDetailV1View,
    WorkspaceLayerListV1View,
    WorkspaceListV1View,
)

urlpatterns = [
    path(
        "workspaces",
        WorkspaceListV1View.as_view(),
        name="catalog-v1-workspace-list",
    ),
    path(
        "layers",
        GlobalLayerListV1View.as_view(),
        name="catalog-v1-layer-list",
    ),
    path(
        "workspaces/<str:workspace_name>/layers",
        WorkspaceLayerListV1View.as_view(),
        name="catalog-v1-workspace-layer-list",
    ),
    path(
        "workspaces/<str:workspace_name>/layers/<str:layer_name>",
        LayerInfoV1View.as_view(),
        name="catalog-v1-layer-info",
    ),
    path(
        "workspaces/<str:workspace_name>/resources/<str:layer_name>",
        LayerDetailV1View.as_view(),
        name="catalog-v1-layer-detail",
    ),
    path(
        "styles/<str:style_ref>",
        StyleDetailV1View.as_view(),
        name="catalog-v1-style-detail",
    ),
]
