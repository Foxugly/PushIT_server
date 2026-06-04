#!/usr/bin/env bash
# =============================================================================
# PushIT — Seed AWS SSM Parameter Store from a local .env file (the "seed").
#
# Source of truth for prod env vars is SSM (/pushit/prod/*, eu-west-1), NOT a
# .env on the server. This pushes a local prod.env up to SSM.
#
#   bash deploy/seed-parameter-store.sh ./prod.env
#
# Requires AWS creds with ssm:PutParameter (your IAM user / SSO) — NOT the EC2
# instance role. Idempotent (--overwrite).
#
# NOTE: --overwrite does NOT change a parameter's Type. To promote a String to
# SecureString, `aws ssm delete-parameter --name <name>` first, then re-seed.
#
# After seeding, apply on the server (see CLAUDE.md):
#   sudo systemctl restart pushit-env-fetch
#   sudo systemctl restart pushit-api-gunicorn pushit-api-celery pushit-api-celery-beat
# =============================================================================
set -euo pipefail

ENV_FILE="${1:?Usage: $0 <path-to-.env>}"
SSM_PREFIX="/pushit/prod"
AWS_REGION="eu-west-1"

# Keys whose values are secrets -> stored as SecureString (KMS key aws/ssm).
# Everything else is stored as a plain String.
SECRET_KEYS=" SECRET_KEY DB_PASSWORD GRAPH_CLIENT_SECRET METRICS_AUTH_TOKEN EXCHANGE_CERT_PASSWORD "

[ -f "$ENV_FILE" ] || { echo "No such file: $ENV_FILE" >&2; exit 1; }

while IFS= read -r line || [ -n "$line" ]; do
    # Skip blank lines and comments; require a KEY=VALUE shape.
    [[ -z "${line//[[:space:]]/}" ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" != *=* ]] && continue

    key="${line%%=*}"
    value="${line#*=}"
    key="${key//[[:space:]]/}"
    [[ -z "$key" ]] && continue

    if [[ "$SECRET_KEYS" == *" $key "* ]]; then
        type="SecureString"
    else
        type="String"
    fi

    echo "  put $SSM_PREFIX/$key  ($type)"
    aws ssm put-parameter \
        --name "$SSM_PREFIX/$key" \
        --value "$value" \
        --type "$type" \
        --overwrite \
        --region "$AWS_REGION" \
        >/dev/null
done < "$ENV_FILE"

echo "Done. Seeded $SSM_PREFIX/* in $AWS_REGION."
echo "Re-fetch on the server:"
echo "  sudo systemctl restart pushit-env-fetch"
echo "  sudo systemctl restart pushit-api-gunicorn pushit-api-celery pushit-api-celery-beat"
