"""External catalog routes, included by ``catalog_api.urls`` (ticket 04)."""

from django.urls import path

from .api import ExternalCategoryDetailView, ExternalCategoryListView, ExternalServiceListView

urlpatterns = [
    path("external-services", ExternalServiceListView.as_view(), name="catalog-v1-external-service-list"),
    path("external-categories", ExternalCategoryListView.as_view(), name="catalog-v1-external-category-list"),
    path(
        "external-categories/<slug:slug>",
        ExternalCategoryDetailView.as_view(),
        name="catalog-v1-external-category-detail",
    ),
]
