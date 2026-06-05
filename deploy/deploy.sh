#!/usr/bin/env bash
# =============================================================================
# PushIT — Deployment script
#
# Called by GitHub Actions (via SSH as 'django' user) or manually:
#   /var/www/django_websites/PushIT_server/deploy/deploy.sh
# =============================================================================
set -euo pipefail
umask 027   # nouveaux dirs 750 / fichiers 640 dès git/pip/collectstatic (OPERATIONS.md §3.1/§3.2)

APP_DIR="/var/www/django_websites/PushIT_server"
VENV="$APP_DIR/.venv"

cd "$APP_DIR"

echo ">>> Pulling latest code..."
git fetch origin main
git reset --hard origin/main

echo ">>> Installing dependencies..."
"$VENV/bin/pip" install --quiet -r requirements.txt

# Load the SSM-fetched env so manage.py has SECRET_KEY, STATE, DB creds,
# etc. systemd injects this for the services via EnvironmentFile, but a manual
# command does not get it — we must source it here. (pushit-env-fetch.service
# writes this file at boot from SSM /pushit/prod/*.)
ENV_FILE="/run/pushit/.env"
if [ -f "$ENV_FILE" ]; then
    echo ">>> Loading env from $ENV_FILE..."
    # Parse literally (key=value), NOT `source`: this is a systemd
    # EnvironmentFile, and values (e.g. SECRET_KEY) may contain
    # shell-special chars ($ ` ( ) …) that `.` would expand/mangle — which
    # silently emptied SECRET_KEY and broke `migrate`. Mirrors systemd parsing.
    while IFS='=' read -r _k _v || [ -n "$_k" ]; do
        case "$_k" in ''|\#*) continue ;; esac
        export "$_k=$_v"
    done < "$ENV_FILE"
    unset _k _v
else
    echo "WARNING: $ENV_FILE missing — has pushit-env-fetch run? Trying without it." >&2
fi

echo ">>> Running migrations..."
"$VENV/bin/python" manage.py migrate --noinput

echo ">>> Collecting static files..."
"$VENV/bin/python" manage.py collectstatic --noinput

echo ">>> Normalizing permissions (idempotent: dirs 750 / files 640, no o-rwx, no g-w)..."
# deploy.sh runs as django, which OWNS the whole tree (primary group www-data),
# so this needs NO sudo: django chgrp's to its own group and chmod's its files.
# chown first (fixes any group drift), then chmod (drop group-write + all "other").
# Owner bits untouched -> execute preserved on .venv/bin, manage.py, etc.
chown -R django:www-data "$APP_DIR"
chmod -R g-w,o-rwx "$APP_DIR"

# NOTE: pushit-env-fetch is intentionally NOT restarted here — a code deploy
# keeps the env already in /run/pushit/.env. To pick up changed SSM values:
#   sudo systemctl restart pushit-env-fetch
#   sudo systemctl restart pushit-gunicorn pushit-celery pushit-celery-beat
echo ">>> Restarting services..."
sudo /bin/systemctl restart pushit-gunicorn
sudo /bin/systemctl restart pushit-celery
sudo /bin/systemctl restart pushit-celery-beat

echo ">>> Deploy complete."
