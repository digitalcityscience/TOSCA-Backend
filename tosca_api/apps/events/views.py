from django.db.models import Count, Prefetch, Q

from rest_framework import permissions, status, views, viewsets
from rest_framework.decorators import action
from rest_framework.mixins import CreateModelMixin, RetrieveModelMixin, UpdateModelMixin
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response

from tosca_api.apps.organizations.permissions import CampaignScopedPermission

from .filters import apply_event_filters
from .models import Event, EventSeries, EventTerm, EventType, TaxonomyDimension, TaxonomyTerm
from .serializers import (
    BBoxSerializer,
    EventTaxonomyDimensionRegistrySerializer,
    EventTypeRegistrySerializer,
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


class EventTypeRegistryView(views.APIView):
    """Public read-only event-type registry."""

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        event_types = EventType.objects.filter(is_active=True).order_by("label")
        serializer = EventTypeRegistrySerializer(event_types, many=True)
        return Response(serializer.data)


class EventTaxonomyRegistryView(views.APIView):
    """Public read-only taxonomy registry for user-facing event filters."""

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        profile_key = (request.query_params.get("profile_key") or "").strip()
        profile_filter = Q(profile_key="")
        if profile_key:
            profile_filter |= Q(profile_key=profile_key)

        dimensions = (
            TaxonomyDimension.objects.filter(is_active=True)
            .filter(profile_filter)
            .prefetch_related(
                Prefetch(
                    "terms",
                    queryset=TaxonomyTerm.objects.filter(is_active=True).order_by(
                        "parent_id",
                        "sort_order",
                        "label",
                    ),
                )
            )
            .order_by("sort_order", "label")
        )
        serializer = EventTaxonomyDimensionRegistrySerializer(dimensions, many=True)
        return Response({"profile_key": profile_key, "dimensions": serializer.data})


class EventViewSet(viewsets.ModelViewSet):
    """
    API endpoint for Event operations.

    ## Read Endpoints

    ### Calendar / chronological list
    ```
    GET /api/v1/events/
    GET /api/v1/events/?campaign_id=<uuid>&start_after=<...>&start_before=<...>
    ```
    Always paginated JSON ordered by start_datetime. Accepts the shared
    filter contract documented in BBoxSerializer (campaign_id, event_type_id,
    dimension_id, term_id, include_past, start_after, start_before, status,
    visibility). The `bbox` parameter is ignored here; use `/events/map/`
    for the spatial response.

    ### Map response
    ```
    GET /api/v1/events/map/?bbox=min_lon,min_lat,max_lon,max_lat
    ```
    Returns `{spatial_events: FeatureCollection, online_events: [...]}`.
    Spatial bucket contains mapped physical/hybrid events; online bucket
    contains online, by_arrangement, and home_visit events.

    ### Polygon filter
    ```
    POST /api/v1/events/within/
    { "geometry": {GeoJSON Polygon/MultiPolygon}, ... }
    ```
    Returns events whose geometry is inside the supplied polygon as a
    GeoJSON FeatureCollection.
    """

    queryset = Event.objects.all()
    permission_classes = [permissions.DjangoModelPermissionsOrAnonReadOnly, CampaignScopedPermission]
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
        return queryset.published_public()

    def _coerce_public_filters(self, filters: dict) -> dict:
        if not self._is_admin_reader():
            filters["status"] = Event.Status.PUBLISHED
            filters["visibility"] = Event.Visibility.PUBLIC
        return filters

    @staticmethod
    def _with_taxonomy_prefetch(queryset):
        return queryset.prefetch_related(
            Prefetch(
                "event_terms",
                queryset=EventTerm.objects.select_related("term__dimension"),
            )
        )

    def get_serializer_class(self):
        if self.action == "list":
            return EventListSerializer
        if self.action == "map_v2":
            return EventMapOnlineSerializer
        if self.action == "retrieve":
            return EventDetailSerializer
        if self.action == "within":
            return EventGeoSerializer
        return EventWriteSerializer

    def get_queryset(self):
        queryset = self._apply_visibility_scope(super().get_queryset())

        if self.action == "list":
            bbox_serializer = BBoxSerializer(data=self.request.query_params)
            bbox_serializer.is_valid(raise_exception=True)
            validated_filters = dict(bbox_serializer.validated_data)
            # bbox is accepted on /events/map/ only; ignore it here so list
            # results never silently change shape based on query params.
            validated_filters.pop("bbox", None)
            validated_filters["spatial_geometry"] = None
            validated_filters = self._coerce_public_filters(validated_filters)

            queryset = apply_event_filters(queryset, filters=validated_filters)

        if self.action == "retrieve":
            queryset = queryset.select_related(
                "context",
                "campaign",
                "event_type",
                "series",
                "organizer",
            )
            queryset = queryset.prefetch_related(
                "eventlayer_set__layer__workspace",
                "feature_links_source__target_content_type",
                Prefetch(
                    "series__events",
                    queryset=Event.objects.only(
                        "id",
                        "series_id",
                        "occurrence_index",
                        "start_datetime",
                    ).order_by("occurrence_index", "start_datetime"),
                ),
            )
        else:
            queryset = queryset.select_related("campaign", "event_type", "series")
            queryset = queryset.annotate(series_total_occurrences=Count("series__events"))
            queryset = self._with_taxonomy_prefetch(queryset)

        return queryset

    def perform_create(self, serializer):
        """Set the organizer to the current user."""
        serializer.save(organizer=self.request.user)

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
            Event.objects.all()
            .select_related("campaign", "event_type", "series")
            .annotate(series_total_occurrences=Count("series__events"))
        )
        base_queryset = self._with_taxonomy_prefetch(base_queryset)
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

        base_queryset = self._apply_visibility_scope(
            Event.objects.all().annotate(series_total_occurrences=Count("series__events"))
        )
        base_queryset = self._with_taxonomy_prefetch(base_queryset)
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
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, CampaignScopedPermission]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if user and user.is_authenticated and user.is_staff:
            return queryset
        return queryset.filter(events__in=Event.objects.published_public()).distinct()

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
