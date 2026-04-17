import json
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import requests


BASE_URL = os.environ.get("PUSHIT_BASE_URL", "http://127.0.0.1:8000/api/v1")

EMAIL = "demo@example.com"
USERNAME = "demo_user"
PASSWORD = "VeryStr0ngPassword123!"

APP_NAME = "Demo Push App"

DEVICE_NAME = "Samsung S24"
DEVICE_PLATFORM = "android"
DEVICE_PUSH_TOKEN = "token_123456789012345678901234567890"

NOTIFICATION_TITLE = "Hello"
NOTIFICATION_MESSAGE = "This is a test notification."


def pretty(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


def build_headers(
    *,
    bearer_token: str | None = None,
    app_token: str | None = None,
) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}

    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    if app_token:
        headers["X-App-Token"] = app_token

    return headers


def request(
    method: str,
    url: str,
    payload: dict | None = None,
    *,
    params: dict[str, Any] | None = None,
    bearer_token: str | None = None,
    app_token: str | None = None,
) -> requests.Response:
    return requests.request(
        method=method,
        url=url,
        json=payload,
        params=params,
        headers=build_headers(bearer_token=bearer_token, app_token=app_token),
        timeout=60,
    )


def get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    bearer_token: str | None = None,
    app_token: str | None = None,
) -> requests.Response:
    return request(
        "GET",
        url,
        params=params,
        bearer_token=bearer_token,
        app_token=app_token,
    )


def post(
    url: str,
    payload: dict | None = None,
    *,
    bearer_token: str | None = None,
    app_token: str | None = None,
) -> requests.Response:
    return request("POST", url, payload, bearer_token=bearer_token, app_token=app_token)


def patch(
    url: str,
    payload: dict | None = None,
    *,
    bearer_token: str | None = None,
    app_token: str | None = None,
) -> requests.Response:
    return request("PATCH", url, payload, bearer_token=bearer_token, app_token=app_token)


def delete(
    url: str,
    *,
    bearer_token: str | None = None,
    app_token: str | None = None,
) -> requests.Response:
    return request("DELETE", url, bearer_token=bearer_token, app_token=app_token)


def dump_response(response: requests.Response) -> None:
    if not response.content:
        print("(empty body)")
        return

    try:
        print(pretty(response.json()))
    except ValueError:
        print(response.text)


def ensure_status(response: requests.Response, allowed_statuses: tuple[int, ...], step_name: str) -> None:
    if response.status_code in allowed_statuses:
        return

    print(f"Unexpected error during: {step_name}")
    print(response.status_code)
    dump_response(response)
    sys.exit(1)


