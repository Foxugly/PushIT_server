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

    me_patch_operation = schema["paths"]["/api/v1/auth/me/"]["patch"]
    _assert_json_response_ref(me_patch_operation, "400", "LanguageUpdateValidationErrorResponse")


@pytest.mark.django_db
def test_application_and_device_validation_responses_are_documented_per_endpoint():
    schema = _load_schema()

    application_create_operation = schema["paths"]["/api/v1/apps/"]["post"]
    _assert_json_response_ref(
        application_create_operation,
        "400",
        "ApplicationCreateValidationErrorResponse",
    )

    application_patch_operation = schema["paths"]["/api/v1/apps/{app_id}/"]["patch"]
    _assert_json_response_ref(
        application_patch_operation,
        "400",
        "ApplicationUpdateValidationErrorResponse",
    )

    quiet_period_create_operation = schema["paths"]["/api/v1/apps/{app_id}/quiet-periods/"]["post"]
    _assert_json_response_ref(
        quiet_period_create_operation,
        "400",
        "ApplicationQuietPeriodValidationErrorResponse",
    )

    device_put_operation = schema["paths"]["/api/v1/devices/{id}/"]["put"]
    _assert_json_response_ref(device_put_operation, "400", "DeviceUpdateValidationErrorResponse")

    device_patch_operation = schema["paths"]["/api/v1/devices/{id}/"]["patch"]
    _assert_json_response_ref(device_patch_operation, "400", "DeviceUpdateValidationErrorResponse")

    device_quiet_period_create_operation = schema["paths"]["/api/v1/devices/{device_id}/quiet-periods/"]["post"]
    _assert_json_response_ref(
        device_quiet_period_create_operation,
        "400",
        "DeviceQuietPeriodValidationErrorResponse",
    )

    device_link_operation = schema["paths"]["/api/v1/devices/link/"]["post"]
    _assert_json_response_ref(
        device_link_operation,
        "400",
        "DeviceLinkWithAppTokenValidationErrorResponse",
    )
    assert {"AppTokenAuth": []} in device_link_operation["security"]


@pytest.mark.django_db
def test_application_read_schema_exposes_inbound_email_fields():
    schema = _load_schema()

    application_read_schema = schema["components"]["schemas"]["ApplicationRead"]
    assert "inbound_email_alias" in application_read_schema["properties"]
    assert "inbound_email_address" in application_read_schema["properties"]


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
    assert set(["202", "404", "409", "503"]).issubset(notification_send_operation["responses"].keys())
    assert {"BearerAuth": []} in notification_send_operation["security"]

    notification_future_patch_operation = schema["paths"]["/api/v1/notifications/future/{id}/"]["patch"]
    _assert_json_response_ref(
        notification_future_patch_operation,
        "400",
        "NotificationFutureUpdateValidationErrorResponse",
    )


@pytest.mark.django_db
def test_notification_read_schema_exposes_effective_scheduled_for():
    schema = _load_schema()

    notification_read_schema = schema["components"]["schemas"]["NotificationRead"]
    assert "effective_scheduled_for" in notification_read_schema["properties"]
    assert notification_read_schema["properties"]["effective_scheduled_for"]["format"] == "date-time"
    assert "device_ids" in notification_read_schema["properties"]
    assert notification_read_schema["properties"]["device_ids"]["type"] == "array"


@pytest.mark.django_db
def test_notification_future_list_documents_effective_scheduled_filters():
    schema = _load_schema()

    notification_list_operation = schema["paths"]["/api/v1/notifications/"]["get"]
    notification_parameter_names = {parameter["name"] for parameter in notification_list_operation["parameters"]}
    assert {
        "application_id",
        "status",
        "effective_scheduled_from",
        "effective_scheduled_to",
        "has_quiet_period_shift",
        "ordering",
    }.issubset(notification_parameter_names)
    assert "400" in notification_list_operation["responses"]

    future_list_operation = schema["paths"]["/api/v1/notifications/future/"]["get"]
    parameter_names = {parameter["name"] for parameter in future_list_operation["parameters"]}
    assert {
        "effective_scheduled_from",
        "effective_scheduled_to",
        "has_quiet_period_shift",
        "ordering",
    }.issubset(parameter_names)
    assert "400" in future_list_operation["responses"]

    app_list_operation = schema["paths"]["/api/v1/notifications/app/"]["get"]
    app_parameter_names = {parameter["name"] for parameter in app_list_operation["parameters"]}
    assert {
        "status",
        "effective_scheduled_from",
        "effective_scheduled_to",
        "has_quiet_period_shift",
        "ordering",
    }.issubset(app_parameter_names)
    assert "400" in app_list_operation["responses"]


@pytest.mark.django_db
def test_notification_list_endpoints_document_shift_and_order_examples():
    schema = _load_schema()

    notification_list_examples = schema["paths"]["/api/v1/notifications/"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["examples"]
    assert len(notification_list_examples) >= 2

    future_list_examples = schema["paths"]["/api/v1/notifications/future/"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["examples"]
    assert len(future_list_examples) >= 2

    app_list_examples = schema["paths"]["/api/v1/notifications/app/"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["examples"]
    assert len(app_list_examples) >= 2
