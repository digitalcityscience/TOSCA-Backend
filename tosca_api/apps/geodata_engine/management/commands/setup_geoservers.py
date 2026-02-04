"""
Management command to setup default GeoServer with auto-detection
Creates default GeoServer instance with vector workspace and gis schema store
"""
import os
import requests
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from tosca_api.apps.geodata_engine.models_simple import GeoServer, Workspace, Store
from tosca_api.apps.geodata_engine.geoserver.client import GeoServerClient


class Command(BaseCommand):
    help = 'Auto-setup default GeoServer with vector workspace and gis schema store'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force recreation of default setup (deletes existing)',
        )

    def handle(self, *args, **options):
        
        # Check if GeoServer is available at geoserver:8080
        geoserver_host = "geoserver"
        geoserver_port = 8080
        geoserver_url = f"http://{geoserver_host}:{geoserver_port}/geoserver"
        
        self.stdout.write("🔍 Checking for GeoServer container...")
        
        try:
            response = requests.get(f"{geoserver_url}/web/", timeout=10)
            if response.status_code != 200:
                self.stdout.write(
                    self.style.WARNING(f'❌ GeoServer not accessible at {geoserver_url}')
                )
                self.stdout.write("Make sure docker-compose is running with GeoServer container")
                return
        except requests.exceptions.RequestException as e:
            self.stdout.write(
                self.style.ERROR(f'❌ GeoServer connection failed: {e}')
            )
            return
        
        self.stdout.write(
            self.style.SUCCESS(f'✅ GeoServer detected at {geoserver_url}')
        )
        
        # Get or create superuser
        creator = User.objects.filter(is_superuser=True).first()
        if not creator:
            creator = User.objects.first()
        
        if not creator:
            self.stdout.write(
                self.style.ERROR('❌ No users found. Please create a superuser first.')
            )
            return
        
        # Handle force option
        if options['force']:
            self.stdout.write("🗑️  Force mode: Removing existing default setup...")
            GeoServer.objects.filter(is_default=True).delete()
            self.stdout.write("✅ Cleaned existing default setup")
            
        # Create or get default GeoServer
        default_geoserver, created = GeoServer.objects.get_or_create(
            is_default=True,
            defaults={
                'name': 'Default GeoServer',
                'url': geoserver_url,
                'username': 'admin',
                'password': 'geoserver',
                'description': 'Default GeoServer instance auto-detected from Docker Compose',
                'is_active': True,
                'created_by': creator
            }
        )
        
        if created:
            self.stdout.write(
                self.style.SUCCESS('✅ Created default GeoServer instance')
            )
        else:
            self.stdout.write(
                self.style.WARNING('⚠️  Default GeoServer already exists')
            )
            
        # Create or get vector workspace
        vector_workspace, created = Workspace.objects.get_or_create(
            geoserver=default_geoserver,
            name='vector',
            defaults={
                'description': 'Default vector workspace for spatial data publishing',
                'created_by': creator
            }
        )
        
        if created:
            self.stdout.write(
                self.style.SUCCESS('✅ Created vector workspace')
            )
        else:
            self.stdout.write(
                self.style.WARNING('⚠️  Vector workspace already exists')
            )
        
        # Create or get default PostGIS store (gis schema)
        default_store, created = Store.objects.get_or_create(
            geoserver=default_geoserver,
            name='default_postgis',
            defaults={
                'host': 'postgis',
                'port': 5432,
                'database': os.getenv('PG_DATABASE', 'tosca_gis'),
                'username': os.getenv('PG_GS_USER', 'geoserver_user'),
                'password': os.getenv('PG_GS_PASSWORD', 'password'),
                'schema': 'gis',
                'description': 'Default PostGIS store connected to gis schema for vector data',
                'created_by': creator
            }
        )
        
        if created:
            self.stdout.write(
                self.style.SUCCESS('✅ Created default PostGIS store (gis schema)')
            )
        else:
            self.stdout.write(
                self.style.WARNING('⚠️  Default PostGIS store already exists')
            )
            
        # Setup GeoServer client and create workspace/store in actual GeoServer
        self.stdout.write("🌐 Configuring actual GeoServer...")
        
        client = GeoServerClient(
            url=default_geoserver.url,
            username=default_geoserver.username,
            password=default_geoserver.password
        )
        
        try:
            # Create workspace in GeoServer
            if client.create_workspace(vector_workspace.name):
                self.stdout.write(
                    self.style.SUCCESS(f'✅ Created workspace "{vector_workspace.name}" in GeoServer')
                )
            else:
                self.stdout.write(
                    self.style.WARNING(f'⚠️  Workspace "{vector_workspace.name}" already exists in GeoServer')
                )
                
            # Create PostGIS store in GeoServer
            if client.create_postgis_store(
                store_name=default_store.name,
                workspace=vector_workspace.name,
                db=default_store.database,
                host=default_store.host,
                port=default_store.port,
                schema=default_store.schema,
                pg_user=default_store.username,
                pg_password=default_store.password
            ):
                self.stdout.write(
                    self.style.SUCCESS(f'✅ Created PostGIS store "{default_store.name}" in GeoServer')
                )
            else:
                self.stdout.write(
                    self.style.WARNING(f'⚠️  PostGIS store "{default_store.name}" already exists in GeoServer')
                )
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ GeoServer setup error: {e}')
            )
            
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                '🚀 Setup completed successfully!\n'
                '\n'
                f'📍 Default GeoServer: {default_geoserver.name}\n'
                f'🌐 URL: {default_geoserver.url}\n'
                f'📁 Workspace: {vector_workspace.name}\n'  
                f'🗄️  Store: {default_store.name} (schema: {default_store.schema})\n'
                f'👤 Admin user: {default_geoserver.username}\n'
                '\n'
                '✨ You can now:\n'
                '  1. Access Django admin to manage your GeoServer\n'
                '  2. Upload GeoJSON/GeoPackage files to publish layers\n'
                '  3. Add more GeoServer instances as needed\n'
            )
        )