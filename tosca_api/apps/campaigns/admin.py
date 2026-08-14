from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from tosca_api.apps.organizations.permissions import OrgScopedAdminMixin, resolve_write_organization

from .models import Campaign


@admin.register(Campaign)
class CampaignAdmin(OrgScopedAdminMixin, admin.ModelAdmin):
    """Admin interface for Campaign model."""

    list_display = ("title", "organization", "status", "visibility", "created_by", "created_at")
    list_filter = ("organization", "status", "visibility", "created_at")
    search_fields = ("title", "summary")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)

    fieldsets = (
        (None, {"fields": ("id", "organization", "title", "summary")}),
        ("Status", {"fields": ("status", "visibility")}),
        ("Ownership", {"fields": ("created_by",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            # Org-scoped staff can only ever create/see rows in their own
            # org (see get_queryset); the field is derived, not chosen.
            readonly.append("organization")
        return readonly

    def save_model(self, request, obj, form, change):
        if not change and not obj.organization_id:
            organization = resolve_write_organization(request)
            if organization is None:
                raise ValidationError("Could not determine an organization for this campaign.")
            obj.organization = organization
        super().save_model(request, obj, form, change)

    # ------------------------------------------------------------------
    # Delete safety: warn before the Event/EventSeries/GeoStory/GeoFeedback
    # cascade. Matches the LayerAdmin.usage_summary() warning pattern —
    # Django's default delete confirmation page already lists every
    # cascaded object individually (which can be a very long raw list for
    # an active campaign), this adds a concise count-based warning banner
    # on top of that so the scope is obvious at a glance.
    # ------------------------------------------------------------------
    def delete_view(self, request, object_id, extra_context=None):
        try:
            campaign = self.get_object(request, object_id)
        except Exception:
            campaign = None

        if campaign is not None:
            usage = campaign.usage_summary()
            if any(usage.values()):
                self.message_user(
                    request,
                    (
                        f"Campaign '{campaign.title}' has "
                        f"{usage['events']} events, "
                        f"{usage['event_series']} event series, "
                        f"{usage['geostories']} geostories, and "
                        f"{usage['feedbacks']} feedbacks. "
                        "Confirming will permanently delete all of them — "
                        "there is no soft-delete or recovery path."
                    ),
                    messages.WARNING,
                )
            extra_context = {**(extra_context or {}), "campaign_usage_summary": usage}

        return super().delete_view(request, object_id, extra_context=extra_context)
