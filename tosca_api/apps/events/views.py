from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.mixins import CreateModelMixin, RetrieveModelMixin, UpdateModelMixin
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response

from .filters import apply_event_filters
from .models import Event, EventSeries
from .serializers import (
    BBoxSerializer,
    EventWriteSerializer,
    EventDetailSerializer,
    EventGeoSerializer,
    EventListSerializer,
    EventMapOnlineSerializer,
    EventSeriesOccurrenceSerializer,
    EventSeriesResponseSerializer,
    EventSeriesWriteSerializer,
    GeometryFilterSerializer,
)
from .services import serialize_occurrence_specs
from .services import get_base_template_event


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
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]
    pagination_class = EventCursorPagination

    def get_permissions(self):
        if self.action == "within":
            return [permissions.AllowAny()]
        return super().get_permissions()

    def _is_admin_reader(self) -> bool:
        user = self.request.user
        return bool(user and user.is_authenticated and user.is_staff)

    def _apply_visibility_scope(self, queryset):
        if self._is_admin_reader():
            return queryset
        return queryset.filter(
            status=Event.Status.PUBLISHED,
            visibility=Event.Visibility.PUBLIC,
        )

    def _coerce_public_filters(self, filters: dict) -> dict:
        if not self._is_admin_reader():
            filters["status"] = Event.Status.PUBLISHED
            filters["visibility"] = Event.Visibility.PUBLIC
        return filters

    def get_serializer_class(self):
        """Return appropriate serializer based on action and request."""
        if self.action == "list":
            # Check if spatial filter is applied
            if self._is_spatial_request():
                return EventGeoSerializer
            return EventListSerializer
        if self.action == "list_v2":
            return EventListSerializer
        if self.action == "map_v2":
            return EventMapOnlineSerializer
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
        queryset = self._apply_visibility_scope(super().get_queryset())

        if self.action in ("list", "list_v2"):
            bbox_serializer = BBoxSerializer(data=self.request.query_params)
            bbox_serializer.is_valid(raise_exception=True)
            validated_filters = dict(bbox_serializer.validated_data)
            validated_filters["spatial_geometry"] = validated_filters.pop("bbox", None)
            validated_filters = self._coerce_public_filters(validated_filters)

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
            queryset = queryset.prefetch_related(
                "eventlayer_set__layer__workspace"
            )
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

    @action(detail=False, methods=["get"], url_path="list")
    def list_v2(self, request):
        """
        Return a paginated chronological mixed stream of events.

        This endpoint keeps list responses in JSON form even when spatial
        filters are supplied, unlike the legacy `/events/` route which still
        switches to GeoJSON for bbox requests.
        """
        queryset = self.filter_queryset(self.get_queryset()).order_by("start_datetime")
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="map")
    def map_v2(self, request):
        """
        Return the dedicated map response with separate spatial and online buckets.
        """
        bbox_serializer = BBoxSerializer(data=request.query_params)
        bbox_serializer.is_valid(raise_exception=True)
        filters = dict(bbox_serializer.validated_data)
        filters["spatial_geometry"] = filters.pop("bbox", None)
        filters = self._coerce_public_filters(filters)

        base_queryset = self._apply_visibility_scope(
            Event.objects.all().select_related("campaign", "event_type", "series")
        )
        queryset = apply_event_filters(base_queryset, filters=filters).order_by(
            "start_datetime"
        )

        spatial_queryset = queryset.filter(
            location_mode__in=[Event.LocationMode.PHYSICAL, Event.LocationMode.HYBRID],
            location__isnull=False,
        )
        online_queryset = queryset.filter(
            location_mode__in=[
                Event.LocationMode.ONLINE,
                Event.LocationMode.BY_ARRANGEMENT,
                Event.LocationMode.HOME_VISIT,
            ],
        )

        spatial_events = EventGeoSerializer(spatial_queryset, many=True).data
        online_events = EventMapOnlineSerializer(online_queryset, many=True).data
        return Response(
            {
                "spatial_events": spatial_events,
                "online_events": online_events,
            }
        )

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
        data = self._coerce_public_filters(data)

        base_queryset = self._apply_visibility_scope(Event.objects.all())
        queryset = apply_event_filters(base_queryset, filters=data)
        queryset = queryset.order_by("start_datetime").select_related(
            "campaign",
            "event_type",
            "series",
        )

        # Serialize as GeoJSON
        serializer = EventGeoSerializer(queryset, many=True)
        return Response(serializer.data)


class EventSeriesViewSet(
    CreateModelMixin,
    UpdateModelMixin,
    RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Preview, create, and update event series with generated occurrences."""

    queryset = EventSeries.objects.all().select_related(
        "campaign",
        "event_type",
        "default_context",
        "created_by",
    )
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if user and user.is_authenticated and user.is_staff:
            return queryset
        return queryset.filter(
            events__status=Event.Status.PUBLISHED,
            events__visibility=Event.Visibility.PUBLIC,
        ).distinct()

    def get_serializer_class(self):
        if self.action == "retrieve":
            return EventSeriesResponseSerializer
        return EventSeriesWriteSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        series = serializer.save()
        response_serializer = EventSeriesResponseSerializer(series)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, *args, **kwargs):
        series = self.get_object()
        if get_base_template_event(series) is None:
            return Response(
                {
                    "detail": (
                        "This series has no usable base occurrence/template for taxonomy hydration."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        response_serializer = EventSeriesResponseSerializer(series)
        return Response(response_serializer.data)

    def partial_update(self, request, *args, **kwargs):
        series = self.get_object()
        serializer = self.get_serializer(series, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        series = serializer.save()
        response_serializer = EventSeriesResponseSerializer(series)
        return Response(response_serializer.data)

    @action(detail=False, methods=["post"], url_path="preview")
    def preview(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        occurrences = serialize_occurrence_specs(serializer.validated_data["_occurrences"])
        response_serializer = EventSeriesOccurrenceSerializer(occurrences, many=True)
        return Response({"occurrences": response_serializer.data})
