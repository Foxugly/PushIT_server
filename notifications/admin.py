from django.contrib import admin
from .models import Notification, NotificationDelivery

admin.site.register(Notification)
admin.site.register(NotificationDelivery)