"""Un secret de signature propre à chaque application.

Les applications existantes en reçoivent un ici. Sans ce remplissage, elles
continueraient de signer avec un secret vide — c'est-à-dire un HMAC que
n'importe qui peut calculer.

Le générateur est recopié plutôt qu'importé : une migration doit rester
reproductible même si `Application.generate_webhook_secret` change ou disparaît.
"""
import secrets

from django.db import migrations, models


def _generer(apps, schema_editor):
    Application = apps.get_model("applications", "Application")
    a_remplir = list(Application.objects.filter(webhook_secret=""))
    for application in a_remplir:
        application.webhook_secret = f"whs_{secrets.token_urlsafe(32)}"
    Application.objects.bulk_update(a_remplir, ["webhook_secret"], batch_size=200)


def _vider(apps, schema_editor):
    # Sens inverse : le champ disparaît avec la colonne, rien à défaire.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('applications', '0009_application_legacy_send_last_used_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='application',
            name='webhook_secret',
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.RunPython(_generer, _vider),
    ]
