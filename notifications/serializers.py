from drf_spectacular.utils import extend_schema_field, inline_serializer
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from rest_framework import serializers

from accounts.models import User
from applications.models import Application
from config.api_errors import (
    ErrorResponseSerializer as DetailResponseSerializer,
    build_validation_error_serializer,
)
from devices.models import Device, DeviceTokenStatus
from .inbound_email import extract_subject_schedule
from .models import DeliveryStatus, Notification, NotificationDelivery, NotificationStatus
from .scheduling import compute_effective_scheduled_for

NotificationQueuedResponseSerializer = inline_serializer(
    name="NotificationQueuedResponse",
    fields={
        "status": serializers.CharField(),
        "notification_id": serializers.IntegerField(),
        "task_id": serializers.CharField(),
    },
)

NotificationCreateValidationErrorResponseSerializer = build_validation_error_serializer(
    "NotificationCreateValidationErrorResponse",
    ["application_id", "device_ids", "title", "message", "scheduled_for"],
)

NotificationCreateWithAppTokenValidationErrorResponseSerializer = build_validation_error_serializer(
    "NotificationCreateWithAppTokenValidationErrorResponse",
    ["title", "message", "scheduled_for"],
)

NotificationInboundEmailValidationErrorResponseSerializer = build_validation_error_serializer(
    "NotificationInboundEmailValidationErrorResponse",
    ["sender", "recipient", "subject", "text", "message_id"],
)

NotificationFutureUpdateValidationErrorResponseSerializer = build_validation_error_serializer(
    "NotificationFutureUpdateValidationErrorResponse",
    ["title", "message", "scheduled_for"],
)

NotificationFutureFilterValidationErrorResponseSerializer = build_validation_error_serializer(
    "NotificationFutureFilterValidationErrorResponse",
    ["effective_scheduled_from", "effective_scheduled_to", "ordering", "has_quiet_period_shift"],
)


class NotificationFutureFilterSerializer(serializers.Serializer):
    effective_scheduled_from = serializers.DateTimeField(required=False)
    effective_scheduled_to = serializers.DateTimeField(required=False)
    has_quiet_period_shift = serializers.BooleanField(required=False)
    ordering = serializers.ChoiceField(
        required=False,
        choices=["effective_scheduled_for", "-effective_scheduled_for"],
    )

    def validate(self, attrs):
        effective_scheduled_from = attrs.get("effective_scheduled_from")
        effective_scheduled_to = attrs.get("effective_scheduled_to")

        if (
            effective_scheduled_from is not None
            and effective_scheduled_to is not None
            and effective_scheduled_to < effective_scheduled_from
        ):
            raise serializers.ValidationError(
                {
                    "effective_scheduled_to": [
                        "La borne de fin doit etre apres ou egale a la borne de debut."
                    ]
                }
            )

        return attrs


NotificationListFilterValidationErrorResponseSerializer = build_validation_error_serializer(
    "NotificationListFilterValidationErrorResponse",
    [
        "application_id",
        "status",
        "effective_scheduled_from",
        "effective_scheduled_to",
        "has_quiet_period_shift",
        "ordering",
    ],
)


class NotificationListFilterSerializer(NotificationFutureFilterSerializer):
    application_id = serializers.IntegerField(required=False)
    status = serializers.ChoiceField(
        required=False,
        choices=[choice for choice, _ in NotificationStatus.choices],
    )


