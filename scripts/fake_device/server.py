"""Flask server for the PushIT fake device diagnostic tool."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, jsonify, request, send_from_directory


REQUIRED_CONFIG_KEYS = (
    "apiKey",
    "authDomain",
    "projectId",
    "messagingSenderId",
    "appId",
    "vapidKey",
)


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        sys.stderr.write(
            f"fake_device: config file not found at {path}. "
            f"Copy config.example.json to config.json and fill in your Firebase Web config.\n"
        )
        raise SystemExit(1)

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"fake_device: config file {path} is not valid JSON: {exc}\n")
        raise SystemExit(1) from exc

    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in data]
    if missing:
        sys.stderr.write(
            f"fake_device: config {path} is missing required keys: {', '.join(missing)}\n"
        )
        raise SystemExit(1)

    return data


def create_app(config_path: Path, api_base: str) -> Flask:
    here = Path(__file__).resolve().parent
    static_dir = here / "static"
    sw_path = here / "firebase-messaging-sw.js"

    app = Flask(__name__, static_folder=None)

    firebase_config = load_config(config_path)
    received: list[dict[str, Any]] = []

    @app.get("/")
    def index() -> Response:
        return send_from_directory(static_dir, "index.html")

    @app.get("/app.js")
    def app_js() -> Response:
        return send_from_directory(static_dir, "app.js")

    @app.get("/firebase-messaging-sw.js")
    def service_worker() -> Response:
        if not sw_path.exists():
            abort(404)
        response = send_from_directory(sw_path.parent, sw_path.name)
        response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.get("/firebase-config.json")
    def firebase_config_json() -> Response:
        payload = dict(firebase_config)
        payload["_apiBase"] = api_base
        return jsonify(payload)

    @app.post("/received")
    def post_received() -> tuple[Response, int]:
        if not request.is_json:
            return jsonify({"error": "expected application/json"}), 400

        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict) or "title" not in payload:
            return jsonify({"error": "missing required field 'title'"}), 400

        received.append(payload)
        app.logger.info("fake_device_received: %s", payload.get("title"))
        return jsonify({"stored": True}), 201

    @app.get("/received")
    def get_received() -> Response:
        return jsonify(received)

    @app.delete("/received")
    def delete_received() -> tuple[str, int]:
        received.clear()
        return "", 204

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="PushIT fake device Flask server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--api-base",
        default="http://127.0.0.1:8000/api/v1",
        help="Base URL of the PushIT API (used by the page for /devices/link/).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parent / "config.json",
        help="Path to the Firebase Web config JSON.",
    )
    args = parser.parse_args()

    app = create_app(config_path=args.config, api_base=args.api_base)
    print(f"fake_device: serving on http://{args.host}:{args.port}")
    print(f"fake_device: PushIT API base is {args.api_base}")
    print(f"fake_device: open http://{args.host}:{args.port}/?app_token=apt_xxx")
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