def main() -> None:
    now = datetime.now(UTC)
    quiet_start = now + timedelta(days=1, hours=2)
    quiet_end = quiet_start + timedelta(hours=8)
    updated_quiet_end = quiet_end + timedelta(hours=1)
    scheduled_for = quiet_start + timedelta(minutes=30)
    rescheduled_for = quiet_start + timedelta(hours=1)

    print("=== 1. Register account ===")
    register_response = post(
        f"{BASE_URL}/auth/register/",
        {
            "email": EMAIL,
            "username": USERNAME,
            "password": PASSWORD,
        },
    )

    ensure_status(register_response, (201, 400), "register account")
    if register_response.status_code == 201:
        print("Account created")
    else:
        print("Account already exists")
    dump_response(register_response)

    print("\n=== 2. Login ===")
    login_response = post(
        f"{BASE_URL}/auth/login/",
        {
            "email": EMAIL,
            "password": PASSWORD,
        },
    )
    ensure_status(login_response, (200,), "login")
    login_data = login_response.json()
    access_token = login_data["access"]
    print("Login OK")
    dump_response(login_response)

    print("\n=== 3. Create application ===")
    create_app_response = post(
        f"{BASE_URL}/apps/",
        {"name": APP_NAME},
        bearer_token=access_token,
    )
    ensure_status(create_app_response, (201,), "create application")
    app_data = create_app_response.json()
    app_id = app_data["id"]
    app_token = app_data.get("app_token")
    print("Application created")
    dump_response(create_app_response)

    if not app_token:
        print("\n=== 3b. Regenerate app token ===")
        regenerate_response = post(
            f"{BASE_URL}/apps/{app_id}/regenerate-token/",
            {},
            bearer_token=access_token,
        )
        ensure_status(regenerate_response, (200,), "regenerate app token")
        app_token = regenerate_response.json()["new_app_token"]
        print("App token regenerated")
        dump_response(regenerate_response)

    print("\n=== 4. Link device with X-App-Token ===")
    link_device_response = post(
        f"{BASE_URL}/devices/link/",
        {
            "device_name": DEVICE_NAME,
            "platform": DEVICE_PLATFORM,
            "push_token": DEVICE_PUSH_TOKEN,
        },
        app_token=app_token,
    )
    ensure_status(link_device_response, (200,), "link device")
    link_data = link_device_response.json()
    device_id = link_data["device_id"]
    print("Device linked")
    dump_response(link_device_response)

    print("\n=== 5. Create quiet period ===")
    create_quiet_period_response = post(
        f"{BASE_URL}/apps/{app_id}/quiet-periods/",
        {
            "name": "Night silence",
            "start_at": quiet_start.isoformat(),
            "end_at": quiet_end.isoformat(),
            "is_active": True,
        },
        bearer_token=access_token,
    )
    ensure_status(create_quiet_period_response, (201,), "create quiet period")
    quiet_period_data = create_quiet_period_response.json()
    quiet_period_id = quiet_period_data["id"]
    print("Quiet period created")
    dump_response(create_quiet_period_response)

    print("\n=== 6. List quiet periods ===")
    list_quiet_periods_response = get(
        f"{BASE_URL}/apps/{app_id}/quiet-periods/",
        bearer_token=access_token,
    )
    ensure_status(list_quiet_periods_response, (200,), "list quiet periods")
    print("Quiet periods listed")
    dump_response(list_quiet_periods_response)

    print("\n=== 7. Update quiet period ===")
    update_quiet_period_response = patch(
        f"{BASE_URL}/apps/{app_id}/quiet-periods/{quiet_period_id}/",
        {
            "name": "Night silence updated",
            "end_at": updated_quiet_end.isoformat(),
        },
        bearer_token=access_token,
    )
    ensure_status(update_quiet_period_response, (200,), "update quiet period")
    print("Quiet period updated")
    dump_response(update_quiet_period_response)

    print("\n=== 8. Create scheduled notification ===")
    create_future_notification_response = post(
        f"{BASE_URL}/notifications/",
        {
            "application_id": app_id,
            "device_ids": [device_id],
            "title": "Reminder tonight",
            "message": "This notification is scheduled in advance.",
            "scheduled_for": scheduled_for.isoformat(),
        },
        bearer_token=access_token,
    )
    ensure_status(create_future_notification_response, (201,), "create scheduled notification")
    future_notification_data = create_future_notification_response.json()
    future_notification_id = future_notification_data["id"]
    print("Scheduled notification created")
    dump_response(create_future_notification_response)

    print("\n=== 9. List future notifications ===")
    list_future_notifications_response = get(
        f"{BASE_URL}/notifications/future/",
        bearer_token=access_token,
    )
    ensure_status(list_future_notifications_response, (200,), "list future notifications")
    print("Future notifications listed")
    dump_response(list_future_notifications_response)

    print("\n=== 10. List future notifications shifted by quiet period ===")
    list_shifted_future_notifications_response = get(
        f"{BASE_URL}/notifications/future/",
        params={"has_quiet_period_shift": "true"},
        bearer_token=access_token,
    )
    ensure_status(
        list_shifted_future_notifications_response,
        (200,),
        "list future notifications shifted by quiet period",
    )
    print("Shifted future notifications listed")
    dump_response(list_shifted_future_notifications_response)

    print("\n=== 11. List future notifications in effective date range ===")
    list_future_notifications_in_range_response = get(
        f"{BASE_URL}/notifications/future/",
        params={
            "effective_scheduled_from": quiet_start.isoformat(),
            "effective_scheduled_to": updated_quiet_end.isoformat(),
        },
        bearer_token=access_token,
    )
    ensure_status(
        list_future_notifications_in_range_response,
        (200,),
        "list future notifications in effective date range",
    )
    print("Future notifications filtered by effective date")
    dump_response(list_future_notifications_in_range_response)

    print("\n=== 12. List future notifications ordered by effective date desc ===")
    list_future_notifications_desc_response = get(
        f"{BASE_URL}/notifications/future/",
        params={"ordering": "-effective_scheduled_for"},
        bearer_token=access_token,
    )
    ensure_status(
        list_future_notifications_desc_response,
        (200,),
        "list future notifications ordered by effective date desc",
    )
    print("Future notifications ordered by effective date desc")
    dump_response(list_future_notifications_desc_response)

    print("\n=== 13. List all user notifications with advanced filters ===")
    list_user_notifications_response = get(
        f"{BASE_URL}/notifications/",
        params={
            "application_id": app_id,
            "status": "scheduled",
            "has_quiet_period_shift": "true",
            "ordering": "-effective_scheduled_for",
        },
        bearer_token=access_token,
    )
    ensure_status(
        list_user_notifications_response,
        (200,),
        "list user notifications with advanced filters",
    )
    print("User notifications filtered by application, status and quiet period shift")
    dump_response(list_user_notifications_response)

    print("\n=== 14. List app-token notifications with advanced filters ===")
    list_app_notifications_response = get(
        f"{BASE_URL}/notifications/app/",
        params={
            "status": "scheduled",
            "has_quiet_period_shift": "true",
            "ordering": "-effective_scheduled_for",
        },
        app_token=app_token,
    )
    ensure_status(
        list_app_notifications_response,
        (200,),
        "list app-token notifications with advanced filters",
    )
    print("App-token notifications filtered by status and quiet period shift")
    dump_response(list_app_notifications_response)

    print("\n=== 15. Get future notification detail ===")
    get_future_notification_response = get(
        f"{BASE_URL}/notifications/future/{future_notification_id}/",
        bearer_token=access_token,
    )
    ensure_status(get_future_notification_response, (200,), "get future notification detail")
    print("Future notification detail")
    dump_response(get_future_notification_response)

    print("\n=== 16. Update future notification ===")
    update_future_notification_response = patch(
        f"{BASE_URL}/notifications/future/{future_notification_id}/",
        {
            "title": "Reminder rescheduled",
            "scheduled_for": rescheduled_for.isoformat(),
        },
        bearer_token=access_token,
    )
    ensure_status(update_future_notification_response, (200,), "update future notification")
    print("Future notification updated")
    dump_response(update_future_notification_response)

    print("\n=== 17. Delete future notification ===")
    delete_future_notification_response = delete(
        f"{BASE_URL}/notifications/future/{future_notification_id}/",
        bearer_token=access_token,
    )
    ensure_status(delete_future_notification_response, (204,), "delete future notification")
    print("Future notification deleted")
    dump_response(delete_future_notification_response)

    print("\n=== 18. Delete quiet period ===")
    delete_quiet_period_response = delete(
        f"{BASE_URL}/apps/{app_id}/quiet-periods/{quiet_period_id}/",
        bearer_token=access_token,
    )
    ensure_status(delete_quiet_period_response, (204,), "delete quiet period")
    print("Quiet period deleted")
    dump_response(delete_quiet_period_response)

    print("\n=== 19. Create immediate notification ===")
    create_notification_response = post(
        f"{BASE_URL}/notifications/",
        {
            "application_id": app_id,
            "device_ids": [device_id],
            "title": NOTIFICATION_TITLE,
            "message": NOTIFICATION_MESSAGE,
        },
        bearer_token=access_token,
    )
    ensure_status(create_notification_response, (201,), "create immediate notification")
    notification_data = create_notification_response.json()
    notification_id = notification_data["id"]
    print("Immediate notification created")
    dump_response(create_notification_response)

    print("\n=== 20. Send immediate notification ===")
    send_notification_response = post(
        f"{BASE_URL}/notifications/{notification_id}/send/",
        {},
        bearer_token=access_token,
    )
    ensure_status(send_notification_response, (202,), "send immediate notification")
    print("Immediate notification queued")
    dump_response(send_notification_response)

    print("\n=== DONE ===")
    print(f"Account: {EMAIL}")
    print(f"Application: {app_id}")
    print(f"Quiet period: {quiet_period_id}")
    print(f"Immediate notification: {notification_id}")


if __name__ == "__main__":
    main()