class NotificationReadSerializer(serializers.ModelSerializer):
    application_id = serializers.IntegerField(source="application.id", read_only=True)
    application_name = serializers.CharField(source="application.name", read_only=True)
    device_ids = serializers.SerializerMethodField()
    effective_scheduled_for = serializers.SerializerMethodField(
        help_text=(
            "Date effective d'envoi calculee a partir de `scheduled_for` et des "
            "periodes blanches actuellement configurees. Cette valeur peut changer "
            "si les periodes blanches changent, meme sans modifier `scheduled_for`."
        )
    )

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_effective_scheduled_for(self, obj):
        quiet_periods = getattr(obj.application, "_prefetched_objects_cache", {}).get("quiet_periods")
        return compute_effective_scheduled_for(
            obj.application,
            obj.scheduled_for,
            quiet_periods=quiet_periods,
        )

    @extend_schema_field(serializers.ListField(child=serializers.IntegerField()))
    def get_device_ids(self, obj):
        deliveries = getattr(obj, "_prefetched_objects_cache", {}).get("deliveries")
        if deliveries is not None:
            return sorted(delivery.device_id for delivery in deliveries)
        return list(obj.deliveries.order_by("device_id").values_list("device_id", flat=True))

    class Meta:
        model = Notification
        fields = [
            "id",
            "application_id",
            "application_name",
            "device_ids",
            "title",
            "message",
            "status",
            "created_at",
            "scheduled_for",
            "effective_scheduled_for",
            "sent_at",
        ]


class BaseNotificationWriteSerializer(serializers.ModelSerializer):
    title = serializers.CharField(max_length=255, trim_whitespace=True)
    message = serializers.CharField(trim_whitespace=True)
    scheduled_for = serializers.DateTimeField(required=False, allow_null=True)

    def validate_title(self, value):
        if not value.strip():
            raise serializers.ValidationError("Le titre ne peut pas être vide.")
        return value

    def validate_message(self, value):
        if not value.strip():
            raise serializers.ValidationError("Le message ne peut pas être vide.")
        if len(value.strip()) > 5000:
            raise serializers.ValidationError("Le message est trop long.")
        return value

    def validate_scheduled_for(self, value):
        if value is None:
            return value
        if value <= timezone.now():
            raise serializers.ValidationError("La date planifiée doit être dans le futur.")
        return value

    @staticmethod
    def build_status_from_scheduled_for(scheduled_for):
        if scheduled_for is not None:
            return NotificationStatus.SCHEDULED
        return NotificationStatus.DRAFT


class NotificationCreateSerializer(BaseNotificationWriteSerializer):
    application_id = serializers.IntegerField(write_only=True)
    device_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        write_only=True,
        allow_empty=False,
    )

    class Meta:
        model = Notification
        fields = [
            "id",
            "application_id",
            "device_ids",
            "title",
            "message",
            "status",
            "created_at",
            "scheduled_for",
            "sent_at",
        ]
        read_only_fields = ["id", "status", "created_at", "sent_at"]

    def validate(self, attrs):
        request = self.context["request"]
        application_id = attrs["application_id"]
        device_ids = attrs["device_ids"]

        try:
            app = Application.objects.get(id=application_id, owner=request.user)
        except Application.DoesNotExist:
            raise serializers.ValidationError({"application_id": ["Application introuvable."]})

        normalized_device_ids = list(dict.fromkeys(device_ids))
        devices_by_id = {
            device.id: device
            for device in Device.objects.filter(
                id__in=normalized_device_ids,
                application_links__application=app,
                application_links__is_active=True,
                push_token_status=DeviceTokenStatus.ACTIVE,
                is_active=True,
            ).distinct()
        }
        invalid_device_ids = [device_id for device_id in normalized_device_ids if device_id not in devices_by_id]
        if invalid_device_ids:
            raise serializers.ValidationError(
                {
                    "device_ids": [
                        "Tous les devices doivent etre actifs et lies a l'application selectionnee."
                    ]
                }
            )

        self.context["application"] = app
        self.context["target_devices"] = devices_by_id
        attrs["device_ids"] = normalized_device_ids
        return attrs

    def create(self, validated_data):
        validated_data.pop("application_id")
        device_ids = validated_data.pop("device_ids")
        application = self.context["application"]
        target_devices = self.context["target_devices"]

        with transaction.atomic():
            notification = Notification.objects.create(
                application=application,
                title=validated_data["title"],
                message=validated_data["message"],
                status=self.build_status_from_scheduled_for(validated_data.get("scheduled_for")),
                scheduled_for=validated_data.get("scheduled_for"),
            )
            NotificationDelivery.objects.bulk_create(
                [
                    NotificationDelivery(
                        notification=notification,
                        device=target_devices[device_id],
                        status=DeliveryStatus.PENDING,
                    )
                    for device_id in device_ids
                ]
            )
        return notification


