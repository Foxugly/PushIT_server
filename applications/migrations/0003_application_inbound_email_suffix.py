from django.db import migrations, models


def backfill_suffix(apps, schema_editor):
    Application = apps.get_model("applications", "Application")
    for app in Application.objects.all():
        # The suffix is the final "_"-segment of the existing alias.
        app.inbound_email_suffix = app.inbound_email_alias.rsplit("_", 1)[-1]
        app.save(update_fields=["inbound_email_suffix"])


class Migration(migrations.Migration):

    dependencies = [
        ("applications", "0002_application_webhook_url"),
    ]

    operations = [
        # Add non-unique first so existing rows can be backfilled before the
        # UNIQUE index is created.
        migrations.AddField(
            model_name="application",
            name="inbound_email_suffix",
            field=models.CharField(default="", max_length=32),
        ),
        migrations.RunPython(backfill_suffix, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="application",
            name="inbound_email_suffix",
            field=models.CharField(max_length=32, unique=True),
        ),
    ]
