import pytest
import yaml
from rest_framework.test import APIClient


def _load_schema() -> dict:
    client = APIClient()
    response = client.get("/api/schema/")
    assert response.status_code == 200
    return yaml.safe_load(response.content)


def _assert_json_response_ref(operation: dict, status_code: str, component_name: str) -> None:
    response_schema = operation["responses"][status_code]["content"]["application/json"]["schema"]
    assert response_schema["$ref"] == f"#/components/schemas/{component_name}"


@pytest.mark.django_db
def test_accounts_validation_responses_are_documented_per_endpoint():
    schema = _load_schema()

    register_operation = schema["paths"]["/api/v1/auth/register/"]["post"]
    _assert_json_response_ref(register_operation, "400", "RegisterValidationErrorResponse")

    login_operation = schema["paths"]["/api/v1/auth/login/"]["post"]
    _assert_json_response_ref(login_operation, "200", "LoginResponse")
    _assert_json_response_ref(login_operation, "400", "LoginValidationErrorResponse")

    logout_operation = schema["paths"]["/api/v1/auth/logout/"]["post"]
    _assert_json_response_ref(logout_operation, "400", "LogoutValidationErrorResponse")

    refresh_operation = schema["paths"]["/api/v1/auth/refresh/"]["post"]
    _assert_json_response_ref(refresh_operation, "200", "TokenRefreshResponse")
    _assert_json_response_ref(refresh_operation, "400", "TokenRefreshValidationErrorResponse")


@pytest.mark.django_db
def test_application_and_device_validation_responses_are_documented_per_endpoint():
    schema = _load_schema()

    application_create_operation = schema["paths"]["/api/v1/apps/"]["post"]
    _assert_json_response_ref(
        application_create_operation,
        "400",
        "ApplicationCreateValidationErrorResponse",
    )

    device_put_operation = schema["paths"]["/api/v1/devices/{id}/"]["put"]
    _assert_json_response_ref(device_put_operation, "400", "DeviceUpdateValidationErrorResponse")

    device_patch_operation = schema["paths"]["/api/v1/devices/{id}/"]["patch"]
    _assert_json_response_ref(device_patch_operation, "400", "DeviceUpdateValidationErrorResponse")

    device_link_operation = schema["paths"]["/api/v1/devices/link/"]["post"]
    _assert_json_response_ref(
        device_link_operation,
        "400",
        "DeviceLinkWithAppTokenValidationErrorResponse",
    )
    assert {"AppTokenAuth": []} in device_link_operation["security"]


@pytest.mark.django_db
def test_notification_validation_and_error_responses_are_documented_per_endpoint():
    schema = _load_schema()

    notification_create_operation = schema["paths"]["/api/v1/notifications/"]["post"]
    _assert_json_response_ref(
        notification_create_operation,
        "400",
        "NotificationCreateValidationErrorResponse",
    )

    notification_app_create_operation = schema["paths"]["/api/v1/notifications/app/create/"]["post"]
    _assert_json_response_ref(
        notification_app_create_operation,
        "400",
        "NotificationCreateWithAppTokenValidationErrorResponse",
    )
    assert set(["200", "201", "400", "401", "403", "409"]).issubset(
        notification_app_create_operation["responses"].keys()
    )
    assert {"AppTokenAuth": []} in notification_app_create_operation["security"]

    notification_send_operation = schema["paths"]["/api/v1/notifications/{notification_id}/send/"]["post"]
    assert set(["202", "404", "409"]).issubset(notification_send_operation["responses"].keys())
    assert {"BearerAuth": []} in notification_send_operation["security"]
