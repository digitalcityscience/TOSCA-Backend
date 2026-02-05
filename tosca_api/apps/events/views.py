from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response

from .models import CalendarEvent
from .serializers import (
    BBoxSerializer,
    CalendarEventCreateSerializer,
    CalendarEventDetailSerializer,
    CalendarEventGeoSerializer,
    CalendarEventListSerializer,
    GeometryFilterSerializer,
)


class EventCursorPagination(CursorPagination):
    """Cursor pagination for events, ordered by start_datetime."""

    page_size = 20
    ordering = "start_datetime"


class CalendarEventViewSet(viewsets.ModelViewSet):
    """
    API endpoint for CalendarEvent operations.

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

    queryset = CalendarEvent.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = EventCursorPagination

    def get_serializer_class(self):
        """Return appropriate serializer based on action and request."""
        if self.action == "list":
            # Check if spatial filter is applied
            if self._is_spatial_request():
                return CalendarEventGeoSerializer
            return CalendarEventListSerializer
        if self.action == "retrieve":
            return CalendarEventDetailSerializer
        if self.action == "within":
            return CalendarEventGeoSerializer
        return CalendarEventCreateSerializer

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

        # Status filter (default: published for list)
        if self.action in ("list", "within"):
            status_param = self.request.query_params.get("status", "published")
            queryset = queryset.filter(status=status_param)

        # Campaign filter
        campaign_id = self.request.query_params.get("campaign_id")
        if campaign_id:
            queryset = queryset.filter(campaign_id=campaign_id)

        # Time filters
        include_past = self.request.query_params.get("include_past", "").lower() == "true"
        if not include_past and self.action == "list":
            queryset = queryset.filter(start_datetime__gte=timezone.now())

        start_after = self.request.query_params.get("start_after")
        if start_after:
            queryset = queryset.filter(start_datetime__gte=start_after)

        start_before = self.request.query_params.get("start_before")
        if start_before:
            queryset = queryset.filter(start_datetime__lte=start_before)

        # Spatial filter (bbox)
        if self._is_spatial_request():
            bbox_serializer = BBoxSerializer(data=self.request.query_params)
            bbox_serializer.is_valid(raise_exception=True)
            bbox_geom = bbox_serializer.validated_data.get("bbox")

            if bbox_geom:
                # Only events with location, within bbox
                queryset = queryset.filter(
                    location__isnull=False,
                    location__within=bbox_geom,
                )

        # Optimize queries
        if self.action == "retrieve":
            queryset = queryset.select_related("context", "campaign", "organizer")
            queryset = queryset.prefetch_related("eventlayer_set__layer")
        else:
            queryset = queryset.select_related("campaign")

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
            queryset = self.filter_queryset(self.get_queryset())
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
        # Validate input
        filter_serializer = GeometryFilterSerializer(data=request.data)
        filter_serializer.is_valid(raise_exception=True)
        data = filter_serializer.validated_data

        # Build queryset
        queryset = CalendarEvent.objects.filter(
            location__isnull=False,
            location__within=data["geometry"],
            status=data.get("status", CalendarEvent.Status.PUBLISHED),
        )

        # Campaign filter
        if data.get("campaign_id"):
            queryset = queryset.filter(campaign_id=data["campaign_id"])

        # Time filters
        if not data.get("include_past", False):
            queryset = queryset.filter(start_datetime__gte=timezone.now())

        if data.get("start_after"):
            queryset = queryset.filter(start_datetime__gte=data["start_after"])

        if data.get("start_before"):
            queryset = queryset.filter(start_datetime__lte=data["start_before"])

        # Order by start_datetime
        queryset = queryset.order_by("start_datetime").select_related("campaign")

        # Serialize as GeoJSON
        serializer = CalendarEventGeoSerializer(queryset, many=True)
        return Response(serializer.data)
