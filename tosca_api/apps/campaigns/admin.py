from django.contrib import admin, messages

from .models import Campaign


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    """Admin interface for Campaign model."""

    list_display = ("title", "status", "visibility", "created_by", "created_at")
    list_filter = ("status", "visibility", "created_at")
    search_fields = ("title", "summary")
    readonly_fields = ("id", "created_at", "updated_at")
    ordering = ("-created_at",)

    fieldsets = (
        (None, {"fields": ("id", "title", "summary")}),
        ("Status", {"fields": ("status", "visibility")}),
        ("Ownership", {"fields": ("created_by",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

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
