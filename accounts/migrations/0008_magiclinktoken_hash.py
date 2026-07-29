"""Le lien magique ne stocke plus que l'empreinte de son jeton.

En clair, la table était une réserve de connexions prêtes à l'emploi pour les
quinze minutes suivantes : un dump de base suffisait. C'est le même raisonnement
qui fait stocker les jetons d'application hachés.

Les lignes existantes sont converties (on a la valeur, on peut la hacher), puis
la colonne en clair disparaît.
"""
import hashlib

from django.db import migrations, models


def _hacher(apps, schema_editor):
    MagicLinkToken = apps.get_model("accounts", "MagicLinkToken")
    a_convertir = list(MagicLinkToken.objects.all())
    for jeton in a_convertir:
        jeton.token_hash = hashlib.sha256((jeton.token or "").encode("utf-8")).hexdigest()
    MagicLinkToken.objects.bulk_update(a_convertir, ["token_hash"], batch_size=200)


def _impossible(apps, schema_editor):
    """Sens inverse : on ne retrouve pas une valeur depuis son empreinte.

    Les liens en vol deviendraient invalides, ce qui est sans conséquence — ils
    durent quinze minutes. On vide donc la colonne restaurée plutôt que de
    prétendre reconstruire quoi que ce soit.
    """
    MagicLinkToken = apps.get_model("accounts", "MagicLinkToken")
    MagicLinkToken.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_user_bypass_granted_at_user_bypass_note_and_more"),
    ]

    operations = [
        # Aucun index à l'ajout, et `unique=True` seul à la fin : cumuler
        # `db_index=True` et `unique=True` fait créer deux fois l'index
        # `…_like` sur PostgreSQL, et la migration meurt sur
        # `relation "…_token_hash_…_like" already exists`. SQLite l'ignore, donc
        # la suite de tests aussi — ce lot l'a appris en déployant.
        migrations.AddField(
            model_name="magiclinktoken",
            name="token_hash",
            field=models.CharField(default="", max_length=64),
            preserve_default=False,
        ),
        migrations.RunPython(_hacher, _impossible),
        migrations.AlterField(
            model_name="magiclinktoken",
            name="token_hash",
            field=models.CharField(max_length=64, unique=True),
        ),
        migrations.RemoveField(
            model_name="magiclinktoken",
            name="token",
        ),
    ]
