#!/usr/bin/env bash
# =============================================================================
# PushIT — One-time server setup for Ubuntu 24.04 EC2
#
# Run as the default 'ubuntu' user (needs sudo):
#   bash deploy/setup-server.sh
#
# Prerequisites:
#   - DNS A record: pushit.foxugly.com → EC2 public IP
#   - Security group: inbound 22, 80, 443
#   - .env file ready (copy from .env_template and fill production values)
# =============================================================================
set -euo pipefail

APP_DIR="/opt/pushit"
APP_USER="pushit"
DOMAIN="pushit.foxugly.com"
REPO="https://github.com/Foxugly/PushIT_server.git"
EMAIL="rvilain@foxugly.com"

echo "=== 1/8 System packages ==="
sudo apt update
sudo apt install -y \
    python3 python3-venv python3-pip \
    nginx redis-server \
    certbot python3-certbot-nginx \
    git

echo "=== 2/8 Create app user ==="
if ! id "$APP_USER" &>/dev/null; then
    sudo useradd --system --create-home --shell /bin/bash "$APP_USER"
fi

echo "=== 3/8 Create directories ==="
sudo mkdir -p "$APP_DIR" /var/log/pushit
sudo chown "$APP_USER":"$APP_USER" "$APP_DIR" /var/log/pushit

echo "=== 4/8 Clone repository ==="
if [ ! -d "$APP_DIR/.git" ]; then
    sudo -u "$APP_USER" git clone "$REPO" "$APP_DIR"
else
    echo "Repo already cloned, pulling latest..."
    sudo -u "$APP_USER" git -C "$APP_DIR" pull origin main
fi

echo "=== 5/8 Python venv + dependencies ==="
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "=== 6/8 Environment file ==="
if [ ! -f "$APP_DIR/.env" ]; then
    sudo -u "$APP_USER" cp "$APP_DIR/.env_template" "$APP_DIR/.env"
    echo ""
    echo ">>> IMPORTANT: Edit $APP_DIR/.env with production values before starting services <<<"
    echo "    Required: DJANGO_SECRET_KEY, STATE=PROD, ALLOWED_HOSTS=$DOMAIN"
    echo ""
fi

echo "=== 7/8 Initial deploy (migrate + collectstatic) ==="
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" migrate --noinput
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" collectstatic --noinput

echo "=== 8/8 Install services ==="

# Systemd
sudo cp "$APP_DIR/deploy/systemd/"*.service /etc/systemd/system/
sudo systemctl daemon-reload

# Allow pushit user to restart its own services (used by deploy.sh)
SUDOERS_FILE="/etc/sudoers.d/pushit-deploy"
if [ ! -f "$SUDOERS_FILE" ]; then
    echo "$APP_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart pushit-web, /bin/systemctl restart pushit-celery-worker, /bin/systemctl restart pushit-celery-beat, /bin/systemctl reload nginx" | sudo tee "$SUDOERS_FILE" > /dev/null
    sudo chmod 440 "$SUDOERS_FILE"
fi

# Nginx
sudo cp "$APP_DIR/deploy/nginx/pushit.conf" /etc/nginx/sites-available/pushit
sudo ln -sf /etc/nginx/sites-available/pushit /etc/nginx/sites-enabled/pushit
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

# SSL certificate
echo ""
echo ">>> Getting SSL certificate for $DOMAIN..."
sudo certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL"

# Enable and start everything
sudo systemctl enable --now redis-server
sudo systemctl enable --now pushit-web
sudo systemctl enable --now pushit-celery-worker
sudo systemctl enable --now pushit-celery-beat

echo ""
echo "=== Setup complete ==="
echo "  App:      https://$DOMAIN"
echo "  API docs: https://$DOMAIN/api/docs/"
echo "  Health:   https://$DOMAIN/health/live/"
echo ""
echo "  Logs:     journalctl -u pushit-web -f"
echo "            journalctl -u pushit-celery-worker -f"
echo "            tail -f /var/log/pushit/gunicorn-access.log"
