import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests


REPO_ROOT = Path(__file__).resolve().parents[1]


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_server(base_url: str, timeout_seconds: int = 30) -> None:
    deadline = time.time() + timeout_seconds
    last_error = None

    while time.time() < deadline:
        try:
            response = requests.get(f"{base_url}/auth/register/", timeout=1)
            if response.status_code in {405, 401, 403}:
                return
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(0.25)

    raise AssertionError(f"Server did not start in time: {last_error}")


@pytest.mark.integration
def test_fresh_db_migrate_and_full_flow(tmp_path):
    db_path = tmp_path / "full_flow.sqlite3"
    port = _get_free_port()
    base_url = f"http://127.0.0.1:{port}/api/v1"

    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "config.settings"
    env["SQLITE_NAME"] = str(db_path)
    env["STATE"] = "DEV"
    env["ALLOWED_HOSTS"] = f"localhost,127.0.0.1,[::1],testserver"

    migrate = subprocess.run(
        [sys.executable, "manage.py", "migrate", "--noinput"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert migrate.returncode == 0, migrate.stdout + "\n" + migrate.stderr

    server = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        _wait_for_server(base_url)

        flow = subprocess.run(
            [sys.executable, "scripts/full_flow.py"],
            cwd=REPO_ROOT,
            env={**env, "PUSHIT_BASE_URL": base_url},
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert flow.returncode == 0, flow.stdout + "\n" + flow.stderr
        assert "=== TERMINE ===" in flow.stdout or "=== TERMIN" in flow.stdout
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
