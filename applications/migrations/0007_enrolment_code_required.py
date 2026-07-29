"""Rend le code d'enrôlement obligatoire, une fois les lignes existantes remplies.

Écrite à la main : `makemigrations` s'arrête sur une invite interactive dès qu'un
champ nullable devient non-nul, parce qu'il ignore que la migration précédente a
déjà rempli chaque ligne. Répondre à l'invite produirait une valeur par défaut
identique partout — ce qui violerait l'unicité.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("applications", "0006_populate_enrolment_codes")]

    operations = [
        migrations.AlterField(
            model_name="application",
            name="enrolment_code",
            field=models.CharField(db_index=True, max_length=32, unique=True),
        ),
    ]
