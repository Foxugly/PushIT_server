"""Attribue un code d'enrôlement aux applications existantes.

Le champ est déclaré nullable par la migration précédente précisément pour
rendre celle-ci possible : une valeur par défaut aurait été la même pour toutes
les lignes, ce qui viole l'unicité.

Le générateur est recopié ici plutôt qu'importé du modèle. Une migration doit
rester reproductible dans dix ans : si `generate_enrolment_code` change de
format un jour, rejouer l'historique produirait autre chose que ce qui est en
base aujourd'hui.
"""
import secrets

from django.db import migrations


def _code() -> str:
    alphabet = secrets.token_urlsafe(24).replace("-", "").replace("_", "")
    return f"apk_{alphabet[:12]}"


def attribuer(apps, schema_editor):
    Application = apps.get_model("applications", "Application")
    a_remplir = list(Application.objects.filter(enrolment_code__isnull=True))
    if not a_remplir:
        return

    # Unicite garantie cote base ; on evite quand meme une collision dans le lot,
    # qui ferait echouer la migration entiere pour une malchance de 2^-70.
    deja_vus = set(
        Application.objects.exclude(enrolment_code__isnull=True).values_list(
            "enrolment_code", flat=True
        )
    )
    for application in a_remplir:
        code = _code()
        while code in deja_vus:
            code = _code()
        deja_vus.add(code)
        application.enrolment_code = code

    Application.objects.bulk_update(a_remplir, ["enrolment_code"], batch_size=200)


def revenir(apps, schema_editor):
    # Rien a defaire : le champ disparait avec la migration de schema.
    pass


class Migration(migrations.Migration):
    dependencies = [("applications", "0005_enrolment_code")]

    operations = [migrations.RunPython(attribuer, revenir)]
