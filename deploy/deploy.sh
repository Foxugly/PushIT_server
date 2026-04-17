#!/usr/bin/env bash
# =============================================================================
# PushIT — Deployment script
#
# Called by GitHub Actions (via SSH as 'pushit' user) or manually:
#   /opt/pushit/deploy/deploy.sh
# =============================================================================
set -euo pipefail

APP_DIR="/opt/pushit"
VENV="$APP_DIR/.venv"

cd "$APP_DIR"

echo ">>> Pulling latest code..."
git fetch origin main
git reset --hard origin/main

echo ">>> Installing dependencies..."
"$VENV/bin/pip" install --quiet -r requirements.txt

echo ">>> Running migrations..."
"$VENV/bin/python" manage.py migrate --noinput

echo ">>> Collecting static files..."
"$VENV/bin/python" manage.py collectstatic --noinput

echo ">>> Restarting services..."
sudo /bin/systemctl restart pushit-web
sudo /bin/systemctl restart pushit-celery-worker
sudo /bin/systemctl restart pushit-celery-beat

echo ">>> Deploy complete."
