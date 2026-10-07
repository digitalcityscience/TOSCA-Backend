"""Public catalog API for external services and categories (ticket 04).

Mounted under ``/api/v1/catalog/`` beside the provider-scoped routes. Read
only and anonymous-friendly: the default authentication classes still run,
so a Keycloak Bearer token (or an admin session) makes ``PRIVATE`` rows
visible. Django serves metadata only; the browser fetches features and
observations straight from the external services.
"""

from __future__ import annotations

from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Category, CategoryItem, ExternalService
from .visibility import ExternalCatalogVisibilityService as Visibility


def _authenticated(request) -> bool:
    return bool(request.user and request.user.is_authenticated)


def service_payload(service: ExternalService) -> dict:
    payload = {
        "id": str(service.pk),
        "slug": service.slug,
        "service_type": service.service_type,
        "title": service.title,
        "base_url": service.base_url,
        "attribution": service.attribution,
        "capabilities": {
            "show_uncurated": service.show_uncurated,
            "full_load": service.allow_full_load,
            "live_updates": service.allow_live_updates,
            "server_filters": service.allow_server_filters,
            "max_features": service.max_features,
        },
    }
    if service.service_type == ExternalService.ServiceType.SENSORTHINGS:
        payload["mqtt_url"] = service.mqtt_url
    return payload


def item_payload(item: CategoryItem) -> dict:
    service = item.service
    payload = {
        "id": str(item.pk),
        "title": item.title,
        "description": item.description,
        "service": service.slug,
        "service_type": service.service_type,
    }
    if service.service_type == ExternalService.ServiceType.SENSORTHINGS:
        payload["sensorthings"] = {
            "service_name": item.sta_service_name,
            "layer_name": item.sta_layer_name,
        }
    else:
        payload["ogc"] = {
            "dataset_id": item.ogc_dataset_id,
            "dataset_title": item.dataset_title,
            "collection_id": item.ogc_collection_id,
        }
        payload["defaults"] = {"properties": item.default_properties, "filter": item.default_filter}
    payload["style"] = {"color": item.color}
    payload["loading"] = {"min_zoom": float(item.min_zoom) if item.min_zoom is not None else None}
    payload["availability"] = {
        "state": item.availability_state,
        "feature_count": item.feature_count,
        "checked_at": item.last_checked_at.isoformat() if item.last_checked_at else None,
    }
    return payload


class ExternalServiceListView(APIView):
    """Every listed service with its capabilities."""

    permission_classes = [AllowAny]

    def get(self, request):
        services = Visibility.services(authenticated=_authenticated(request))
        return Response([service_payload(service) for service in services])


class ExternalCategoryListView(APIView):
    """Sidebar list of listed categories (no items)."""

    permission_classes = [AllowAny]

    def get(self, request):
        categories = Visibility.categories(authenticated=_authenticated(request))
        return Response(
            [
                {
                    "slug": category.slug,
                    "title": category.title,
                    "description": category.description,
                    "display_order": category.display_order,
                    "item_count": category.visible_item_count,
                }
                for category in categories
            ]
        )


class ExternalCategoryDetailView(APIView):
    """One listed category with everything the map needs for its items."""

    permission_classes = [AllowAny]

    def get(self, request, slug: str):
        try:
            category = Visibility.category(slug=slug, authenticated=_authenticated(request))
        except Category.DoesNotExist as exc:
            raise NotFound("Category not found.") from exc
        return Response(
            {
                "slug": category.slug,
                "title": category.title,
                "description": category.description,
                "items": [item_payload(item) for item in category.visible_items],
            }
        )
