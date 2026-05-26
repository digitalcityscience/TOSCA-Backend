"""
Management command to setup default GeodataEngine from environment variables
Reads from Django settings which loads .env.dev or .env.prod based on ENV_TYPE
"""
import requests
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.conf import settings
from tosca_api.apps.geodata_providers.models import GeodataEngine


class Command(BaseCommand):
    help = 'Setup default GeodataEngine from environment variables (.env.dev/.env.prod)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force recreate if default engine already exists',
        )

    def handle(self, *args, **options):
        
        # Get environment variables from Django settings (which loads from .env files)
        geoserver_host = settings.GEOSERVER_HOST
        geoserver_port = settings.GEOSERVER_PORT
        geoserver_admin_user = settings.GEOSERVER_ADMIN_USER
        geoserver_admin_password = settings.GEOSERVER_ADMIN_PASSWORD
        env_type = getattr(settings, 'ENV', 'dev')
        geoserver_public_url = getattr(settings, 'GEOSERVER_PUBLIC_URL', '')
        
        # Build GeoServer URL
        geoserver_url = f"http://{geoserver_host}:{geoserver_port}/geoserver"
        if not geoserver_public_url:
            if env_type == 'prod':
                self.stdout.write(
                    self.style.ERROR('❌ GEOSERVER_PUBLIC_URL is required in production')
                )
                return
            geoserver_public_url = f"http://localhost:{geoserver_port}/geoserver"
        
        self.stdout.write(f"🔧 Environment: {env_type}")
        self.stdout.write(f"🌐 GeoServer URL: {geoserver_url}")
        self.stdout.write(f"🌍 Public GeoServer URL: {geoserver_public_url}")
        self.stdout.write(f"👤 Admin User: {geoserver_admin_user}")
        
        # Test GeoServer connectivity first
        try:
            self.stdout.write("🔍 Testing GeoServer connectivity...")
            response = requests.get(f"{geoserver_url}/web/", timeout=10)
            if response.status_code == 200:
                self.stdout.write(self.style.SUCCESS('✅ GeoServer is accessible'))
            else:
                self.stdout.write(
                    self.style.WARNING(f'⚠️ GeoServer returned status {response.status_code}')
                )
        except requests.exceptions.RequestException as e:
            self.stdout.write(
                self.style.ERROR(f'❌ GeoServer connection failed: {e}')
            )
            self.stdout.write(self.style.WARNING('⚠️ Continuing with setup anyway...'))
        
        # Get or create superuser
        try:
            user = User.objects.filter(is_superuser=True).first()
            if not user:
                user = User.objects.create_superuser(
                    username='admin',
                    email='admin@local.dev',
                    password='admin123'
                )
                self.stdout.write(
                    self.style.SUCCESS(f'✅ Created superuser: {user.username}')
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(f'✅ Using existing superuser: {user.username}')
                )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Failed to create/get superuser: {e}')
            )
            return
        
        # Check if default engine exists
        existing_engine = GeodataEngine.objects.filter(is_default=True).first()
        if existing_engine and not options['force']:
            self.stdout.write(
                self.style.WARNING(f'⚠️ Default engine already exists: {existing_engine.name}')
            )
            changed_fields = []
            updates = {
                'base_url': geoserver_url,
                'public_url': geoserver_public_url,
                'admin_username': geoserver_admin_user,
                'admin_password': geoserver_admin_password,
                'is_active': True,
            }
            for field, value in updates.items():
                if getattr(existing_engine, field) != value:
                    setattr(existing_engine, field, value)
                    changed_fields.append(field)

            if changed_fields:
                existing_engine.save(update_fields=changed_fields + ['updated_at'])
                self.stdout.write(
                    self.style.SUCCESS(
                        f'✅ Updated existing default GeodataEngine fields: {", ".join(changed_fields)}'
                    )
                )
                self.stdout.write(f'   URL: {existing_engine.base_url}')
                self.stdout.write(f'   Public URL: {existing_engine.public_url}')
            else:
                self.stdout.write('Default engine already matches current settings.')
            self.stdout.write('Use --force to recreate it')
            return
        
        # Delete existing if force option
        if existing_engine and options['force']:
            existing_engine.delete()
            self.stdout.write(
                self.style.WARNING(f'🗑️ Deleted existing engine: {existing_engine.name}')
            )
        
        # Create default GeodataEngine
        try:
            # Since encryption is temporarily disabled, we can save password directly
            # The model's save method currently skips encryption
            engine = GeodataEngine.objects.create(
                name='Default GeoServer',
                description=f'Auto-created GeoServer engine from {env_type} environment',
                base_url=geoserver_url,
                public_url=geoserver_public_url,
                admin_username=geoserver_admin_user,
                admin_password=geoserver_admin_password,  # Will be stored as plain text temporarily
                is_active=True,
                is_default=True,
                created_by=user
            )
            
            self.stdout.write(
                self.style.SUCCESS(f'✅ Created default GeodataEngine: {engine.name}')
            )
            self.stdout.write(f'   ID: {engine.id}')
            self.stdout.write(f'   URL: {engine.base_url}')
            self.stdout.write(f'   Public URL: {engine.public_url}')
            self.stdout.write(f'   Username: {engine.admin_username}')
            self.stdout.write(f'   Default: {engine.is_default}')
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Failed to create GeodataEngine: {e}')
            )
            return
        
        self.stdout.write(
            self.style.SUCCESS('🎉 Setup completed successfully!')
        )
        self.stdout.write(
            'You can now run: python manage.py sync_geoserver'
        )
