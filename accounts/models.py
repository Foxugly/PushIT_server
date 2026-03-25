import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models

def generate_userkey():
    return f"usr_{uuid.uuid4().hex[:12]}"

class User(AbstractUser):
    email = models.EmailField(unique=True)
    userkey = models.CharField(max_length=16, unique=True, default=generate_userkey, db_index=True)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    def __str__(self):
        return f"{self.username} ({self.userkey})"