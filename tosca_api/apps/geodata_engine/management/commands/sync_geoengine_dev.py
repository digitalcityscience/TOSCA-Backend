"""Dev helper command to sync geoengine without API token/auth flow."""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from tosca_api.apps.geodata_engine.engine_factory import EngineClientFactory
from tosca_api.apps.geodata_engine.models import GeodataEngine


class Command(BaseCommand):
    help = 'Sync GeoEngine directly in dev (no token required)'

    def add_arguments(self, parser):
        parser.add_argument('--engine-id', type=str, help='Engine UUID to sync')
        parser.add_argument('--engine-name', type=str, help='Engine name to sync')
        parser.add_argument('--all', action='store_true', help='Sync all active engines')
        parser.add_argument('--dry-run', action='store_true', help='Print selected engines only')

    def handle(self, *args, **options):
        engines = self._select_engines(options)
        if not engines:
            raise CommandError('No active engines found to sync')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('[DRY RUN] Engines to sync:'))
            for engine in engines:
                self.stdout.write(f"- {engine.id} | {engine.name} | {engine.engine_type}")
            return

        sync_user = self._resolve_sync_user()
        self.stdout.write(self.style.SUCCESS(f"Using sync user: {sync_user.username}"))

        total_ok = 0
        for engine in engines:
            self.stdout.write(f"\nSyncing: {engine.name} ({engine.id})")
            sync_service = EngineClientFactory.create_sync_service(engine)
            result = sync_service.sync_all_resources(created_by=sync_user)

            if result.get('success', False):
                total_ok += 1
                ws = result.get('workspaces', {})
                st = result.get('stores', {})
                ly = result.get('layers', {})
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ OK | ws c/s/d: {ws.get('created', 0)}/{ws.get('synced', 0)}/{ws.get('deleted', 0)} | "
                        f"st c/s/d: {st.get('created', 0)}/{st.get('synced', 0)}/{st.get('deleted', 0)} | "
                        f"ly c/s/d: {ly.get('created', 0)}/{ly.get('synced', 0)}/{ly.get('deleted', 0)}"
                    )
                )
            else:
                self.stdout.write(self.style.ERROR(f"✗ FAILED | {result.get('error', 'Unknown error')}"))

        self.stdout.write(
            self.style.SUCCESS(f"\nCompleted: {total_ok}/{len(engines)} engines synced successfully")
        )

    def _select_engines(self, options):
        if options['all']:
            return list(GeodataEngine.objects.filter(is_active=True).order_by('name'))

        if options.get('engine_id'):
            engine = GeodataEngine.objects.filter(id=options['engine_id'], is_active=True).first()
            return [engine] if engine else []

        if options.get('engine_name'):
            engine = GeodataEngine.objects.filter(name=options['engine_name'], is_active=True).first()
            return [engine] if engine else []

        default_engine = GeodataEngine.objects.filter(is_default=True, is_active=True).first()
        return [default_engine] if default_engine else []

    def _resolve_sync_user(self):
        user = User.objects.filter(is_superuser=True).first()
        if user:
            return user

        user = User.objects.filter(is_staff=True).first()
        if user:
            return user

        return User.objects.create_superuser(
            username='geoengine_dev_sync',
            email='geoengine_dev_sync@local.dev',
            password='geoengine_dev_sync',
        )
