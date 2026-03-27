import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models


def generate_userkey():
    return f"usr_{uuid.uuid4().hex[:12]}"


class UserLanguage(models.TextChoices):
    FR = "FR", "French"
    NL = "NL", "Dutch"
    EN = "EN", "English"


class User(AbstractUser):
    email = models.EmailField(unique=True)
    userkey = models.CharField(max_length=16, unique=True, default=generate_userkey, db_index=True)
    language = models.CharField(max_length=2, choices=UserLanguage.choices, default=UserLanguage.FR)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    def __str__(self):
        return f"{self.username} ({self.userkey})"
