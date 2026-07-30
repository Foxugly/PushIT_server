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


@pytest.fixture
def fresh_database_env(tmp_path):
    """
    Variables d'env pointant vers une base VIDE, sur le moteur de production.

    Tout l'interet de ce test est de prouver que les migrations s'appliquent sur
    une base vierge. Sur SQLite cette preuve ne vaut rien pour la production :
    c'est PostgreSQL qui tourne sur la box, et c'est lui qui refuse ce que
    SQLite accepte. On cree donc une base PostgreSQL jetable, qu'on detruit
    ensuite. Repli sur un fichier SQLite temporaire quand DB_ENGINE n'est pas
    PostgreSQL, pour qu'un checkout nu reste executable.
    """
    if "postgres" not in os.environ.get("DB_ENGINE", "sqlite3"):
        yield {"SQLITE_NAME": str(tmp_path / "full_flow.sqlite3")}
        return

    import psycopg

    name = f"pushit_flow_{os.getpid()}"
    params = {
        "host": os.environ.get("DB_HOST") or "localhost",
        "port": os.environ.get("DB_PORT") or "5432",
        "user": os.environ.get("DB_USER") or "postgres",
        "password": os.environ.get("DB_PASSWORD") or "",
        "dbname": "postgres",
    }

    def _drop(conn):
        # Un serveur de test encore connecte empecherait le DROP.
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (name,),
        )
        conn.execute(f'DROP DATABASE IF EXISTS "{name}"')

    with psycopg.connect(autocommit=True, **params) as conn:
        _drop(conn)
        conn.execute(f'CREATE DATABASE "{name}"')
    try:
        yield {"DB_NAME": name}
    finally:
        with psycopg.connect(autocommit=True, **params) as conn:
            _drop(conn)


@pytest.mark.integration
def test_fresh_db_migrate_and_full_flow(tmp_path, fresh_database_env):
    port = _get_free_port()
    base_url = f"http://127.0.0.1:{port}/api/v1"

    env = os.environ.copy()
    env["DJANGO_SETTINGS_MODULE"] = "config.settings"
    # `python scripts/full_flow.py` met scripts/ en tete de sys.path, pas la
    # racine : sans PYTHONPATH, le sous-processus echoue sur `import config` des
    # le django.setup(). Le test etait casse en local et exclu de la CI par son
    # marqueur, donc personne ne le voyait echouer.
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.update(fresh_database_env)
    env["STATE"] = "DEV"
    env["ALLOWED_HOSTS"] = f"localhost,127.0.0.1,[::1],testserver"
    env["PUSHIT_FORCE_MOCK_PUSH"] = "true"

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
        # Le marqueur final du script. Il cherchait « === TERMINE === », reste
        # d'une version francaise que `full_flow.py` n'imprime plus : le test ne
        # tournant nulle part, le decalage a survecu a la traduction.
        assert "=== DONE ===" in flow.stdout, flow.stdout[-500:]
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
