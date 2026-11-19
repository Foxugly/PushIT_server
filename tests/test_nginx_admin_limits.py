"""The admin login and its 2FA steps share the same nginx rate-limit zone."""
import re
from pathlib import Path

NGINX = Path(__file__).resolve().parent.parent / "deploy" / "nginx"
CONF = (NGINX / "pushit-api.conf").read_text(encoding="utf-8")
LIMITS = (NGINX / "pushit-limits.conf").read_text(encoding="utf-8")


def _block(header):
    start = CONF.index(header)
    return CONF[start:CONF.index("}", start)]


def test_admin_login_is_rate_limited():
    assert "limit_req zone=pushit_admin_login" in _block("location = /admin/login/")
    assert re.search(r"limit_req_zone \$binary_remote_addr zone=pushit_admin_login:\S+ rate=10r/m;", LIMITS)


def test_admin_2fa_steps_share_the_login_limit():
    block = _block("location ^~ /admin/2fa/")
    assert "limit_req zone=pushit_admin_login" in block
    assert "proxy_pass http://127.0.0.1:8001;" in block
