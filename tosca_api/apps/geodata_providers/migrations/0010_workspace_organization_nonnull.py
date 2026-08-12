"""Enforce Workspace.organization NOT NULL after the seed/backfill has run.

Depends on organizations.0002_seed_dcs_and_backfill so every existing row is
already attached to the seed 'dcs' org before the column becomes non-nullable.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("geodata_providers", "0009_workspace_organization_workspace_visibility"),
        ("organizations", "0002_seed_dcs_and_backfill"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workspace",
            name="organization",
            field=models.ForeignKey(
                help_text="Owning organization; derives GeoServer ACL roles from its slug.",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="workspaces",
                to="organizations.organization",
            ),
        ),
    ]
