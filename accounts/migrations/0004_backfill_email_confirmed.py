from django.db import migrations


def confirm_existing_users(apps, schema_editor):
    """Existing accounts predate email confirmation — mark them confirmed so the
    new login gate doesn't lock them out. Only the field's default (False) applies
    to accounts created after this migration."""
    User = apps.get_model("accounts", "User")
    User.objects.update(email_confirmed=True)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_user_email_confirmed"),
    ]

    operations = [
        migrations.RunPython(confirm_existing_users, noop_reverse),
    ]
