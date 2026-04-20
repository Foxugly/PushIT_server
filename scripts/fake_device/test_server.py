import json
from pathlib import Path

import pytest

from scripts.fake_device.server import create_app, load_config


VALID_CONFIG = {
    "apiKey": "AIzaTEST",
    "authDomain": "pushit-dcf8a.firebaseapp.com",
    "projectId": "pushit-dcf8a",
    "messagingSenderId": "1234567890",
    "appId": "1:1234567890:web:abc",
    "vapidKey": "BTESTVAPIDKEY",
}


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(VALID_CONFIG))
    return path


@pytest.fixture
def client(config_file: Path):
    app = create_app(config_path=config_file, api_base="http://127.0.0.1:8000/api/v1")
    app.config["TESTING"] = True
    return app.test_client()


def test_load_config_returns_dict(config_file: Path):
    config = load_config(config_file)
    assert config["projectId"] == "pushit-dcf8a"
    assert config["vapidKey"] == "BTESTVAPIDKEY"


def test_load_config_missing_file_exits(tmp_path: Path):
    missing = tmp_path / "nope.json"
    with pytest.raises(SystemExit) as exc_info:
        load_config(missing)
    assert exc_info.value.code == 1


def test_load_config_malformed_json_exits(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    with pytest.raises(SystemExit) as exc_info:
        load_config(bad)
    assert exc_info.value.code == 1


def test_get_index_returns_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.content_type.startswith("text/html")


def test_get_firebase_config_returns_json(client):
    response = client.get("/firebase-config.json")
    assert response.status_code == 200
    assert response.is_json
    body = response.get_json()
    assert body["projectId"] == "pushit-dcf8a"


def test_get_firebase_config_includes_api_base(client):
    response = client.get("/firebase-config.json")
    body = response.get_json()
    assert body["_apiBase"] == "http://127.0.0.1:8000/api/v1"


def test_service_worker_served_from_root(client):
    response = client.get("/firebase-messaging-sw.js")
    assert response.status_code == 200
    assert response.content_type.startswith(("application/javascript", "text/javascript"))


def test_post_received_stores_notification(client):
    payload = {
        "title": "Hello",
        "body": "World",
        "data": {"foo": "bar"},
        "received_at": "2026-04-20T10:00:00Z",
        "mode": "foreground",
    }
    post = client.post("/received", json=payload)
    assert post.status_code == 201

    get = client.get("/received")
    assert get.status_code == 200
    items = get.get_json()
    assert len(items) == 1
    assert items[0]["title"] == "Hello"
    assert items[0]["mode"] == "foreground"


def test_post_received_rejects_payload_without_title(client):
    response = client.post("/received", json={"body": "no title"})
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_post_received_rejects_non_json(client):
    response = client.post("/received", data="not json", content_type="text/plain")
    assert response.status_code == 400


def test_delete_received_clears_list(client):
    client.post(
        "/received",
        json={"title": "A", "body": "B", "data": {}, "received_at": "t", "mode": "foreground"},
    )
    delete = client.delete("/received")
    assert delete.status_code == 204
    get = client.get("/received")
    assert get.get_json() == []
