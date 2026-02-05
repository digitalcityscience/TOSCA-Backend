from django import forms
from django.contrib import admin
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q

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
        "source_object",
        "target_object",
        "created_at",
    ]
    list_filter = ["campaign", "link_type", "created_at"]
    search_fields = ["id"]
    readonly_fields = ["created_at", "updated_at"]
    autocomplete_fields = ["campaign", "created_by"]
    
    fieldsets = (
        (None, {
            "fields": ("campaign", "link_type", "created_by")
        }),
        ("Source", {
            "description": "Select the entity type and enter its UUID.",
            "fields": ("source_content_type", "source_object_id")
        }),
        ("Target", {
            "description": "Select the entity type and enter its UUID.",
            "fields": ("target_content_type", "target_object_id")
        }),
        ("Metadata", {
            "fields": ("created_at", "updated_at")
        }),
    )
