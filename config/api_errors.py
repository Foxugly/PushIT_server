from drf_spectacular.utils import inline_serializer
from rest_framework import serializers, status
from rest_framework.response import Response


ErrorResponseSerializer = inline_serializer(
    name="ErrorResponse",
    fields={
        "code": serializers.CharField(),
        "detail": serializers.CharField(),
    },
)

ValidationErrorResponseSerializer = inline_serializer(
    name="ValidationErrorResponse",
    fields={
        "code": serializers.CharField(),
        "detail": serializers.CharField(),
        "errors": serializers.JSONField(),
    },
)


def build_validation_error_serializer(name: str, field_names: list[str]):
    error_fields = {
        field_name: serializers.ListField(
            child=serializers.CharField(),
            required=False,
        )
        for field_name in field_names
    }
    error_fields["non_field_errors"] = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )

    nested_errors = inline_serializer(
        name=f"{name}Fields",
        fields=error_fields,
    )

    return inline_serializer(
        name=name,
        fields={
            "code": serializers.CharField(),
            "detail": serializers.CharField(),
            "errors": nested_errors,
        },
    )


def error_response(*, code: str, detail: str, http_status: int = status.HTTP_400_BAD_REQUEST) -> Response:
    return Response(
        {
            "code": code,
            "detail": detail,
        },
        status=http_status,
    )
