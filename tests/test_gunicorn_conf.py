"""deploy/gunicorn.conf.py, checked with gunicorn's own setting registry."""

import runpy
from pathlib import Path

import pytest

# gunicorn.config imports grp (Unix only): this runs in CI, not on Windows.
Config = pytest.importorskip("gunicorn.config", reason="gunicorn needs grp (Unix only)").Config

CONF = Path(__file__).resolve().parents[1] / "deploy" / "gunicorn.conf.py"


def test_gunicorn_control_socket_is_disabled():
    # gunicorn >= 25.1 opens a control socket by default (~/.gunicorn/gunicorn.ctl),
    # the same path for every site running as the django user.
    settings = runpy.run_path(str(CONF))
    cfg = Config()
    cfg.set("control_socket_disable", settings["control_socket_disable"])  # unknown name raises
    assert cfg.control_socket_disable is True
