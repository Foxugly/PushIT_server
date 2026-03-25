import logging
import uuid

from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler

from config.logging_utils import get_request_id, set_incident_id


logger = logging.getLogger("pushit.api")


def _get_safe_request_user(request):
    user = getattr(request, "_user", None)
    if user is not None:
        return user

    try:
        return getattr(request, "user", None)
    except Exception:
        return None


def _extract_detail_and_code(exc, response):
    exc_codes = exc.get_codes() if hasattr(exc, "get_codes") else None

    if isinstance(exc, exceptions.Throttled):
        return "throttled", str(exc.detail)
    if isinstance(exc, exceptions.ValidationError):
        return "validation_error", "Validation error."
    if isinstance(exc, exceptions.NotAuthenticated):
        return str(exc_codes or "not_authenticated"), str(exc.detail)
    if isinstance(exc, exceptions.AuthenticationFailed):
        return str(exc_codes or "authentication_failed"), str(exc.detail)
    if isinstance(exc, exceptions.PermissionDenied):
        return str(exc_codes or "permission_denied"), str(exc.detail)
    if isinstance(exc, exceptions.NotFound):
        return str(exc_codes or "not_found"), str(exc.detail)
    if isinstance(exc, exceptions.MethodNotAllowed):
        return str(exc_codes or "method_not_allowed"), str(exc.detail)
    detail = response.data.get("detail") if isinstance(response.data, dict) else None
    return "error", str(detail or "Request failed.")


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is None:
        request = context.get("request")
        request_id = getattr(request, "request_id", get_request_id())
        incident_id = f"inc_{uuid.uuid4().hex[:12]}"
        set_incident_id(incident_id)

        extra = {
            "incident_id": incident_id,
            "request_id": request_id,
            "error_code": "internal_error",
        }
        if request is not None:
            extra["path"] = getattr(request, "path", "")
            extra["method"] = getattr(request, "method", "")

            auth_application = getattr(request, "auth_application", None)
            if auth_application is not None:
                extra["application_id"] = auth_application.id

            user = _get_safe_request_user(request)
            if getattr(user, "is_authenticated", False) and hasattr(user, "id"):
                extra["user_id"] = user.id

        logger.exception("Unhandled API exception", exc_info=exc, extra=extra)
        return Response(
            {
                "code": "internal_error",
                "detail": "Internal server error.",
                "incident_id": incident_id,
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    code, detail = _extract_detail_and_code(exc, response)

    if isinstance(exc, exceptions.ValidationError):
        response.data = {
            "code": code,
            "detail": detail,
            "errors": response.data,
        }
        return response

    response.data = {
        "code": code,
        "detail": detail,
    }
    return response
