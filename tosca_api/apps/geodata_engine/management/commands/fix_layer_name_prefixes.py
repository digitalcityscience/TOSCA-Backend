"""
Management command to fix layer names that contain workspace prefixes.

GeoServer REST API returns layer names as 'workspace:layername' but Django
Layer.name should store only 'layername'. This command cleans up any
corrupted rows created before the client.get_layers() patch.
"""
import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from tosca_api.apps.geodata_engine.models import Layer

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Fix layer names containing workspace prefixes (e.g. "vector:buildings" → "buildings")'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be changed without making changes',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Skip duplicate checking and force rename (dangerous)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']

        # Find all layers with ':' in the name
        corrupted_layers = Layer.objects.filter(name__contains=':')

        if not corrupted_layers.exists():
            self.stdout.write(self.style.SUCCESS('✅ No corrupted layer names found.'))
            return

        self.stdout.write(f'🔍 Found {corrupted_layers.count()} layers with workspace prefixes:')

        changes = []
        skipped = []

        for layer in corrupted_layers:
            # Split on first ':' and take the part after
            if ':' not in layer.name:
                continue  # Should not happen due to filter

            prefix, clean_name = layer.name.split(':', 1)

            self.stdout.write(f'  {layer.workspace.name}:{layer.name} → {clean_name}')

            # Check for duplicate in same workspace
            existing = Layer.objects.filter(
                workspace=layer.workspace,
                name=clean_name
            ).exclude(pk=layer.pk).first()

            if existing and not force:
                skipped.append({
                    'layer': layer,
                    'clean_name': clean_name,
                    'conflict': existing
                })
                self.stdout.write(self.style.WARNING(
                    f'    ⚠️  Skipping: duplicate name "{clean_name}" already exists '
                    f'(ID: {existing.pk})'
                ))
                continue

            changes.append({
                'layer': layer,
                'old_name': layer.name,
                'new_name': clean_name,
                'conflict_resolved': existing is not None
            })

        if not changes and not skipped:
            self.stdout.write(self.style.SUCCESS('✅ No changes needed.'))
            return

        if dry_run:
            self.stdout.write(self.style.WARNING('🔍 DRY RUN - No changes made.'))
            return

        # Apply changes
        with transaction.atomic():
            for change in changes:
                layer = change['layer']
                old_name = change['old_name']
                new_name = change['new_name']

                layer.name = new_name
                layer.save(update_fields=['name'])

                logger.info(f'Fixed layer name prefix: {layer.workspace.name}:{old_name} → {new_name}')
                self.stdout.write(self.style.SUCCESS(
                    f'✅ Renamed: {layer.workspace.name}:{old_name} → {new_name}'
                ))

        # Summary
        self.stdout.write(f'\n📊 Summary:')
        self.stdout.write(f'  ✅ Renamed: {len(changes)} layers')
        if skipped:
            self.stdout.write(self.style.WARNING(f'  ⚠️  Skipped: {len(skipped)} layers (duplicates)'))

        # Final verification
        remaining = Layer.objects.filter(name__contains=':').count()
        if remaining == 0:
            self.stdout.write(self.style.SUCCESS('✅ All layer names are now clean.'))
        else:
            self.stdout.write(self.style.ERROR(f'❌ {remaining} layers still have prefixes.'))