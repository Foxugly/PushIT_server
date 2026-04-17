#!/usr/bin/env bash
# =============================================================================
# PushIT — Server setup for Ubuntu 24.04 EC2
#
# Cohabits with QuizOnline already deployed at /opt/quizonline/.
# Assumes apache2, redis-server, python3.14, django:www-data already exist.
#
# Run as 'ubuntu' user (needs sudo):
#   bash /tmp/setup-pushit.sh
#   or after clone: bash /opt/pushit/deploy/setup-server.sh
#
# Prerequisites:
#   - DNS A record: pushit-api.foxugly.com → EC2 public IP
#   - Security group: inbound 22, 80, 443 (likely already open)
# =============================================================================
set -euo pipefail

APP_DIR="/opt/pushit"
APP_USER="django"
APP_GROUP="www-data"
DOMAIN="pushit-api.foxugly.com"
REPO="https://github.com/Foxugly/PushIT_server.git"
EMAIL="rvilain@foxugly.com"

# ---------------------------------------------------------------------------
echo "=== 1/7 Verify existing infrastructure ==="
# ---------------------------------------------------------------------------

# Check django:www-data user exists
if ! id "$APP_USER" &>/dev/null; then
    echo "Creating user $APP_USER with group $APP_GROUP..."
    sudo useradd --system --create-home --shell /bin/bash --gid "$APP_GROUP" "$APP_USER"
else
    echo "User $APP_USER already exists: $(id $APP_USER)"
fi

# Check required packages (skip install if already present)
MISSING_PKGS=()
for pkg in apache2 redis-server certbot python3-certbot-apache git; do
    if ! dpkg -l "$pkg" &>/dev/null; then
        MISSING_PKGS+=("$pkg")
    fi
done

if [ ${#MISSING_PKGS[@]} -gt 0 ]; then
    echo "Installing missing packages: ${MISSING_PKGS[*]}"
    sudo apt update
    sudo apt install -y "${MISSING_PKGS[@]}"
else
    echo "All required packages already installed."
fi

# Ensure required Apache modules are enabled
sudo a2enmod proxy proxy_http proxy_uwsgi headers ssl rewrite 2>/dev/null || true

# ---------------------------------------------------------------------------
echo "=== 2/7 Create app directory ==="
# ---------------------------------------------------------------------------

sudo mkdir -p "$APP_DIR" /var/log/pushit
sudo chown "$APP_USER":"$APP_GROUP" "$APP_DIR" /var/log/pushit

# ---------------------------------------------------------------------------
echo "=== 3/7 Clone repository ==="
# ---------------------------------------------------------------------------

if [ ! -d "$APP_DIR/.git" ]; then
    sudo -u "$APP_USER" git clone "$REPO" "$APP_DIR"
else
    echo "Repo already cloned, pulling latest..."
    sudo -u "$APP_USER" git -C "$APP_DIR" fetch origin main
    sudo -u "$APP_USER" git -C "$APP_DIR" reset --hard origin/main
fi

# ---------------------------------------------------------------------------
echo "=== 4/7 Python venv + dependencies ==="
# ---------------------------------------------------------------------------

if [ ! -d "$APP_DIR/.venv" ]; then
    sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
fi
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# ---------------------------------------------------------------------------
echo "=== 5/7 Environment file ==="
# ---------------------------------------------------------------------------

if [ ! -f "$APP_DIR/.env" ]; then
    echo ""
    echo "┌──────────────────────────────────────────────────────────────────┐"
    echo "│  No .env found. It will be written by GitHub Actions on first   │"
    echo "│  deploy via the DOTENV_PROD secret.                             │"
    echo "│                                                                 │"
    echo "│  For initial setup, create it manually:                         │"
    echo "│    sudo -u $APP_USER nano $APP_DIR/.env               │"
    echo "│                                                                 │"
    echo "│  See .env_template for required variables.                      │"
    echo "└──────────────────────────────────────────────────────────────────┘"
    echo ""
    # Create a minimal .env so migrate/collectstatic can run
    sudo -u "$APP_USER" bash -c "cat > $APP_DIR/.env << 'ENVEOF'
DJANGO_SECRET_KEY=initial-setup-change-me
STATE=PROD
DEBUG=False
ALLOWED_HOSTS=$DOMAIN
MEDIA_ROOT_DIR=media
REDIS_URL=redis://127.0.0.1:6379/0
ENVEOF"
    sudo chmod 600 "$APP_DIR/.env"
else
    echo ".env already exists, skipping."
fi

# ---------------------------------------------------------------------------
echo "=== 6/7 Initial migrate + collectstatic ==="
# ---------------------------------------------------------------------------

sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" migrate --noinput
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" "$APP_DIR/manage.py" collectstatic --noinput

# ---------------------------------------------------------------------------
echo "=== 7/7 Install services + Apache vhost ==="
# ---------------------------------------------------------------------------

# Systemd services
sudo cp "$APP_DIR/deploy/systemd/"*.service /etc/systemd/system/
sudo systemctl daemon-reload

# Sudoers for deploy.sh (append to existing file if present)
SUDOERS_FILE="/etc/sudoers.d/pushit-deploy"
if [ ! -f "$SUDOERS_FILE" ]; then
    echo "$APP_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart pushit-web, /bin/systemctl restart pushit-celery-worker, /bin/systemctl restart pushit-celery-beat, /bin/systemctl reload apache2" \
        | sudo tee "$SUDOERS_FILE" > /dev/null
    sudo chmod 440 "$SUDOERS_FILE"
    echo "Sudoers rules added for $APP_USER."
else
    echo "Sudoers file already exists, skipping."
fi

# Apache vhost (alongside existing QuizOnline vhost)
sudo cp "$APP_DIR/deploy/apache/pushit.conf" /etc/apache2/sites-available/pushit.conf
sudo a2ensite pushit
sudo apache2ctl configtest
sudo systemctl reload apache2

# SSL certificate for pushit subdomain
echo ""
echo ">>> Getting SSL certificate for $DOMAIN..."
sudo certbot --apache -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL"

# Enable and start PushIT services (redis already running for QuizOnline)
sudo systemctl enable --now pushit-web
sudo systemctl enable --now pushit-celery-worker
sudo systemctl enable --now pushit-celery-beat

echo ""
echo "=== Setup complete ==="
echo ""
echo "  PushIT:        https://$DOMAIN"
echo "  API docs:      https://$DOMAIN/api/docs/"
echo "  Health:        https://$DOMAIN/health/live/"
echo ""
echo "  QuizOnline:    (unchanged, still running)"
echo ""
echo "  Logs:          journalctl -u pushit-web -f"
echo "                 journalctl -u pushit-celery-worker -f"
echo "                 tail -f /var/log/pushit/gunicorn-access.log"
echo "                 tail -f /var/log/apache2/pushit-error.log"
echo ""
echo "  SSH deploy:    Add django's SSH key for GitHub Actions:"
echo "                 sudo -u django mkdir -p /home/django/.ssh"
echo "                 echo '<public key>' | sudo -u django tee -a /home/django/.ssh/authorized_keys"
