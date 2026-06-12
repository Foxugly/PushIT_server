import uuid
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


def generate_userkey():
    return f"usr_{uuid.uuid4().hex[:12]}"


class UserLanguage(models.TextChoices):
    FR = "FR", "French"
    NL = "NL", "Dutch"
    EN = "EN", "English"


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

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return f"{self.email} ({self.userkey})"
