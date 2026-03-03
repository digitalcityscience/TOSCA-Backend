from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response

from .models import FeedbackSubmission, GeoFeedback
from .serializers import (
    FeedbackSubmissionSerializer,
    GeoFeedbackCreateUpdateSerializer,
    GeoFeedbackDetailSerializer,
    GeoFeedbackListSerializer,
)


class FeedbackCursorPagination(CursorPagination):
    """Cursor pagination for feedback, ordered by created_at descending."""

    page_size = 20
    ordering = "-created_at"


class IsAdminOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow admins to edit/create it.
    Read-only permissions are allowed for any request.
    """

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)


class GeoFeedbackViewSet(viewsets.ModelViewSet):
    """
    API endpoint for GeoFeedback operations.

    - GET /api/v1/feedback/ : List all published feedback campaigns
    - GET /api/v1/feedback/{id}/ : Get details
    - POST /api/v1/feedback/ : Create (Admin only)
    - PATCH/PUT /api/v1/feedback/{id}/ : Update (Admin only)
    - DELETE /api/v1/feedback/{id}/ : Delete (Admin only)
    
    Submissions:
    - POST /api/v1/feedback/{id}/submit/ : Submit citizen feedback
    """

    queryset = GeoFeedback.objects.all()
    # Admins can do anything, others can only read.
    # Note: submission has its own permission logic in the submit action.
    permission_classes = [IsAdminOrReadOnly]
    pagination_class = FeedbackCursorPagination

    def get_serializer_class(self):
        if self.action == "list":
            return GeoFeedbackListSerializer
        if self.action == "retrieve":
            return GeoFeedbackDetailSerializer
        if self.action == "submit":
            return FeedbackSubmissionSerializer
        return GeoFeedbackCreateUpdateSerializer

    def get_queryset(self):
        """
        Filter queryset:
        - Regular users only see PUBLISHED and PUBLIC feedbacks.
        - Staff see everything.
        - Filter by campaign_id query param if provided.
        """
        qs = super().get_queryset()

        if self.action == "retrieve":
            qs = qs.select_related("context", "campaign", "created_by", "custom_form")
            qs = qs.prefetch_related("feedbacklayer_set__layer")

        user = self.request.user
        if not (user and user.is_staff):
            qs = qs.filter(
                status=GeoFeedback.Status.PUBLISHED,
                visibility=GeoFeedback.Visibility.PUBLIC,
            )

        campaign_id = self.request.query_params.get("campaign_id")
        if campaign_id:
            qs = qs.filter(campaign_id=campaign_id)

        return qs

    def perform_create(self, serializer):
        """Set the creator to the current user."""
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"], permission_classes=[permissions.AllowAny])
    def submit(self, request, pk=None):
        """
        Submit feedback to this campaign.
        Anonymous users are allowed.

        JSON body:
        {
            "rating": 5, (optional/required based on config)
            "form_data": {...}, (optional/required based on config)
            "geometry": {... GeoJSON ...} (optional/allowed based on config)
        }
        """
        feedback = self.get_object()

        # Parse request data
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        rating = data.get("rating")
        form_data = data.get("form_data")
        geometry = data.get("geometry")

        # Config Validation
        errors = {}
        if feedback.rating_enabled and rating is None:
            errors["rating"] = "Rating is required for this feedback campaign."
        
        if feedback.form_enabled and not form_data:
            errors["form_data"] = "Form data is required for this feedback campaign."

        if geometry and not feedback.allow_drawings:
            errors["geometry"] = "Drawings are not allowed for this feedback campaign."

        if errors:
            raise ValidationError(errors)

        # Create submission
        user = request.user if request.user.is_authenticated else None
        
        try:
            submission = FeedbackSubmission(
                feedback=feedback,
                submitted_by=user,
                rating=rating,
                form_data=form_data,
                geometry=geometry,
                # Simple logic for anonymization parameter if provided, otherwise default False
                is_anonymized=request.data.get("is_anonymized", False)
            )
            submission.full_clean()  # Model-level bounds checking
            submission.save()
            return Response(
                FeedbackSubmissionSerializer(submission).data,
                status=status.HTTP_201_CREATED,
            )
        except DjangoValidationError as e:
            # Catch model-level ValidationError (e.g., rating out of bounds)
            raise ValidationError(e.message_dict)
