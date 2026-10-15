from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("footer", "0002_defer_document_order_constraint")]

    operations = [
        migrations.RemoveConstraint(
            model_name="footerlogo",
            name="unique_footer_logo_order",
        ),
        migrations.AddConstraint(
            model_name="footerlogo",
            constraint=models.UniqueConstraint(
                fields=("footer", "display_order"),
                name="unique_footer_logo_order",
                deferrable=models.Deferrable.DEFERRED,
            ),
        ),
    ]