class NotificationCreateWithAppTokenSerializer(BaseNotificationWriteSerializer):
    class Meta:
        model = Notification
        fields = [
            "id",
            "title",
            "message",
            "status",
            "created_at",
            "scheduled_for",
            "sent_at",
        ]
        read_only_fields = ["id", "status", "created_at", "sent_at"]

    def create(self, validated_data):
        """
        La création réelle est gérée dans :
        notifications/api_views_app_token.py
        -> NotificationCreateWithAppTokenApiView.post()

        Ce serializer reste responsable de la validation.
        """
        raise NotImplementedError(
            "La création doit être effectuée dans NotificationCreateWithAppTokenApiView.post()."
        )


class NotificationInboundEmailSerializer(serializers.Serializer):
    sender = serializers.EmailField()
    recipient = serializers.EmailField()
    subject = serializers.CharField(max_length=255, trim_whitespace=True)
    text = serializers.CharField(trim_whitespace=True)
    message_id = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        trim_whitespace=True,
    )

    def validate(self, attrs):
        errors = {}

        sender = attrs["sender"].strip().lower()
        recipient = attrs["recipient"].strip().lower()
        subject = attrs["subject"]
        text = attrs["text"]

        try:
            local_part, domain = recipient.split("@", 1)
        except ValueError:
            raise serializers.ValidationError({"recipient": ["Adresse email invalide."]})

        expected_domain = settings.INBOUND_EMAIL_DOMAIN.strip().lower()
        if domain != expected_domain:
            errors["recipient"] = [f"Le domaine entrant doit etre {expected_domain}."]

        try:
            title, scheduled_for = extract_subject_schedule(subject)
        except ValueError as exc:
            errors["subject"] = [str(exc)]
            title = ""
            scheduled_for = None

        if not title:
            errors.setdefault("subject", []).append("Le sujet ne peut pas etre vide.")

        if not text.strip():
            errors["text"] = ["Le contenu du mail ne peut pas etre vide."]
        elif len(text.strip()) > 5000:
            errors["text"] = ["Le contenu du mail est trop long."]

        if scheduled_for is not None and scheduled_for <= timezone.now():
            errors.setdefault("subject", []).append("La date planifiee doit etre dans le futur.")

        application = (
            Application.objects.filter(
                inbound_email_alias=local_part,
                is_active=True,
                revoked_at__isnull=True,
            )
            .order_by("id")
            .first()
        )
        if application is None:
            errors.setdefault("recipient", []).append("Aucune application ne correspond a cette adresse email.")

        user = User.objects.filter(email=sender).first()
        if user is None:
            errors.setdefault("sender", []).append("Aucun utilisateur ne correspond a cette adresse email.")
        elif application is not None and application.owner_id != user.id:
            errors.setdefault("sender", []).append(
                "L'expediteur doit correspondre au proprietaire de l'application ciblee."
            )

        if errors:
            raise serializers.ValidationError(errors)

        self.context["user"] = user
        self.context["normalized_sender"] = sender
        self.context["application"] = application
        self.context["normalized_recipient"] = recipient
        self.context["normalized_title"] = title
        self.context["scheduled_for"] = scheduled_for
        attrs["sender"] = sender
        attrs["recipient"] = recipient
        attrs["subject"] = title
        attrs["text"] = text.strip()
        attrs["message_id"] = attrs.get("message_id", "").strip()
        return attrs


class NotificationFutureUpdateSerializer(BaseNotificationWriteSerializer):
    class Meta:
        model = Notification
        fields = [
            "id",
            "title",
            "message",
            "scheduled_for",
            "status",
            "created_at",
            "sent_at",
        ]
        read_only_fields = ["id", "status", "created_at", "sent_at"]

    def update(self, instance, validated_data):
        for field in ["title", "message", "scheduled_for"]:
            if field in validated_data:
                setattr(instance, field, validated_data[field])
        instance.status = NotificationStatus.SCHEDULED
        instance.save(update_fields=["title", "message", "scheduled_for", "status"])
        return instance


class NotificationStatsSerializer(serializers.Serializer):
    status = serializers.CharField()
    count = serializers.IntegerField()
