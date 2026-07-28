from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import BypassGrantLog, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Email-only user admin (the model has no ``username``)."""

    ordering = ("email",)
    list_display = ("email", "userkey", "language", "is_staff", "is_active", "subscription_bypass")
    list_filter = ("subscription_bypass", "is_staff", "is_superuser", "is_active", "language")
    search_fields = ("email", "userkey")
    readonly_fields = ("userkey", "last_login", "date_joined")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("first_name", "last_name", "userkey", "language")}),
        ("Billing", {"fields": ("subscription_bypass", "bypass_note", "bypass_granted_at")}),
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


@admin.register(BypassGrantLog)
class BypassGrantLogAdmin(admin.ModelAdmin):
    """Journal append-only : consultable, jamais modifiable depuis l'admin."""

    list_display = ("created_at", "target", "target_label", "granted", "actor_label", "note")
    list_filter = ("granted", "created_at")
    search_fields = ("actor_label", "target_label", "target__email", "note")
    readonly_fields = ("actor", "actor_label", "target", "target_label", "granted", "note", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
