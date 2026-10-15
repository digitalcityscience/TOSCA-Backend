from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from tosca_api.apps.footer.forms import (
    FooterDocumentForm,
    FooterDocumentInlineFormSet,
    FooterLogoInlineFormSet,
)
from tosca_api.apps.footer.models import Footer, FooterDocument, FooterLogo
from tosca_api.apps.footer.services import footer_overflow_warnings, publish_footer


class FooterLogoInline(admin.TabularInline):
    model = FooterLogo
    formset = FooterLogoInlineFormSet
    extra = 0
    fields = (
        "image",
        "logo_preview",
        "alt_text",
        "destination_url",
        "display_order",
        "is_active",
    )
    readonly_fields = ("logo_preview",)

    @admin.display(description="Preview")
    def logo_preview(self, obj):
        if not obj or not obj.image:
            return "—"
        return format_html(
            '<img src="{}" alt="{}" class="footer-logo-preview" />',
            obj.image.url,
            obj.alt_text,
        )


class FooterDocumentInline(admin.StackedInline):
    model = FooterDocument
    form = FooterDocumentForm
    formset = FooterDocumentInlineFormSet
    extra = 0
    fields = ("title", "slug", "content", "display_order", "is_active")


@admin.register(Footer)
class FooterAdmin(admin.ModelAdmin):
    change_form_template = "admin/footer/footer/change_form.html"
    list_display = (
        "name",
        "publication_state",
        "active_logo_count",
        "active_document_count",
        "updated_at",
        "publish_control",
    )
    search_fields = ("name",)
    ordering = ("-updated_at",)
    inlines = (FooterLogoInline, FooterDocumentInline)
    readonly_fields = (
        "is_published",
        "publication_version",
        "published_at",
        "published_by",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (None, {"fields": ("name",)}),
        (
            "Publication",
            {
                "fields": (
                    "is_published",
                    "publication_version",
                    "published_at",
                    "published_by",
                ),
                "description": "Save changes here, then publish from the footer list.",
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    class Media:
        css = {"all": ("footer/admin/footer-editor.css",)}
        js = ("footer/admin/footer-ordering.js",)

    def get_urls(self):
        return [
            path(
                "<uuid:object_id>/publish/",
                self.admin_site.admin_view(self.publish_view),
                name="footer_footer_publish",
            )
        ] + super().get_urls()

    @admin.display(description="State", ordering="is_published")
    def publication_state(self, obj):
        if obj.is_published:
            return format_html('<span class="footer-state footer-state--live">Published</span>')
        return format_html('<span class="footer-state">Draft</span>')

    @admin.display(description="Logos")
    def active_logo_count(self, obj):
        return obj.logos.filter(is_active=True).count()

    @admin.display(description="Documents")
    def active_document_count(self, obj):
        return obj.documents.filter(is_active=True).count()

    @admin.display(description="Publish")
    def publish_control(self, obj):
        label = "Republish" if obj.is_published else "Publish"
        return format_html(
            '<a class="button footer-publish-button" href="{}">{}</a>',
            reverse("admin:footer_footer_publish", args=[obj.pk]),
            label,
        )

    def publish_view(self, request, object_id):
        footer = get_object_or_404(Footer, pk=object_id)
        if not self.has_change_permission(request, footer):
            raise PermissionDenied

        if request.method == "POST":
            try:
                publish_footer(footer.pk, user=request.user)
            except ValidationError as exc:
                self.message_user(request, "; ".join(exc.messages), level=messages.ERROR)
                return redirect(reverse("admin:footer_footer_change", args=[footer.pk]))
            self.message_user(
                request,
                f"Footer “{footer.name}” is now published.",
                level=messages.SUCCESS,
            )
            return redirect(reverse("admin:footer_footer_changelist"))

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": footer,
            "title": f"Publish footer “{footer.name}”?",
            "active_documents": footer.documents.filter(is_active=True),
            "overflow_warnings": footer_overflow_warnings(footer),
            "replaces": Footer.objects.filter(is_published=True).exclude(pk=footer.pk).first(),
        }
        return TemplateResponse(request, "admin/footer/footer/publish_confirmation.html", context)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        for warning in footer_overflow_warnings(form.instance):
            self.message_user(request, warning, level=messages.WARNING)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_published:
            return False
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions
