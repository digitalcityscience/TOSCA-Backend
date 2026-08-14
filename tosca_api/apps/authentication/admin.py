import logging

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path

from tosca_api.apps.authentication.models import KeycloakRole

logger = logging.getLogger(__name__)


@admin.register(KeycloakRole)
class KeycloakRoleAdmin(admin.ModelAdmin):
    change_list_template = "admin/tosca_authentication/keycloakrole/change_list.html"

    list_display = (
        "name",
        "organization",
        "project",
        "level",
        "source",
        "is_active",
        "last_seen_at",
    )
    search_fields = ("name", "project")
    list_filter = ("source", "is_active", "level", "organization")

    # KeycloakRole is a read-only mirror of Keycloak (populated by login upsert +
    # the "Sync with Keycloak" button). It must be *visible* but never editable by
    # hand: every value is derived from the Keycloak role name, and is_active is
    # driven by the sync (deactivated when a role disappears). So we allow view
    # only -- no add, no change, no delete. Rows open in Django's read-only detail
    # view. (The sync button is a custom admin view, unaffected by these.)
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "sync-with-keycloak/",
                self.admin_site.admin_view(self.sync_with_keycloak),
                name="tosca_authentication_keycloakrole_sync_with_keycloak",
            ),
        ]
        return custom + urls

    def sync_with_keycloak(self, request):
        """One-button, two-hop sync (Epic 11 Phase 2, operator-triggered).

        Hop 1: pull the full realm role list from the Keycloak Admin API and
        reconcile the catalog (upsert conforming roles, deactivate vanished ones).
        Hop 2: mirror the catalog's reader/writer roles into every active
        GeoServer role service (create active, delete deactivated). Hop-2
        failures are surfaced as warnings but never abort the request.
        """
        from tosca_api.apps.authentication.keycloak_admin import (
            KeycloakAdminError,
            list_realm_roles,
        )
        from tosca_api.apps.authentication.role_registry import sync_realm_roles
        from tosca_api.apps.geodata_providers.role_sync import reconcile_all_engines

        redirect_url = "admin:tosca_authentication_keycloakrole_changelist"

        # Hop 1 -- Keycloak -> catalog.
        try:
            role_names = list_realm_roles()
        except KeycloakAdminError as exc:
            self.message_user(request, f"Keycloak sync failed: {exc}", level=messages.ERROR)
            return redirect(redirect_url)

        catalog = sync_realm_roles(role_names)
        self.message_user(
            request,
            f"Keycloak → catalog: created {catalog['created']}, updated "
            f"{catalog['updated']}, deactivated {catalog['deactivated']} "
            f"(skipped {catalog['skipped']} non-conforming).",
            level=messages.SUCCESS,
        )

        # Hop 2 -- catalog reader/writer -> GeoServer role service(s).
        results = reconcile_all_engines()
        if not results:
            self.message_user(
                request, "No active GeoServer engines to sync roles to.", level=messages.WARNING
            )
            return redirect(redirect_url)

        for engine, summary, error in results:
            if error:
                self.message_user(
                    request,
                    f"GeoServer '{engine.name}': role sync failed — {error}",
                    level=messages.WARNING,
                )
                continue
            level = messages.WARNING if summary["failed"] else messages.SUCCESS
            detail = (
                f"GeoServer '{engine.name}': created {len(summary['created'])}, "
                f"deleted {len(summary['deleted'])} reader/writer role(s)"
            )
            if summary["failed"]:
                detail += f"; {len(summary['failed'])} failed"
            self.message_user(request, detail, level=level)

        return redirect(redirect_url)
