"""
Authoritative Keycloak role sync (Epic 11 Phase 1, canonical §4 step 4).

Pulls the *full* realm role list from the Keycloak Admin API and reconciles it
into the ``KeycloakRole`` catalog: conforming roles that resolve to a known
Organization are upserted; roles that vanished from Keycloak are soft-deactivated
(never hard-deleted). Unlike the login-triggered upsert, this catches conforming
org/project roles nobody has logged in with yet (e.g. ``ROLE_GQ2_*``).

    python manage.py sync_keycloak_roles [--dry-run] [--no-deactivate]
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from tosca_api.apps.authentication.keycloak_admin import (
    KeycloakAdminError,
    list_realm_roles,
)
from tosca_api.apps.authentication.role_registry import sync_realm_roles


class Command(BaseCommand):
    help = "Sync the KeycloakRole catalog from the Keycloak Admin API realm roles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing to the catalog.",
        )
        parser.add_argument(
            "--no-deactivate",
            action="store_true",
            help="Do not deactivate catalog roles absent from Keycloak.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        deactivate = not options["no_deactivate"]

        self.stdout.write("🔄 Fetching realm roles from Keycloak Admin API...")
        try:
            role_names = list_realm_roles()
        except KeycloakAdminError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f"  • {len(role_names)} realm roles returned")

        summary = sync_realm_roles(
            role_names,
            deactivate_stale=deactivate,
            dry_run=dry_run,
        )

        prefix = "[DRY RUN] would " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"✅ {prefix}create {summary['created']}, update {summary['updated']}, "
                f"deactivate {summary['deactivated']} (skipped {summary['skipped']} "
                f"non-conforming / unresolved)"
            )
        )
