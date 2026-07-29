"""Jetons d'émission : multiples, nommés, révocables un par un.

Ils remplacent l'usage « émission » du jeton historique. Contrairement à lui,
ils ne quittent jamais le serveur de leur propriétaire : ce qui part dans le QR
vers les destinataires est le code d'enrôlement, qui ne sait rien envoyer.

Deux représentations, deux usages, aucun superflu :

- `token_hash` — unique et indexé, sert à **retrouver et vérifier** le jeton à
  chaque requête. À sens unique.
- `secret_encrypted` — sert **uniquement** à te le remontrer dans la console.

Le second existe parce qu'on ne peut pas chercher dans du chiffré : le
chiffrement produit un résultat différent à chaque fois pour la même valeur,
donc rien à indexer. Et l'authentification ne déchiffre jamais — un défaut dans
la révélation ne peut pas affaiblir le contrôle d'accès.
"""
import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from . import crypto

# Un compte compromis ne doit pas pouvoir en fabriquer mille.
MAX_TOKENS_PER_APPLICATION = 10


class AppSendToken(models.Model):
    application = models.ForeignKey(
        "applications.Application", on_delete=models.CASCADE, related_name="send_tokens"
    )
    name = models.CharField(max_length=60, help_text="À quoi il sert : « serveur-prod ».")
    prefix = models.CharField(max_length=12, db_index=True)
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    secret_encrypted = models.BinaryField(blank=True, default=b"")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.application_id}:{self.name} ({self.prefix}…)"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    @staticmethod
    def generate_raw() -> str:
        """22 caractères base62 après le préfixe, soit 128 bits.

        Deux fois plus court que l'hexadécimal historique, pour une résistance
        très au-delà de ce qu'une attaque en ligne peut approcher.
        """
        alphabet = "".join(
            secrets.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
            for _ in range(22)
        )
        return f"apt_{alphabet}"

    @staticmethod
    def hash_raw(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def issue(cls, application, name: str) -> tuple["AppSendToken", str]:
        """Crée un jeton et renvoie (instance, valeur brute).

        La valeur brute ne repasse jamais par ici ensuite : pour la revoir, il
        faut la révélation, qui déchiffre et se journalise.
        """
        raw = cls.generate_raw()
        jeton = cls.objects.create(
            application=application,
            name=name,
            # 8 caractères : de quoi reconnaître un jeton dans une liste sans en
            # exposer une part significative. Le raccourcissement du jeton oblige
            # à raccourcir le préfixe -- 12 sur 26 en montreraient presque la moitié.
            prefix=raw[:12],
            token_hash=cls.hash_raw(raw),
            secret_encrypted=crypto.encrypt(raw),
        )
        return jeton, raw

    def reveal(self) -> str:
        return crypto.decrypt(self.secret_encrypted)

    def revoke(self) -> None:
        """Immédiat : c'est le geste d'urgence quand un jeton a fuité."""
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.save(update_fields=["revoked_at"])


class SendTokenReveal(models.Model):
    """Journal des révélations.

    Un jeton relisible sans trace serait relisible sans qu'on le sache. Cette
    table répond à « qui l'a regardé, et quand » — la question qu'on se pose
    après coup, quand il est trop tard pour l'instrumenter.
    """

    token = models.ForeignKey(AppSendToken, on_delete=models.CASCADE, related_name="reveals")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    actor_label = models.CharField(max_length=254, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
