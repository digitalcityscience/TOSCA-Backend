"""Enforce Campaign.organization NOT NULL after the seed/backfill has run."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("campaigns", "0003_campaign_organization"),
        ("organizations", "0002_seed_dcs_and_backfill"),
    ]

    operations = [
        migrations.AlterField(
            model_name="campaign",
            name="organization",
            field=models.ForeignKey(
                help_text="Owning organization.",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="campaigns",
                to="organizations.organization",
            ),
        ),
    ]
