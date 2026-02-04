"""
Management command to create default GeoServer instance
"""
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.conf import settings
from tosca_api.apps.geodata_engine.models import GeodataEngine, Store


class Command(BaseCommand):
    help = 'Create default GeoServer instance and PostgreSQL store'

    def handle(self, *args, **options):
        # Get or create superuser
        try:
            admin_user = User.objects.filter(is_superuser=True).first()
            if not admin_user:
                admin_user = User.objects.create_superuser(
                    username='admin',
                    email='admin@localhost',
                    password='admin'
                )
                self.stdout.write(f"Created admin user: {admin_user.username}")
        except Exception as e:
            self.stdout.write(f"Using existing admin user")
            admin_user = User.objects.filter(is_superuser=True).first()

        # Create default GeoServer engine
        geoserver_host = getattr(settings, 'GEOSERVER_HOST', 'geoserver')
        geoserver_port = getattr(settings, 'GEOSERVER_PORT', '8080')
        geoserver_user = getattr(settings, 'GEOSERVER_ADMIN_USER', 'admin2')
        geoserver_password = getattr(settings, 'GEOSERVER_ADMIN_PASSWORD', 'geoserver2')
        
        engine, created = GeodataEngine.objects.get_or_create(
            name='Default GeoServer',
            defaults={
                'description': 'Default GeoServer instance for development',
                'base_url': f'http://{geoserver_host}:{geoserver_port}/geoserver',
                'admin_username': geoserver_user,
                'admin_password': geoserver_password,
                'is_active': True,
                'is_default': True,
                'created_by': admin_user
            }
        )
        
        if created:
            self.stdout.write(
                self.style.SUCCESS(f'Created GeoServer engine: {engine.name}')
            )
        else:
            self.stdout.write(f'GeoServer engine already exists: {engine.name}')

        # Create default PostgreSQL store
        from os import environ
        
        pg_host = environ.get('PG_HOST', 'db')
        pg_port = int(environ.get('PG_DOCKER_PORT', '5432'))
        pg_database = environ.get('PG_DATABASE', 'tosca')
        pg_user = environ.get('PG_GS_USER', 'tosca_gs')
        pg_password = environ.get('PG_GS_PASSWORD', 'postgres_gs')
        pg_schema = environ.get('PG_SCHEMA_GIS', 'gis_schema')
        
        store, created = Store.objects.get_or_create(
            name='Default PostGIS',
            geodata_engine=engine,
            defaults={
                'description': 'Default PostGIS store for GIS data',
                'host': pg_host,
                'port': pg_port,
                'database': pg_database,
                'username': pg_user,
                'password': pg_password,
                'schema': pg_schema,
                'created_by': admin_user
            }
        )
        
        if created:
            self.stdout.write(
                self.style.SUCCESS(f'Created PostgreSQL store: {store.name}')
            )
        else:
            self.stdout.write(f'PostgreSQL store already exists: {store.name}')

        self.stdout.write(
            self.style.SUCCESS('Setup completed! You can now access the admin panel.')
        )