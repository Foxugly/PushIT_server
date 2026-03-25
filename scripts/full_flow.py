import json
import os
import sys
from typing import Any

import requests


BASE_URL = os.environ.get("PUSHIT_BASE_URL", "http://127.0.0.1:8000/api/v1")

EMAIL = "demo@example.com"
USERNAME = "demo_user"
PASSWORD = "MotDePasseTresSolide123!"

APP_NAME = "Demo Push App"

DEVICE_NAME = "Samsung S24"
DEVICE_PLATFORM = "android"
DEVICE_PUSH_TOKEN = "token_123456789012345678901234567890"

NOTIFICATION_TITLE = "Bonjour"
NOTIFICATION_MESSAGE = "Ceci est une notification de test."


def pretty(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


def post(
    url: str,
    payload: dict | None = None,
    *,
    bearer_token: str | None = None,
    app_token: str | None = None,
) -> requests.Response:
    headers = {"Content-Type": "application/json"}

    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    if app_token:
        headers["X-App-Token"] = app_token

    response = requests.post(url, json=payload or {}, headers=headers, timeout=20)
    return response


def main() -> None:
    print("=== 1. Création du compte ===")
    register_url = f"{BASE_URL}/auth/register/"
    register_payload = {
        "email": EMAIL,
        "username": USERNAME,
        "password": PASSWORD,
    }

    register_response = post(register_url, register_payload)

    if register_response.status_code not in (201, 400):
        print("Erreur inattendue à la création du compte")
        print(register_response.status_code)
        print(register_response.text)
        sys.exit(1)

    if register_response.status_code == 201:
        print("Compte créé avec succès")
        print(pretty(register_response.json()))
    else:
        print("Compte non créé, probablement déjà existant")
        print(pretty(register_response.json()))

    print("\n=== 2. Connexion ===")
    login_url = f"{BASE_URL}/auth/login/"
    login_payload = {
        "email": EMAIL,
        "password": PASSWORD,
    }

    login_response = post(login_url, login_payload)

    if login_response.status_code != 200:
        print("Échec de connexion")
        print(login_response.status_code)
        print(login_response.text)
        sys.exit(1)

    login_data = login_response.json()
    access_token = login_data["access"]

    print("Connexion OK")
    print(pretty(login_data))

    print("\n=== 3. Création de l'application ===")
    create_app_url = f"{BASE_URL}/apps/"
    create_app_payload = {
        "name": APP_NAME,
    }

    create_app_response = post(
        create_app_url,
        create_app_payload,
        bearer_token=access_token,
    )

    if create_app_response.status_code != 201:
        print("Échec création application")
        print(create_app_response.status_code)
        print(create_app_response.text)
        sys.exit(1)

    app_data = create_app_response.json()
    app_id = app_data["id"]

    # Hypothèse: le token brut est renvoyé à la création de l'app
    # Adapte la clé si chez toi ce n'est pas "app_token"
    app_token = app_data.get("app_token")

    if not app_token:
        print("Application créée, mais aucun app_token brut n'a été trouvé dans la réponse.")
        print("Réponse reçue :")
        print(pretty(app_data))
        print("\n=== 3bis. Génération du token applicatif ===")
        regen_url = f"{BASE_URL}/apps/{app_id}/regenerate-token/"
        regen_response = post(
            regen_url,
            {},
            bearer_token=access_token,
        )

        if regen_response.status_code != 200:
            print("Échec régénération token app")
            print(regen_response.status_code)
            print(regen_response.text)
            sys.exit(1)

        regen_data = regen_response.json()
        app_token = regen_data["new_app_token"]

        print("Token applicatif généré")
        print(pretty(regen_data))

    print("Application créée")
    print(pretty(app_data))

    print("\n=== 4. Création / liaison du device via X-App-Token ===")
    link_device_url = f"{BASE_URL}/devices/link/"
    link_device_payload = {
        "device_name": DEVICE_NAME,
        "platform": DEVICE_PLATFORM,
        "push_token": DEVICE_PUSH_TOKEN,
    }

    link_device_response = post(
        link_device_url,
        link_device_payload,
        app_token=app_token,
    )

    if link_device_response.status_code != 200:
        print("Échec liaison device")
        print(link_device_response.status_code)
        print(link_device_response.text)
        sys.exit(1)

    device_data = link_device_response.json()
    print("Device lié")
    print(pretty(device_data))

    print("\n=== 5. Création de la notification ===")
    create_notification_url = f"{BASE_URL}/notifications/"
    create_notification_payload = {
        "application_id": app_id,
        "title": NOTIFICATION_TITLE,
        "message": NOTIFICATION_MESSAGE,
    }

    create_notification_response = post(
        create_notification_url,
        create_notification_payload,
        bearer_token=access_token,
    )

    if create_notification_response.status_code != 201:
        print("Échec création notification")
        print(create_notification_response.status_code)
        print(create_notification_response.text)
        sys.exit(1)

    notification_data = create_notification_response.json()
    notification_id = notification_data["id"]

    print("Notification créée")
    print(pretty(notification_data))

    print("\n=== 6. Envoi de la notification ===")
    send_notification_url = f"{BASE_URL}/notifications/{notification_id}/send/"
    send_notification_response = post(
        send_notification_url,
        {},
        bearer_token=access_token,
    )

    if send_notification_response.status_code != 202:
        print("Échec mise en file de la notification")
        print(send_notification_response.status_code)
        print(send_notification_response.text)
        sys.exit(1)

    send_data = send_notification_response.json()
    print("Notification mise en file")
    print(pretty(send_data))

    print("\n=== TERMINÉ ===")
    print(f"Compte      : {EMAIL}")
    print(f"Application : {app_id}")
    print(f"Notification: {notification_id}")


if __name__ == "__main__":
    main()
