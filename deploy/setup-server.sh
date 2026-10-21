#!/usr/bin/env bash
# =============================================================================
# PushIT — Server setup for Ubuntu 24.04 EC2
#
# Cohabits with QuizOnline already deployed at /opt/quizonline/.
# Assumes nginx, redis-server, python3, django:www-data already exist.
#
# Env vars come from AWS SSM (/pushit/prod/*, eu-west-1), NOT a .env on disk.
# BEFORE running this, seed SSM from your machine and grant the EC2 role read
# access — see "=== 6/8" below and CLAUDE.md.
#
# Run as 'ubuntu' user (needs sudo).
# ⚠️ §3.10: run THIS script from a checkout django cannot write — never from
#    the django-owned app tree (the script runs with ubuntu's sudo). E.g.:
#      D=$(mktemp -d) && git clone --depth=1 https://github.com/Foxugly/PushIT_server.git "$D" \
#        && bash "$D/deploy/setup-server.sh"
#
# Prerequisites:
#   - DNS A record: pushit-api.foxugly.com → EC2 public IP
#   - Security group: inbound 22, 80, 443 (likely already open)
# =============================================================================
set -euo pipefail
umask 027   # nouveaux dirs 750 / fichiers 640 dès clone/pip/collectstatic (OPERATIONS.md §3.1/§3.2)

APP_DIR="/var/www/django_websites/PushIT_server"
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

# Check required packages (skip install if already present).
# awscli is needed by fetch-env-from-ssm.sh.
MISSING_PKGS=()
for pkg in nginx redis-server certbot python3-certbot-nginx git awscli; do
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
echo "=== 5/8 Install systemd services + sudoers ==="
# ---------------------------------------------------------------------------

# §3.10 / §3.11: every file root installs is read from ROOT'S OWN bare mirror
# (/var/lib/fleet-deploy/PushIT_server.git, root:root 700) fetched from GitHub —
# never from $APP_DIR or its .git, which django owns and could rewrite before
# root copies them (django -> root escalation).
MIRROR=/var/lib/fleet-deploy/PushIT_server.git
sudo install -d -m 700 -o root -g root /var/lib/fleet-deploy
sudo test -d "$MIRROR" || sudo git init --quiet --bare "$MIRROR"
sudo git --git-dir="$MIRROR" fetch --quiet --depth=1 "https://github.com/Foxugly/PushIT_server.git" main
SHA=$(sudo git --git-dir="$MIRROR" rev-parse FETCH_HEAD)
root_install() {  # <mode> <path in repo> <destination>
    sudo git --git-dir="$MIRROR" show "$SHA:$2" | sudo install -o root -g root -m "$1" /dev/stdin "$3"
}

# Systemd units (includes pushit-env-fetch, which writes /run/pushit/.env).
for unit in $(sudo git --git-dir="$MIRROR" ls-tree --name-only "$SHA" deploy/systemd/ | grep '\.service$'); do
    root_install 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
sudo systemctl daemon-reload

# Sudoers for deploy.sh. It restarts the app services as root; it does NOT
# restart pushit-env-fetch (env changes are applied manually — see CLAUDE.md).
# Rewritten unconditionally so re-runs pick up renamed units.
SUDOERS_FILE="/etc/sudoers.d/pushit-deploy"
# Quoted heredoc: keep the backslash line-continuations and the '!' negations
# literal (an unquoted heredoc would swallow the trailing '\' and merge the
# lines). APP_USER is "django" by construction; hardcoded here so the quoting
# stays simple. Paths are matched literally by sudo, so keep /bin/systemctl and
# /usr/sbin/nginx as proven (do NOT rewrite to /usr/bin despite usrmerge).
sudo tee "$SUDOERS_FILE" > /dev/null <<'EOF'
# PushIT deploy.sh privileges — restart app units + nginx control as root only.
Cmnd_Alias PUSHIT_CTRL = \
    /bin/systemctl restart pushit-gunicorn, \
    /bin/systemctl restart pushit-celery, \
    /bin/systemctl restart pushit-celery-beat, \
    /usr/sbin/nginx -t, \
    /bin/systemctl reload nginx
django ALL=(root) NOPASSWD: PUSHIT_CTRL
Defaults!PUSHIT_CTRL !setenv, !env_keep
EOF
# Validate syntax before it can break sudo for everyone, then lock perms.
sudo visudo -c -f "$SUDOERS_FILE"
sudo chmod 440 "$SUDOERS_FILE"
echo "Sudoers rules written for $APP_USER."

# ---------------------------------------------------------------------------
echo "=== 6/8 Fetch environment from AWS SSM ==="
# ---------------------------------------------------------------------------
#
# Prerequisites (do these BEFORE running this script):
#   1. Seed SSM from your machine:
#        bash deploy/seed-parameter-store.sh ./prod.env   (or the .ps1 on Windows)
#   2. The EC2 instance role must allow ssm:GetParametersByPath on
#        arn:aws:ssm:eu-west-1:*:parameter/pushit/prod/*   (+ kms:Decrypt).
#
sudo systemctl enable pushit-env-fetch
if ! sudo systemctl start pushit-env-fetch; then
    echo "ERROR: pushit-env-fetch failed — is SSM /pushit/prod seeded and the" >&2
    echo "       EC2 role allowed to read it?  journalctl -u pushit-env-fetch" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
echo "=== 7/8 Initial migrate + collectstatic ==="
# ---------------------------------------------------------------------------

# manage.py run by hand doesn't get systemd's EnvironmentFile — source it.
sudo -u "$APP_USER" bash -c "set -a; . /run/pushit/.env; set +a; \
    '$APP_DIR/.venv/bin/python' '$APP_DIR/manage.py' migrate --noinput && \
    '$APP_DIR/.venv/bin/python' '$APP_DIR/manage.py' collectstatic --noinput"

# Normalize ownership + perms (idempotent). Run by 'ubuntu' here, so sudo is the
# provisioning user's own privilege — NOT a new rule in django's sudoers.
# chown -h: never follow a symlink django may have planted; chmod as django.
sudo chown -R -h "$APP_USER":"$APP_GROUP" "$APP_DIR"
sudo -u "$APP_USER" chmod -R g-w,o-rwx "$APP_DIR"

# ---------------------------------------------------------------------------
echo "=== 8/8 nginx vhost + TLS + start services ==="
# ---------------------------------------------------------------------------

# nginx vhost (alongside the existing QuizOnline vhost)
root_install 0644 deploy/nginx/pushit-api.conf /etc/nginx/sites-available/pushit-api.conf
sudo ln -sf /etc/nginx/sites-available/pushit-api.conf /etc/nginx/sites-enabled/pushit-api.conf
sudo nginx -t
sudo systemctl reload nginx

# SSL certificate for the pushit subdomain (certbot edits the nginx vhost).
echo ""
echo ">>> Getting SSL certificate for $DOMAIN..."
sudo certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL"

# Enable and start PushIT services (redis already running for QuizOnline)
sudo systemctl enable --now pushit-gunicorn
sudo systemctl enable --now pushit-celery
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
echo "  Logs:          journalctl -u pushit-gunicorn -f"
echo "                 journalctl -u pushit-env-fetch -f"
echo "                 journalctl -u pushit-celery -f"
echo "                 tail -f /var/log/pushit/gunicorn-access.log"
echo "                 tail -f /var/log/nginx/pushit-error.log"
echo ""
echo "  SSH deploy:    Add django's SSH key for GitHub Actions:"
echo "                 sudo -u django mkdir -p /home/django/.ssh"
echo "                 echo '<public key>' | sudo -u django tee -a /home/django/.ssh/authorized_keys"
