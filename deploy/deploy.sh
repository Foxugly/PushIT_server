#!/usr/bin/env bash
# =============================================================================
# PushIT — Deployment script
#
# Called by GitHub Actions (via SSH as 'django' user) or manually:
#   /var/www/django_websites/PushIT_server/deploy/deploy.sh
# =============================================================================
set -euo pipefail

APP_DIR="/var/www/django_websites/PushIT_server"
VENV="$APP_DIR/.venv"

cd "$APP_DIR"

echo ">>> Pulling latest code..."
git fetch origin main
git reset --hard origin/main

echo ">>> Installing dependencies..."
"$VENV/bin/pip" install --quiet -r requirements.txt

# Load the SSM-fetched env so manage.py has DJANGO_SECRET_KEY, STATE, DB creds,
# etc. systemd injects this for the services via EnvironmentFile, but a manual
# command does not get it — we must source it here. (pushit-env-fetch.service
# writes this file at boot from SSM /pushit/prod/*.)
ENV_FILE="/run/pushit/.env"
if [ -f "$ENV_FILE" ]; then
    echo ">>> Loading env from $ENV_FILE..."
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
else
    echo "WARNING: $ENV_FILE missing — has pushit-env-fetch run? Trying without it." >&2
fi

echo ">>> Running migrations..."
"$VENV/bin/python" manage.py migrate --noinput

echo ">>> Collecting static files..."
"$VENV/bin/python" manage.py collectstatic --noinput

# NOTE: pushit-env-fetch is intentionally NOT restarted here — a code deploy
# keeps the env already in /run/pushit/.env. To pick up changed SSM values:
#   sudo systemctl restart pushit-env-fetch
#   sudo systemctl restart pushit-api-gunicorn pushit-celery-worker pushit-celery-beat
echo ">>> Restarting services..."
sudo /bin/systemctl restart pushit-api-gunicorn
sudo /bin/systemctl restart pushit-celery-worker
sudo /bin/systemctl restart pushit-celery-beat

echo ">>> Deploy complete."
