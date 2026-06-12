from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Email-only user admin (the model has no ``username``)."""

    ordering = ("email",)
    list_display = ("email", "userkey", "language", "is_staff", "is_active")
    list_filter = ("is_staff", "is_superuser", "is_active", "language")
    search_fields = ("email", "userkey")
    readonly_fields = ("userkey", "last_login", "date_joined")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("first_name", "last_name", "userkey", "language")}),
        ("Permissions", {
            "fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions"),
        }),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "password1", "password2"),
        }),
    )
