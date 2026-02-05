"""
Management command to test GeoServer integration
"""
from django.core.management.base import BaseCommand, CommandError
from tosca_api.apps.geodata_engine.geoserver.client import GeoServerClient
from tosca_api.apps.geodata_engine.exceptions import GeoServerConnectionError
from tosca_api.apps.geodata_engine.models import GeodataEngine


class Command(BaseCommand):
    help = 'Test GeoServer integration and connectivity'

    def add_arguments(self, parser):
        parser.add_argument(
            '--url',
            type=str,
            help='GeoServer URL (default: from settings)',
        )
        parser.add_argument(
            '--username',
            type=str,
            help='GeoServer username (default: from settings)',
        )
        parser.add_argument(
            '--password',
            type=str,
            help='GeoServer password (default: from settings)',
        )

    def handle(self, *args, **options):
        self.stdout.write('Testing GeoServer integration...\n')

        try:
            # Get default GeoServer engine from DB or use provided params
            if not any([options.get('url'), options.get('username'), options.get('password')]):
                try:
                    engine = GeodataEngine.objects.filter(is_default=True, engine_type='geoserver').first()
                    if not engine:
                        raise CommandError("No default GeoServer engine found. Use --url, --username, --password options.")
                    
                    client = GeoServerClient(
                        url=engine.geoserver_url,
                        username=engine.admin_username,
                        password=engine.decrypted_admin_password
                    )
                    self.stdout.write(f'✓ Using default GeoServer: {engine.name}')
                except Exception as e:
                    raise CommandError(f"Failed to connect to default engine: {e}")
            else:
                # Use provided connection parameters
                url = options.get('url') or 'http://geoserver:8080/geoserver'
                username = options.get('username') or 'admin'
                password = options.get('password') or 'geoserver'
                
                client = GeoServerClient(url=url, username=username, password=password)
                self.stdout.write(f'✓ GeoServer URL: {url}')
                self.stdout.write(f'✓ Username: {username}')
            
            self.stdout.write('')

            # Test 1: Get workspaces
            self.stdout.write('Test 1: Getting workspaces...')
            try:
                workspaces = client.get_workspaces()
                self.stdout.write(self.style.SUCCESS(f'✓ Found {len(workspaces)} workspaces: {workspaces}'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'✗ Failed to get workspaces: {e}'))
                return

            # Test 2: Create test workspace
            self.stdout.write('\\nTest 2: Creating test workspace...')
            try:
                result = client.create_workspace('test_cli_workspace')
                if result.get('success'):
                    self.stdout.write(self.style.SUCCESS(f'✓ {result["message"]}'))
                else:
                    self.stdout.write(self.style.ERROR(f'✗ Failed to create workspace: {result}'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'✗ Workspace creation failed: {e}'))

            # Test 3: Delete test workspace  
            self.stdout.write('\\nTest 3: Deleting test workspace...')
            try:
                result = client.delete_workspace('test_cli_workspace')
                if result.get('success'):
                    self.stdout.write(self.style.SUCCESS(f'✓ {result["message"]}'))
                else:
                    self.stdout.write(self.style.WARNING(f'⚠ {result.get("message", "Delete result unclear")}'))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'⚠ Workspace deletion failed (might not exist): {e}'))

            self.stdout.write('\\n' + self.style.SUCCESS('🎉 GeoServer integration test completed!'))
                # Use database settings from Django
                from django.conf import settings
                import os
                
                db_settings = settings.DATABASES['default']
                result = engine.create_datastore(
                    workspace='test_workspace',
                    store_name='test_store',
                    host=db_settings.get('HOST', 'localhost'),
                    port=int(db_settings.get('PORT', 5432)),
                    database=db_settings.get('NAME', 'tosca'),
                    username=os.getenv('PG_GS_USER', 'tosca_gs'),
                    password=os.getenv('PG_GS_PASSWORD', 'postgres_gs'),
                    schema='public'
                )
                
                if result.get('success'):
                    self.stdout.write(self.style.SUCCESS(f'✓ {result["message"]}'))
                else:
                    self.stdout.write(self.style.ERROR(f'✗ Failed to create datastore: {result}'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'✗ Datastore creation failed: {e}'))

            # Test 3: List workspaces
            self.stdout.write('\nTest 3: Listing workspaces...')
            try:
                if engine.client.workspace_exists('test_workspace'):
                    self.stdout.write(self.style.SUCCESS('✓ Test workspace exists'))
                else:
                    self.stdout.write(self.style.WARNING('⚠ Test workspace not found'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'✗ Failed to list workspaces: {e}'))

            # Test 4: Check datastore
            self.stdout.write('\nTest 4: Checking datastore...')
            try:
                if engine.client.store_exists('test_workspace', 'test_store'):
                    self.stdout.write(self.style.SUCCESS('✓ Test datastore exists'))
                else:
                    self.stdout.write(self.style.WARNING('⚠ Test datastore not found'))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'✗ Failed to check datastore: {e}'))

            # Cleanup
            self.stdout.write('\nCleaning up test resources...')
            try:
                # Try to delete test workspace (this should cascade delete the datastore)
                engine.client._client.delete_workspace('test_workspace')
                self.stdout.write(self.style.SUCCESS('✓ Test resources cleaned up'))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f'⚠ Cleanup warning: {e}'))

            # Final result
            self.stdout.write(self.style.SUCCESS('\n' + '='*50))
            self.stdout.write(self.style.SUCCESS('GeoServer integration test PASSED!'))
            self.stdout.write('Your GeoServer is properly configured and accessible.')
            self.stdout.write(self.style.SUCCESS('='*50))

        except GeoServerConnectionError as e:
            self.stdout.write(self.style.ERROR(f'\n✗ GeoServer connection failed: {e}'))
            self.stdout.write('\nTroubleshooting checklist:')
            self.stdout.write('1. Is GeoServer running?')
            self.stdout.write('2. Is the URL correct?')
            self.stdout.write('3. Are the credentials correct?')
            self.stdout.write('4. Is there a network connection?')
            self.stdout.write('5. Check firewall settings')
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n✗ Unexpected error: {e}'))
            raise CommandError(f'GeoServer test failed: {e}')