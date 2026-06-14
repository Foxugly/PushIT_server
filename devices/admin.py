from django.contrib import admin
from .models import Device, DeviceApplicationLink


@admin.register(DeviceApplicationLink)
class DeviceApplicationLinkAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "device",
        "application",
        "is_active",
        "linked_at",
        "unlinked_at",
        "unlink_source",
    )
    list_filter = ("is_active", "unlink_source")
    search_fields = ("device__push_token", "device__device_name", "application__name")


admin.site.register(Device)
