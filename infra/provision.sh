#!/usr/bin/env bash
# Provision all Azure resources for the TTB Label Verification prototype.
#
# Creates an isolated resource group containing:
#   - one AIServices resource that serves BOTH Azure AI Vision (Image Analysis
#     4.0 / Read OCR) AND an in-region gpt-4o deployment (US data residency)
#   - a Linux App Service plan + web app (Python) to host the FastAPI backend
#
# Re-runnable: existing resources are left in place. Resolved names, endpoint,
# and key are written to infra/deploy.env (gitignored) for deploy.sh to consume.
#
# Usage:  bash infra/provision.sh
set -euo pipefail

# ---- configuration ----------------------------------------------------------
LOCATION="${LOCATION:-eastus2}"
SUFFIX="${SUFFIX:-$(printf '%04x' $((RANDOM % 65536)))}"
RG="${RG:-rg-ttb-label-verify}"
AI_NAME="${AI_NAME:-ttb-ai-${SUFFIX}}"
PLAN_NAME="${PLAN_NAME:-ttb-plan-${SUFFIX}}"
APP_NAME="${APP_NAME:-ttb-label-verify-${SUFFIX}}"
GPT_DEPLOYMENT="gpt-4o"
GPT_MODEL_VERSION="2024-11-20"
GPT_SKU="Standard"          # in-region Standard => US data residency (not Global)
GPT_CAPACITY="30"           # 30K tokens/min — ample for a prototype
PY_RUNTIME="PYTHON:3.12"
PLAN_SKU="S1"   # Standard: enough CPU for concurrent OCR+VLM under the 5s bar

# Resource providers must be registered once per subscription. Idempotent;
# --wait blocks until registration completes so the create calls below succeed
# on a brand-new subscription.
echo "==> Ensuring resource providers are registered"
az provider register -n Microsoft.CognitiveServices --wait
az provider register -n Microsoft.Web --wait

echo "==> Resource group $RG ($LOCATION)"
az group create -n "$RG" -l "$LOCATION" -o none

echo "==> AIServices resource $AI_NAME (Vision + OpenAI)"
az cognitiveservices account create \
  -n "$AI_NAME" -g "$RG" -l "$LOCATION" \
  --kind AIServices --sku S0 \
  --custom-domain "$AI_NAME" --yes -o none

echo "==> Deploying $GPT_DEPLOYMENT ($GPT_MODEL_VERSION, $GPT_SKU, US in-region)"
az cognitiveservices account deployment create \
  -n "$AI_NAME" -g "$RG" \
  --deployment-name "$GPT_DEPLOYMENT" \
  --model-name gpt-4o --model-version "$GPT_MODEL_VERSION" --model-format OpenAI \
  --sku-name "$GPT_SKU" --sku-capacity "$GPT_CAPACITY" -o none

AI_ENDPOINT=$(az cognitiveservices account show -n "$AI_NAME" -g "$RG" \
  --query properties.endpoint -o tsv)
AI_KEY=$(az cognitiveservices account keys list -n "$AI_NAME" -g "$RG" \
  --query key1 -o tsv)

echo "==> App Service plan $PLAN_NAME ($PLAN_SKU, Linux)"
az appservice plan create -n "$PLAN_NAME" -g "$RG" --is-linux --sku "$PLAN_SKU" -o none

echo "==> Web app $APP_NAME ($PY_RUNTIME)"
az webapp create -n "$APP_NAME" -g "$RG" -p "$PLAN_NAME" --runtime "$PY_RUNTIME" -o none

echo "==> App settings (endpoints, keys, runtime)"
az webapp config appsettings set -n "$APP_NAME" -g "$RG" -o none --settings \
  VISION_ENDPOINT="$AI_ENDPOINT" VISION_KEY="$AI_KEY" \
  AOAI_ENDPOINT="$AI_ENDPOINT" AOAI_KEY="$AI_KEY" \
  AOAI_DEPLOYMENT="$GPT_DEPLOYMENT" AOAI_API_VERSION="2024-10-21" \
  MOCK_VISION=false REQUIRE_AZURE=true \
  BATCH_CONCURRENCY=8 MAX_UPLOAD_MB=10 MAX_BATCH_FILES=400 \
  RATE_LIMIT_PER_MIN=60 DAILY_CALL_CAP=1500 \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true WEBSITES_PORT=8000

az webapp config set -n "$APP_NAME" -g "$RG" -o none \
  --always-on true \
  --startup-file "gunicorn -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:8000 app.main:app"

# ---- persist resolved names for deploy.sh -----------------------------------
cat > "$(dirname "$0")/deploy.env" <<EOF
RG=$RG
APP_NAME=$APP_NAME
AI_NAME=$AI_NAME
AI_ENDPOINT=$AI_ENDPOINT
APP_URL=https://$APP_NAME.azurewebsites.net
EOF

echo ""
echo "Provisioning complete."
echo "  Web app:  https://$APP_NAME.azurewebsites.net"
echo "  AI:       $AI_ENDPOINT"
echo "  Wrote infra/deploy.env"
