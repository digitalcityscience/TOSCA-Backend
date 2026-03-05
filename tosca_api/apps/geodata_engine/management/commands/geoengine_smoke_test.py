"""
GeoEngine smoke test command for local/dev verification.
Checks and ensures Engine -> Workspace -> Store consistency in Django and GeoServer.
"""

import os

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from tosca_api.apps.geodata_engine.engine_factory import EngineClientFactory
from tosca_api.apps.geodata_engine.models import GeodataEngine, Store, Workspace


class Command(BaseCommand):
    help = 'Run GeoEngine smoke test: validate engine, ensure workspace and PostGIS store in DB + GeoServer'

    def add_arguments(self, parser):
        parser.add_argument('--engine', type=str, help='Engine name (default: active default geoserver engine)')
        parser.add_argument('--workspace', type=str, default='vector', help='Workspace name (default: vector)')
        parser.add_argument('--store', type=str, default='default_postgis', help='Store name (default: default_postgis)')
        parser.add_argument('--schema', type=str, default='gis', help='PostGIS schema (default: gis)')
        parser.add_argument('--dry-run', action='store_true', help='Only check and print planned actions')

    def handle(self, *args, **options):
        workspace_name = options['workspace']
        store_name = options['store']
        schema_name = options['schema']
        dry_run = options['dry_run']

        engine = self._resolve_engine(options.get('engine'))
        self.stdout.write(self.style.SUCCESS(f"Using engine: {engine.name} ({engine.engine_type})"))

        sync_user = self._resolve_user(engine)
        self.stdout.write(self.style.SUCCESS(f"Using user: {sync_user.username}"))

        client = EngineClientFactory.create_client(engine)

        # 1) Validate engine connectivity.
        try:
            workspaces = client.get_workspaces()
            self.stdout.write(self.style.SUCCESS(f"✓ Engine reachable, current workspace count: {len(workspaces)}"))
        except Exception as exc:
            raise CommandError(f"Engine connectivity check failed: {exc}")

        # 2) Ensure workspace in DB.
        workspace, ws_created = Workspace.objects.get_or_create(
            geodata_engine=engine,
            name=workspace_name,
            defaults={
                'description': f'Smoke test workspace: {workspace_name}',
                'created_by': sync_user,
            },
        )
        if ws_created:
            self.stdout.write(self.style.SUCCESS(f"✓ DB workspace created: {workspace.name}"))
        else:
            self.stdout.write(f"• DB workspace exists: {workspace.name}")

        # 3) Ensure workspace in engine.
        workspace_exists_in_engine = client.workspace_exists(workspace_name)
        if workspace_exists_in_engine:
            self.stdout.write(f"• Engine workspace exists: {workspace_name}")
        elif dry_run:
            self.stdout.write(self.style.WARNING(f"[DRY RUN] Would create engine workspace: {workspace_name}"))
        else:
            result = client.create_workspace(workspace_name)
            if result.get('success', False):
                self.stdout.write(self.style.SUCCESS(f"✓ Engine workspace created: {workspace_name}"))
            else:
                raise CommandError(f"Workspace create failed in engine: {result}")

        # 4) Build PostGIS store config from env/settings.
        db_defaults = settings.DATABASES.get('default', {})
        host = os.getenv('PG_HOST') or db_defaults.get('HOST') or 'db'
        port = int(os.getenv('PG_DOCKER_PORT') or db_defaults.get('PORT') or 5432)
        database = os.getenv('PG_DATABASE') or db_defaults.get('NAME') or 'tosca'
        username = os.getenv('PG_GS_USER') or os.getenv('PG_SUPERUSER') or 'postgres'
        password = os.getenv('PG_GS_PASSWORD') or os.getenv('PG_SUPERPASS') or 'postgres'

        # 5) Ensure store in DB.
        store, store_created = Store.objects.get_or_create(
            workspace=workspace,
            name=store_name,
            defaults={
                'geodata_engine': engine,
                'store_type': 'postgis',
                'host': host,
                'port': port,
                'database': database,
                'username': username,
                'password': password,
                'schema': schema_name,
                'description': f'Smoke test store: {store_name}',
                'created_by': sync_user,
            },
        )
        if store_created:
            self.stdout.write(self.style.SUCCESS(f"✓ DB store created: {workspace_name}/{store_name}"))
        else:
            self.stdout.write(f"• DB store exists: {workspace_name}/{store_name}")

        # 6) Ensure store in engine.
        store_exists_in_engine = client.store_exists(workspace_name, store_name)
        if store_exists_in_engine:
            self.stdout.write(f"• Engine store exists: {workspace_name}/{store_name}")
        elif dry_run:
            self.stdout.write(self.style.WARNING(f"[DRY RUN] Would create engine store: {workspace_name}/{store_name}"))
        else:
            result = client.create_postgis_store(
                name=store_name,
                workspace=workspace_name,
                host=host,
                port=port,
                database=database,
                username=username,
                password=password,
                schema=schema_name,
            )
            if result.get('success', False):
                self.stdout.write(self.style.SUCCESS(f"✓ Engine store created: {workspace_name}/{store_name}"))
            else:
                raise CommandError(f"Store create failed in engine: {result}")

        self.stdout.write(self.style.SUCCESS('\nSmoke test completed successfully.'))

    def _resolve_engine(self, engine_name):
        if engine_name:
            engine = GeodataEngine.objects.filter(name=engine_name, is_active=True).first()
            if not engine:
                raise CommandError(f"Engine '{engine_name}' not found or inactive")
            return engine

        engine = GeodataEngine.objects.filter(is_default=True, is_active=True).first()
        if not engine:
            raise CommandError('No active default engine found. Run setup_default_engine first.')
        return engine

    def _resolve_user(self, engine):
        if engine.created_by_id:
            return engine.created_by

        user = User.objects.filter(is_superuser=True).first()
        if user:
            return user

        user = User.objects.create_superuser(
            username='geoengine_smoke',
            email='geoengine_smoke@local.dev',
            password='geoengine_smoke',
        )
        return user
