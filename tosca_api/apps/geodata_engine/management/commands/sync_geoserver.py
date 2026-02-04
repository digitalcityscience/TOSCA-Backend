"""
Auto-sync Django with GeoServer on system startup
Ensures Django DB stays synchronized with GeoServer resources
"""
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from tosca_api.apps.geodata_engine.models import GeodataEngine
from tosca_api.apps.geodata_engine.sync_service import GeoServerSyncService


class Command(BaseCommand):
    help = 'Sync all GeoServer instances with Django database'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--engine',
            type=str,
            help='Sync specific engine by name (default: all active engines)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be synced without making changes'
        )
    
    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS('🔄 Starting GeoServer synchronization...')
        )
        
        # Get superuser for created_by field
        try:
            sync_user = User.objects.filter(is_superuser=True).first()
            if not sync_user:
                sync_user = User.objects.create_superuser(
                    username='geoserver_sync',
                    email='sync@geoserver.local',
                    password='sync123'
                )
                self.stdout.write(
                    self.style.WARNING(f'Created sync user: {sync_user.username}')
                )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Failed to get/create sync user: {e}')
            )
            return
        
        # Get engines to sync
        if options['engine']:
            engines = GeodataEngine.objects.filter(
                name=options['engine'], is_active=True
            )
            if not engines.exists():
                self.stdout.write(
                    self.style.ERROR(f'❌ Engine "{options["engine"]}" not found or inactive')
                )
                return
        else:
            engines = GeodataEngine.objects.filter(is_active=True)
        
        if not engines.exists():
            self.stdout.write(
                self.style.WARNING('⚠️ No active GeoServer engines found')
            )
            return
        
        # Sync each engine
        total_synced = 0
        total_created = 0
        total_deleted = 0
        total_errors = 0
        
        for engine in engines:
            self.stdout.write(f'\n🏭 Syncing engine: {engine.name}')
            
            if options['dry_run']:
                self.stdout.write(
                    self.style.WARNING('  [DRY RUN] Would sync resources from GeoServer')
                )
                continue
            
            try:
                sync_service = GeoServerSyncService(engine)
                results = sync_service.sync_all_resources(created_by=sync_user)
                
                if results.get('success'):
                    # Workspaces
                    ws_synced = results['workspaces']['synced']
                    ws_created = results['workspaces']['created']
                    ws_deleted = results['workspaces']['deleted']
                    ws_errors = len(results['workspaces']['errors'])
                    
                    # Stores  
                    st_synced = results['stores']['synced']
                    st_created = results['stores']['created']
                    st_deleted = results['stores']['deleted']
                    st_errors = len(results['stores']['errors'])
                    
                    # Layers
                    ly_synced = results['layers']['synced']
                    ly_created = results['layers']['created']
                    ly_deleted = results['layers']['deleted']
                    ly_errors = len(results['layers']['errors'])
                    
                    # Totals
                    engine_synced = ws_synced + st_synced + ly_synced
                    engine_created = ws_created + st_created + ly_created
                    engine_deleted = ws_deleted + st_deleted + ly_deleted
                    engine_errors = ws_errors + st_errors + ly_errors
                    
                    total_synced += engine_synced
                    total_created += engine_created
                    total_deleted += engine_deleted
                    total_errors += engine_errors
                    
                    self.stdout.write(
                        self.style.SUCCESS(f'  ✅ Success: {engine_created} created, {engine_synced} synced, {engine_deleted} deleted')
                    )
                    
                    if engine_errors > 0:
                        self.stdout.write(
                            self.style.WARNING(f'  ⚠️ {engine_errors} errors occurred')
                        )
                        # Show errors
                        for error in results['workspaces']['errors']:
                            self.stdout.write(f'    - Workspace: {error}')
                        for error in results['stores']['errors']:
                            self.stdout.write(f'    - Store: {error}')
                        for error in results['layers']['errors']:
                            self.stdout.write(f'    - Layer: {error}')
                
                else:
                    error_msg = results.get('error', 'Unknown error')
                    self.stdout.write(
                        self.style.ERROR(f'  ❌ Failed: {error_msg}')
                    )
                    total_errors += 1
                    
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'  ❌ Exception: {e}')
                )
                total_errors += 1
        
        # Final summary
        self.stdout.write('\n' + '='*50)
        self.stdout.write(
            self.style.SUCCESS(f'🎯 Sync Summary:')
        )
        self.stdout.write(f'  • Engines processed: {engines.count()}')
        self.stdout.write(f'  • Resources created: {total_created}')
        self.stdout.write(f'  • Resources synced: {total_synced}')
        self.stdout.write(f'  • Resources deleted: {total_deleted}')
        
        if total_errors > 0:
            self.stdout.write(
                self.style.WARNING(f'  • Errors: {total_errors}')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS('  • No errors! 🎉')
            )
        
        self.stdout.write('='*50)