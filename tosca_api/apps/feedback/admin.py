from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

from tosca_api.apps.organizations.permissions import OrgScopedAdminMixin, get_request_org_context

from .forms import FeedbackLayerFormSet
from .models import FeedbackLayer, FeedbackSubmission, GeoFeedback


class FeedbackLayerInline(admin.TabularInline):
    model = FeedbackLayer
    formset = FeedbackLayerFormSet
    extra = 1
    autocomplete_fields = ["layer"]

    class Media:
        js = ("feedback/js/admin_feedback.js",)


class FeedbackSubmissionInline(admin.TabularInline):
    model = FeedbackSubmission
    extra = 0
    readonly_fields = ["id", "submitted_by", "rating", "form_data", "created_at"]
    fields = ["id", "submitted_by", "rating", "form_data", "is_anonymized", "created_at"]
    show_change_link = True


@admin.register(GeoFeedback)
class GeoFeedbackAdmin(admin.ModelAdmin):
    """Admin interface for GeoFeedback.

    KNOWN GAP -- GeoFeedback is an incomplete module in the org-authorization
    rollout, tracked separately (security tickets ticket 11, open question
    A8: "GeoFeedback scope decision -- currently out of scope"). Its DRF API
    (`feedback/views.py`) still gates writes with a local `IsAdminOrReadOnly`
    permission, not `CampaignScopedPermission`/`has_perm()` like Event/
    GeoStory. This admin was likewise never migrated onto
    `OrgScopedAdminMixin` the way Campaign/GeoStory/Event/Workspace were.

    Regression closed here (2026-08-19): registering `OrgRolePermissionBackend`
    (ticket 06) made Django's default `ModelAdmin.has_*_permission` -- which
    reads `request.user.has_perm()` model-globally, with no row filter --
    start granting real capability for `geofeedback` (it's in
    `TOSCA_PERMISSION_MODELS`) for the first time; previously `has_perm()`
    was always `False`, so this admin was superuser-only in practice. Without
    a queryset scope, that would let any staff user holding a WRITER+ role in
    *any* org entitled to `feedback` see and edit *every* organization's
    GeoFeedback rows, not just their own. `get_queryset` below closes that
    specific leak (gate C) with the same "queryset is the real tenant gate"
    mechanism used everywhere else in this app -- it deliberately does
    **not** adopt the rest of `OrgScopedAdminMixin` (add-time org resolution,
    the has_add/change/delete_permission ladder); GeoFeedback's broader
    org-authorization integration belongs to the ticket-11 decision, not a
    piecemeal fix here.
    """

    list_display = [
        "title",
        "campaign",
        "status",
        "visibility",
        "rating_enabled",
        "form_enabled",
        "allow_drawings",
        "created_by",
        "created_at",
    ]
    list_filter = ["campaign", "status", "visibility", "rating_enabled", "form_enabled"]
    search_fields = ["title", "description"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["campaign", "created_by", "context", "custom_form"]
    inlines = [FeedbackLayerInline, FeedbackSubmissionInline]

    fieldsets = (
        (None, {"fields": ("id", "campaign", "title", "description")}),
        ("Content", {"fields": ("context",)}),
        (
            "Feedback Configuration",
            {
                "fields": (
                    "custom_form",
                    "rating_enabled",
                    "form_enabled",
                    "allow_drawings",
                ),
            },
        ),
        ("Settings", {"fields": ("status", "visibility", "created_by")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related(
            "campaign", "created_by", "custom_form"
        )
        if request.user.is_superuser:
            return qs
        _roles, org_slug, exempt = get_request_org_context(request)
        if exempt:
            return qs
        if not org_slug:
            return qs.none()
        return qs.filter(campaign__organization__slug=org_slug)


@admin.register(FeedbackSubmission)
class FeedbackSubmissionAdmin(OrgScopedAdminMixin, GISModelAdmin):
    """Admin interface for FeedbackSubmission with map widget for geometry."""

    org_lookup = "feedback__campaign__organization__slug"

    list_display = [
        "id",
        "feedback",
        "submitted_by",
        "rating",
        "is_anonymized",
        "created_at",
    ]
    list_filter = ["feedback", "is_anonymized", "rating"]
    search_fields = ["feedback__title"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["feedback", "submitted_by"]

    fieldsets = (
        (None, {"fields": ("id", "feedback", "submitted_by")}),
        ("Response", {"fields": ("rating", "form_data")}),
        ("Spatial Data", {"fields": ("geometry",)}),
        ("Settings", {"fields": ("is_anonymized",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            "feedback", "submitted_by"
        )
