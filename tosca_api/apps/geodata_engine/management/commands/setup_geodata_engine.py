"""
Management command to create sample geodata engine setup
"""
import os
from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth.models import User
from django.conf import settings

from tosca_api.apps.geodata_engine.models import Workspace, Store, Layer
from tosca_api.apps.geodata_engine.services import GeodataEngineService


class Command(BaseCommand):
    help = 'Create sample geodata engine setup with workspaces, stores, and test data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete existing geodata engine data before creating samples',
        )

    def handle(self, *args, **options):
        if options['reset']:
            self.stdout.write('Resetting geodata engine data...')
            Layer.objects.all().delete()
            Store.objects.all().delete()
            Workspace.objects.all().delete()
            self.stdout.write(self.style.SUCCESS('✓ Reset complete'))

        # Get or create superuser
        try:
            user = User.objects.filter(is_superuser=True).first()
            if not user:
                user = User.objects.create_superuser(
                    username='admin',
                    email='admin@example.com',
                    password='admin'
                )
                self.stdout.write(self.style.SUCCESS('✓ Created superuser: admin/admin'))
            else:
                self.stdout.write(f'✓ Using existing superuser: {user.username}')
        except Exception as e:
            raise CommandError(f'Failed to get/create superuser: {e}')

        # Create only vector workspace
        all_workspaces_data = [
            {
                'name': 'vector',
                'description': 'Default workspace for vector data layers (points, lines, polygons)'
            }
        ]

        created_workspaces = []
        service = None
        
        # Initialize service for GeoServer operations
        try:
            service = GeodataEngineService()
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'⚠ Could not initialize GeoServer service: {e}'))

        for ws_data in all_workspaces_data:
            workspace, created = Workspace.objects.get_or_create(
                name=ws_data['name'],
                defaults={
                    'description': ws_data['description'],
                    'created_by': user
                }
            )
            
            # Try to create workspace in GeoServer as well
            if service and created:
                try:
                    service.ensure_workspace_in_geoserver(workspace.name)
                    self.stdout.write(f'✓ Created workspace in Django and GeoServer: {workspace.name}')
                except Exception as e:
                    self.stdout.write(f'✓ Created workspace in Django: {workspace.name} (GeoServer: {e})')
            elif created:
                self.stdout.write(f'✓ Created workspace: {workspace.name}')
            else:
                self.stdout.write(f'✓ Using existing workspace: {workspace.name}')
            created_workspaces.append(workspace)

        # Create stores - prioritize default GIS store
        # Get database settings
        db_settings = settings.DATABASES['default']
        
        # Create only default PostGIS store
        stores_data = [
            {
                'name': 'default_postgis',
                'description': 'Default PostGIS store for vector layers',
                'host': db_settings.get('HOST', 'localhost'),
                'port': int(db_settings.get('PORT', 5432)),
                'database': db_settings.get('NAME', 'tosca'),
                'username': os.getenv('PG_GS_USER', 'tosca_gs'),
                'password': os.getenv('PG_GS_PASSWORD', 'postgres_gs'),
                'schema': os.getenv('PG_SCHEMA_GIS', 'gis')
            }
        ]

        created_stores = []
        for store_data in stores_data:
            store, created = Store.objects.get_or_create(
                name=store_data['name'],
                defaults={
                    'description': store_data['description'],
                    'host': store_data['host'],
                    'port': store_data['port'],
                    'database': store_data['database'],
                    'username': store_data['username'],
                    'password': store_data['password'],
                    'schema': store_data['schema'],
                    'created_by': user
                }
            )
            if created:
                self.stdout.write(f'✓ Created store: {store.name} ({store.host}:{store.port}/{store.database}.{store.schema})')
                
                # Try to create store in GeoServer as well
                if service:
                    try:
                        # Create store in vector workspace
                        service.ensure_store_in_geoserver(store, 'vector')
                        self.stdout.write(f'  ✓ Created store in GeoServer workspace: vector')
                    except Exception as e:
                        self.stdout.write(f'  ⚠ Could not create store in GeoServer: {e}')
                    except Exception as e:
                        self.stdout.write(f'  ⚠ GeoServer store creation failed: {e}')
            else:
                self.stdout.write(f'✓ Using existing store: {store.name}')
            created_stores.append(store)

        # Test GeoServer connection
        self.stdout.write('\nTesting GeoServer connection...')
        try:
            service = GeodataEngineService()
            
            # Try to create a test workspace
            test_result = service.publisher.create_workspace('test_connection', 'Test connection')
            if test_result.get('success'):
                self.stdout.write(self.style.SUCCESS('✓ GeoServer connection successful'))
                
                # Clean up test workspace
                try:
                    service.publisher.client._client.delete_workspace('test_connection')
                except:
                    pass  # Ignore cleanup errors
            else:
                self.stdout.write(self.style.WARNING('⚠ GeoServer connection test inconclusive'))
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ GeoServer connection failed: {e}'))
            self.stdout.write('Please check your GeoServer configuration and make sure it\'s running')

        # Summary
        self.stdout.write(self.style.SUCCESS('\n' + '='*50))
        self.stdout.write(self.style.SUCCESS('Sample setup complete!'))
        self.stdout.write(f'Created {len(created_workspaces)} workspaces:')
        for ws in created_workspaces:
            self.stdout.write(f'  • {ws.name}')
        
        self.stdout.write(f'\nCreated {len(created_stores)} stores:')
        for store in created_stores:
            self.stdout.write(f'  • {store.name} ({store.schema} schema)')
        
        self.stdout.write('\nNext steps:')
        self.stdout.write('1. Access Django admin at /admin/')
        self.stdout.write('2. Go to Geodata Engine section')
        self.stdout.write('3. Use "Upload Layer" to import GeoJSON/GeoPackage files')
        self.stdout.write('4. Use workspace "Introspect Database" to import existing PostGIS tables')
        self.stdout.write('5. Publish layers to GeoServer using the admin actions')
        self.stdout.write(self.style.SUCCESS('='*50))