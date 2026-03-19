from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response

from .filters import apply_event_filters
from .models import Event
from .serializers import (
    BBoxSerializer,
    EventWriteSerializer,
    EventDetailSerializer,
    EventGeoSerializer,
    EventListSerializer,
    GeometryFilterSerializer,
)


class EventCursorPagination(CursorPagination):
    """Cursor pagination for events, ordered by start_datetime."""

    page_size = 20
    ordering = "start_datetime"


class EventViewSet(viewsets.ModelViewSet):
    """
    API endpoint for Event operations.

    ## List Endpoints

    ### Calendar View (default)
    ```
    GET /api/v1/events/
    ```
    Returns all events (with or without location) as JSON.
    By default, only future events are returned.

    **Query Parameters:**
    - `campaign_id`: Filter by campaign UUID
    - `include_past`: Set to `true` to include past events (default: false)
    - `start_after`: Filter events starting after this datetime
    - `start_before`: Filter events starting before this datetime
    - `status`: Filter by status (default: published)

    ### Map View (bbox)
    ```
    GET /api/v1/events/?bbox=min_lon,min_lat,max_lon,max_lat
    ```
    Returns events WITH location inside bounding box as GeoJSON FeatureCollection.

    ### Map View (polygon) - POST
    ```
    POST /api/v1/events/within/
    {
        "geometry": {GeoJSON Polygon/MultiPolygon},
        "campaign_id": "uuid",
        "include_past": false
    }
    ```
    Returns events WITH location inside geometry as GeoJSON FeatureCollection.
    """

    queryset = Event.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = EventCursorPagination

    def get_serializer_class(self):
        """Return appropriate serializer based on action and request."""
        if self.action == "list":
            # Check if spatial filter is applied
            if self._is_spatial_request():
                return EventGeoSerializer
            return EventListSerializer
        if self.action == "retrieve":
            return EventDetailSerializer
        if self.action == "within":
            return EventGeoSerializer
        return EventWriteSerializer

    def _is_spatial_request(self) -> bool:
        """Check if request has bbox parameter."""
        return bool(self.request.query_params.get("bbox"))

    def get_queryset(self):
        """
        Filter queryset based on request parameters.

        - Default: Only upcoming events (start_datetime >= now)
        - Spatial requests (bbox): Only events with location
        - Status filtering
        - Campaign filtering
        """
        queryset = super().get_queryset()

        if self.action == "list":
            bbox_serializer = BBoxSerializer(data=self.request.query_params)
            bbox_serializer.is_valid(raise_exception=True)
            validated_filters = dict(bbox_serializer.validated_data)
            validated_filters["spatial_geometry"] = validated_filters.pop("bbox", None)

            queryset = apply_event_filters(queryset, filters=validated_filters)

        # Optimize queries
        if self.action == "retrieve":
            queryset = queryset.select_related(
                "context",
                "campaign",
                "event_type",
                "series",
                "organizer",
            )
            queryset = queryset.prefetch_related("eventlayer_set__layer")
        else:
            queryset = queryset.select_related("campaign", "event_type", "series")

        return queryset

    def perform_create(self, serializer):
        """Set the organizer to the current user."""
        serializer.save(organizer=self.request.user)

    def list(self, request, *args, **kwargs):
        """
        Override list to return GeoJSON FeatureCollection for spatial requests.
        Non-spatial requests use standard paginated response.
        """
        if self._is_spatial_request():
            queryset = self.filter_queryset(self.get_queryset()).order_by("start_datetime")
            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)
        return super().list(request, *args, **kwargs)

    @action(detail=False, methods=["post"], url_path="within")
    def within(self, request):
        """
        Filter events within a given geometry (Polygon/MultiPolygon).

        Returns GeoJSON FeatureCollection of events with location inside the geometry.

        Request body:
        {
            "geometry": {GeoJSON Polygon or MultiPolygon},
            "campaign_id": "uuid (optional)",
            "include_past": false,
            "start_after": "datetime (optional)",
            "start_before": "datetime (optional)",
            "status": "published"
        }
        """
        filter_serializer = GeometryFilterSerializer(data=request.data)
        filter_serializer.is_valid(raise_exception=True)
        data = dict(filter_serializer.validated_data)
        data["spatial_geometry"] = data.pop("geometry")

        queryset = apply_event_filters(Event.objects.all(), filters=data)
        queryset = queryset.order_by("start_datetime").select_related(
            "campaign",
            "event_type",
            "series",
        )

        # Serialize as GeoJSON
        serializer = EventGeoSerializer(queryset, many=True)
        return Response(serializer.data)
