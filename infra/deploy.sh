#!/usr/bin/env bash
# Package and deploy the app to the App Service web app created by provision.sh.
#
# Assembles a minimal deployment bundle (app/ + frontend/ + requirements.txt),
# zip-deploys it, and lets Oryx pip-install on the server (SCM build enabled).
#
# Usage:  bash infra/deploy.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/infra/deploy.env"   # RG, APP_NAME, APP_URL

BUILD="$(mktemp -d)"
cp -r "$ROOT/backend/app"            "$BUILD/app"
cp -r "$ROOT/frontend"              "$BUILD/frontend"
cp    "$ROOT/backend/requirements.txt" "$BUILD/requirements.txt"

ZIP="$(mktemp -d)/ttb_deploy.zip"
( cd "$BUILD" && zip -qr "$ZIP" . )
echo "==> Deploying $(du -h "$ZIP" | cut -f1) bundle to $APP_NAME"

# The deploy command can report a non-zero "worker failed to start within the
# allotted time" even when the container comes up moments later (a warm-up race,
# especially right after an app-settings change). So we don't trust its exit code
#, we poll /health as the real source of truth.
az webapp deploy -g "$RG" -n "$APP_NAME" --type zip --src-path "$ZIP" -o none \
  || echo "Note: deploy returned non-zero (often a cosmetic warm-up timeout); verifying health..."

URL="${APP_URL:-https://$APP_NAME.azurewebsites.net}"
echo "==> Waiting for $URL/health"
for _ in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "$URL/health" || true)
  if [ "$code" = "200" ]; then
    echo "Healthy. URL: $URL"
    exit 0
  fi
  sleep 5
done
echo "WARNING: $URL did not report healthy in time."
echo "  Inspect: az webapp log tail -n $APP_NAME -g $RG"
exit 1
