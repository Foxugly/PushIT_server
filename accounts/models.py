import secrets
import uuid
from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone


def generate_userkey():
    return f"usr_{uuid.uuid4().hex[:12]}"


def generate_magic_token() -> str:
    return secrets.token_urlsafe(32)


class UserLanguage(models.TextChoices):
    FR = "FR", "French"
    NL = "NL", "Dutch"
    EN = "EN", "English"
    IT = "IT", "Italian"
    ES = "ES", "Spanish"


class UserManager(BaseUserManager):
    """Email-based manager. The default ``UserManager`` keys off ``username``,
    which this model no longer has, so ``createsuperuser`` and programmatic
    user creation must go through email instead."""

    use_in_migrations = True

    def _create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("The email must be set.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    email = models.EmailField(unique=True)
    userkey = models.CharField(max_length=16, unique=True, default=generate_userkey, db_index=True)
    language = models.CharField(max_length=2, choices=UserLanguage.choices, default=UserLanguage.FR)
    # Email ownership gate: registration creates the user with this False and
    # sends a confirmation link; login is refused until it is confirmed. Existing
    # accounts are backfilled to True by the data migration so they aren't locked out.
    email_confirmed = models.BooleanField(default=False)
    # Acces offert : le compte a tous les droits payants sans souscription.
    # Distinct de is_staff, qui n'accorde AUCUN droit metier. Lu et court-circuite
    # dans billing/service.py (user_is_paid / application_quota), nulle part ailleurs.
    subscription_bypass = models.BooleanField(default=False)
    # Audit seul, aucun effet fonctionnel : pourquoi et quand l'acces a ete offert.
    bypass_note = models.CharField(max_length=200, blank=True)
    bypass_granted_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return f"{self.email} ({self.userkey})"


class MagicLinkToken(models.Model):
    """Single-use, short-TTL passwordless login token keyed on a user.

    The raw token travels only in the emailed link; verification consumes it
    (sets ``used_at``) and issues the JWT pair like a normal login. Mirrors the
    fleet magic-link pattern (Poker_server). Unlike the stateless password-reset
    tokens, this is a dedicated row so single-use can be enforced server-side."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="magic_links"
    )
    token = models.CharField(max_length=64, unique=True, default=generate_magic_token)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_valid(self) -> bool:
        return self.used_at is None and self.expires_at > timezone.now()

    def consume(self) -> None:
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])


class BypassGrantLog(models.Model):
    """Journal append-only des octrois et revocations d'acces offert.

    Le User porte l'ETAT courant (`subscription_bypass`, `bypass_note`,
    `bypass_granted_at`) ; ce modele porte l'HISTOIRE, dont l'acteur, que l'etat
    courant ne dit pas. Jamais modifie ni supprime : une revocation ajoute une
    ligne, elle n'en efface aucune -- offrir l'acces est un geste commercial, on
    doit pouvoir dire qui l'a fait et quand.

    Transpose de Poker_server, ou le meme journal existe.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="bypass_grants_made",
    )
    # Snapshot de l'email : la trace survit a la suppression du compte staff.
    actor_label = models.CharField(max_length=254, blank=True)
    target = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="bypass_grants_received",
    )
    # Snapshot de l'email : la trace survit a la suppression du compte cible.
    target_label = models.CharField(max_length=254, blank=True)
    granted = models.BooleanField()  # True = octroi, False = revocation
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        verbe = "grant" if self.granted else "revoke"
        return f"{verbe} {self.target_label} by {self.actor_label}"
