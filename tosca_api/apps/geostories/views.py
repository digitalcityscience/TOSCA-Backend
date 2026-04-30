from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import permissions, viewsets
from rest_framework.pagination import CursorPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser

from .models import GeoStory
from .serializers import (
    GeoStoryDetailSerializer,
    GeoStoryListSerializer,
    GeoStoryWriteSerializer,
)


class GeoStoryCursorPagination(CursorPagination):
    """Cursor pagination for GeoStory list."""

    page_size = 20
    ordering = "-created_at"


@extend_schema_view(
    list=extend_schema(
        tags=["geostories"],
        summary="List GeoStories",
        responses={200: GeoStoryListSerializer},
        examples=[
            OpenApiExample(
                "GeoStory card",
                value={
                    "id": "4a7b90a6-31bd-4b84-9bc0-9a94d61b4d15",
                    "title": "Waterfront adaptation",
                    "summary": "A story about planned public-space changes.",
                    "hero_image_url": "https://example.test/media/geostories/story/hero.jpg",
                    "hero_image_alt": "A waterfront promenade with new tree planting.",
                    "campaign": "b0c2d2d5-cd1d-49db-8f4d-a56e37798e80",
                    "created_at": "2026-04-30T10:30:00Z",
                },
                response_only=True,
            )
        ],
    ),
    retrieve=extend_schema(
        tags=["geostories"],
        summary="Retrieve a GeoStory",
        responses={200: GeoStoryDetailSerializer},
    ),
    create=extend_schema(
        tags=["geostories"],
        summary="Create a GeoStory",
        request=GeoStoryWriteSerializer,
        responses={201: OpenApiResponse(response=GeoStoryWriteSerializer)},
    ),
    update=extend_schema(
        tags=["geostories"],
        summary="Replace a GeoStory",
        request=GeoStoryWriteSerializer,
        responses={200: OpenApiResponse(response=GeoStoryWriteSerializer)},
    ),
    partial_update=extend_schema(
        tags=["geostories"],
        summary="Update a GeoStory",
        request=GeoStoryWriteSerializer,
        responses={200: OpenApiResponse(response=GeoStoryWriteSerializer)},
    ),
)
class GeoStoryViewSet(viewsets.ModelViewSet):
    """
    API endpoint for GeoStory operations.

    - **List**: Returns published stories with slim payload.
    - **Retrieve**: Returns full story with nested context, layers, links.
    - **Create/Update/Delete**: Requires authentication.

    Supports filtering by `campaign_id` query parameter.
    """

    queryset = GeoStory.objects.all()
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = GeoStoryCursorPagination
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_serializer_class(self):
        """
        Return appropriate serializer based on action.
        - list: GeoStoryListSerializer (slim)
        - retrieve: GeoStoryDetailSerializer (full nested)
        - create/update/delete: GeoStorySerializer (write capable)
        """
        if self.action == "list":
            return GeoStoryListSerializer
        if self.action == "retrieve":
            return GeoStoryDetailSerializer
        return GeoStoryWriteSerializer

    def get_queryset(self):
        """
        Filter queryset based on action and parameters.

        - List view: Only published stories (unless user is staff).
        - Filter by campaign_id if provided.
        - Optimize queries with select_related/prefetch_related.
        """
        queryset = super().get_queryset()

        # Apply status filter for list action (public consumption)
        if self.action == "list":
            # Staff users can see all, public users see only published
            if not self.request.user.is_staff:
                queryset = queryset.filter(status=GeoStory.Status.PUBLISHED)

        # Filter by campaign_id if provided
        campaign_id = self.request.query_params.get("campaign_id")
        if campaign_id:
            queryset = queryset.filter(campaign_id=campaign_id)

        # Optimize queries for detail view
        if self.action == "retrieve":
            queryset = queryset.select_related("context", "campaign", "author")
            queryset = queryset.prefetch_related(
                "geostorylayer_set__layer__workspace"
            )

        # Optimize queries for list view
        if self.action == "list":
            queryset = queryset.select_related("campaign")

        return queryset

    def perform_create(self, serializer):
        """Set the author to the current user."""
        serializer.save(author=self.request.user)
