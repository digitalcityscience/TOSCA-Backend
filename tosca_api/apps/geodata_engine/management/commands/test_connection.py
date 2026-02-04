"""
Simple GeoServer connection test
"""
from django.core.management.base import BaseCommand
from tosca_api.apps.geodata_engine.models import GeodataEngine


class Command(BaseCommand):
    help = 'Test GeoServer connection'

    def handle(self, *args, **options):
        try:
            # Get default engine
            engine = GeodataEngine.objects.filter(is_default=True).first()
            if not engine:
                self.stdout.write(
                    self.style.ERROR('No default GeoServer engine found.')
                )
                return

            self.stdout.write(f'Testing: {engine.base_url}')
            self.stdout.write(f'User: {engine.admin_username} / Pass: {engine.admin_password}')

            # Try basic import
            from tosca_api.apps.geodata_engine.geoserver.client import GeoServerClient
            
            client = GeoServerClient(
                url=engine.base_url,
                username=engine.admin_username,
                password=engine.admin_password
            )
            
            self.stdout.write(self.style.SUCCESS('✅ GeoServer client created successfully'))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'❌ Failed: {e}'))