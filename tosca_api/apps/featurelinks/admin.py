from django import forms
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils.html import format_html

from .models import ALLOWED_LINK_MODELS, FeatureLink


class FeatureLinkForm(forms.ModelForm):
    class Meta:
        model = FeatureLink
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Build query for allowed content types
        # ALLOWED_LINK_MODELS format: "app_label.model"
        q_filter = Q()
        for item in ALLOWED_LINK_MODELS:
            app_label, model_name = item.split('.')
            q_filter |= Q(app_label=app_label, model=model_name)
        
        allowed_cts = ContentType.objects.filter(q_filter)
        
        if 'source_content_type' in self.fields:
            self.fields['source_content_type'].queryset = allowed_cts
        if 'target_content_type' in self.fields:
            self.fields['target_content_type'].queryset = allowed_cts


@admin.register(FeatureLink)
class FeatureLinkAdmin(admin.ModelAdmin):
    form = FeatureLinkForm
    list_display = [
        "id",
        "campaign",
        "link_type",
        "get_source_display",
        "get_target_display",
        "created_at",
    ]
    list_filter = ["campaign", "link_type", "created_at"]
    search_fields = ["id"]
    readonly_fields = ["created_at", "updated_at", "get_source_display", "get_target_display"]
    autocomplete_fields = ["campaign", "created_by"]
    
    fieldsets = (
        (None, {
            "fields": ("campaign", "link_type", "created_by")
        }),
        ("Source", {
            "description": "Select the entity type and enter its UUID.",
            "fields": ("source_content_type", "source_object_id", "get_source_display")
        }),
        ("Target", {
            "description": "Select the entity type and enter its UUID.",
            "fields": ("target_content_type", "target_object_id", "get_target_display")
        }),
        ("Metadata", {
            "fields": ("created_at", "updated_at")
        }),
    )

    def get_queryset(self, request):
        """Optimize queryset to prefetch content types."""
        return super().get_queryset(request).select_related(
            "campaign",
            "source_content_type",
            "target_content_type",
            "created_by",
        )

    @admin.display(description="Source Object")
    def get_source_display(self, obj):
        """Safely display the source object."""
        if not obj.source_content_type_id or not obj.source_object_id:
            return "-"
        try:
            source = obj.source_object
            if source:
                return format_html(
                    '<span title="{}">{}</span>',
                    f"{obj.source_content_type.app_label}.{obj.source_content_type.model}",
                    str(source)
                )
            return format_html('<span style="color: red;">Object not found</span>')
        except Exception:
            return format_html('<span style="color: red;">Error loading</span>')

    @admin.display(description="Target Object")
    def get_target_display(self, obj):
        """Safely display the target object."""
        if not obj.target_content_type_id or not obj.target_object_id:
            return "-"
        try:
            target = obj.target_object
            if target:
                return format_html(
                    '<span title="{}">{}</span>',
                    f"{obj.target_content_type.app_label}.{obj.target_content_type.model}",
                    str(target)
                )
            return format_html('<span style="color: red;">Object not found</span>')
        except Exception:
            return format_html('<span style="color: red;">Error loading</span>')

