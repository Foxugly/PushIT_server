"""Self-service account erasure (GDPR art. 17) behind DELETE /api/v1/auth/me/."""
import logging

from django.db import transaction

logger = logging.getLogger(__name__)


def delete_user_account(user) -> None:
    """Erase ``user`` and everything that only exists because of them.

    - every refresh token issued to the user is blacklisted (OutstandingToken
      survives the user with ``user=NULL``, so the blacklist row still bites);
    - owned applications are deleted **even when other users' devices are
      subscribed to them**: their notifications, deliveries, templates, quiet
      periods, send tokens and device links cascade; their inbound-email
      journal rows (sender addresses) are deleted rather than orphaned;
    - the user's own devices (and their push tokens) are deleted instead of
      being left behind by ``Device.user`` ``SET_NULL``;
    - side effects outside the DB (Exchange aliases, notification images)
      only run once the transaction has committed.
    """
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    from applications.models import Application
    from devices.models import Device
    from notifications.models import InboundEmailIngestionLog, Notification

    user_id = user.pk
    with transaction.atomic():
        for token in OutstandingToken.objects.filter(user=user):
            BlacklistedToken.objects.get_or_create(token=token)

        apps = list(Application.objects.filter(owner=user))
        aliases = [app.inbound_email_address for app in apps if app.inbound_email_alias]
        images = list(
            Notification.objects.filter(application__in=apps)
            .exclude(image="")
            .exclude(image__isnull=True)
            .values_list("image", flat=True)
        )

        InboundEmailIngestionLog.objects.filter(application__in=apps).delete()
        Device.objects.filter(user=user).delete()
        # Bulk cascade from the user row: Application.delete() is NOT called,
        # so the alias deprovisioning it would do is replayed on commit below.
        user.delete()

        transaction.on_commit(lambda: _after_commit(aliases, images))

    logger.info("account_deleted", extra={"user_id": user_id, "applications_deleted": len(apps)})


def _after_commit(aliases, images) -> None:
    from exchange.integration import deprovision_alias_for_application
    from notifications.retention import delete_notification_image_files

    for alias in aliases:
        deprovision_alias_for_application(alias)
    delete_notification_image_files(images)
