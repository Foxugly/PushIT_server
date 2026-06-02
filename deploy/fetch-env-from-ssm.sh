#!/usr/bin/env bash
# =============================================================================
# PushIT — Fetch environment from AWS SSM Parameter Store into tmpfs.
#
# Run as root by pushit-env-fetch.service (oneshot) at boot, BEFORE
# gunicorn/celery start. The written file lives in /run (tmpfs): it never
# touches disk, never lands in an EBS snapshot, and is re-fetched each boot.
#
# Source of truth = SSM /pushit/prod/* (region eu-west-1), read with the EC2
# instance role via IMDS — no AWS keys on disk.
#
# Guard-rails (so the box never serves traffic with a broken config):
#   - refuses to overwrite the existing .env with an empty result (e.g. broken
#     IAM or wrong prefix) — the last valid .env is kept;
#   - rejects any value containing a newline (would corrupt the EnvironmentFile);
#   - writes atomically (.env.tmp -> mv) with mode 640, owner django:www-data;
#   - exits non-zero on any failure -> pushit-* units (Requires=) won't start.
# =============================================================================
set -euo pipefail

SSM_PREFIX="/pushit/prod"
AWS_REGION="eu-west-1"
RUN_DIR="/run/pushit"
ENV_FILE="$RUN_DIR/.env"
TMP_FILE="$RUN_DIR/.env.tmp"
OWNER="django:www-data"

mkdir -p "$RUN_DIR"

# Fetch every parameter under the prefix (SecureStrings decrypted via the EC2
# instance role) and reconstruct KEY=VALUE lines. Parsing in python3 is robust
# against tabs/spaces in values and lets us reject newline-bearing values.
# A failure anywhere in this pipeline (pipefail) leaves TMP_FILE unpromoted,
# so the previous $ENV_FILE survives untouched.
aws ssm get-parameters-by-path \
    --path "$SSM_PREFIX" \
    --recursive \
    --with-decryption \
    --region "$AWS_REGION" \
    --output json \
| python3 - "$SSM_PREFIX" "$TMP_FILE" <<'PY'
import json, sys

prefix, tmp_path = sys.argv[1], sys.argv[2]
params = json.load(sys.stdin).get("Parameters", [])

if not params:
    sys.stderr.write(
        f"ERROR: no parameters under {prefix}; refusing to write an empty env.\n"
    )
    sys.exit(1)

lines = []
for p in params:
    key = p["Name"][len(prefix):].lstrip("/")
    value = p["Value"]
    if "\n" in value or "\r" in value:
        sys.stderr.write(f"ERROR: value for {key} contains a newline; refusing.\n")
        sys.exit(1)
    lines.append(f"{key}={value}")

with open(tmp_path, "w") as fh:
    fh.write("\n".join(sorted(lines)) + "\n")
PY

# Belt-and-braces: never promote an empty file.
if [ ! -s "$TMP_FILE" ]; then
    echo "ERROR: assembled env file is empty; keeping previous $ENV_FILE." >&2
    rm -f "$TMP_FILE"
    exit 1
fi

chmod 640 "$TMP_FILE"
chown "$OWNER" "$TMP_FILE"
mv -f "$TMP_FILE" "$ENV_FILE"

echo "Wrote $(wc -l < "$ENV_FILE") variables to $ENV_FILE."
