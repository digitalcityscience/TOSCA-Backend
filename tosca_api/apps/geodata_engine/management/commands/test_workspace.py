"""
Test workspace creation with GeoServer
"""
from django.core.management.base import BaseCommand
from tosca_api.apps.geodata_engine.models import GeodataEngine
from tosca_api.apps.geodata_engine.geoserver.client import GeoServerClient


class Command(BaseCommand):
    help = 'Test workspace creation'

    def handle(self, *args, **options):
        try:
            # Get default engine
            engine = GeodataEngine.objects.filter(is_default=True).first()
            if not engine:
                self.stdout.write(self.style.ERROR('No default engine found'))
                return

            self.stdout.write(f'Testing workspace creation with: {engine.admin_username}')

            # Create client
            client = GeoServerClient(
                url=engine.base_url,
                username=engine.admin_username,
                password=engine.admin_password
            )
            
            # Test workspace creation
            workspace_name = 'test_mobility'
            
            # Check if exists first
            exists = client.workspace_exists(workspace_name)
            self.stdout.write(f'Workspace "{workspace_name}" exists: {exists}')
            
            # Create workspace
            result = client.create_workspace(workspace_name)
            
            self.stdout.write(self.style.SUCCESS(f'✅ {result["message"]}'))
            
            # Verify it was created
            exists_after = client.workspace_exists(workspace_name)
            self.stdout.write(f'Workspace "{workspace_name}" exists after creation: {exists_after}')
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Test failed: {e}'))