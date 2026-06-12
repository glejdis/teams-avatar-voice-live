#!/usr/bin/env bash
# ----------------------------------------------------------------------------
# deploy.sh — Build & push the Lisa container, then create a new agent version
#             with the microsoft.voice-live.configuration metadata attached.
# ----------------------------------------------------------------------------
# Required env vars:
#   ACR_NAME             ACR registry name (no .azurecr.io)
#   FOUNDRY_RG           Resource group of the Foundry account
#   FOUNDRY_NAME         Foundry account (Cognitive Services) resource name
#   FOUNDRY_PROJECT      Foundry project name
#   AZURE_TENANT_ID      Azure AD / Entra ID tenant id
#   AZURE_SUBSCRIPTION   Azure subscription id
#
# Optional:
#   AGENT_NAME           Default: lisa
#   IMAGE_TAG            Default: timestamp YYYYmmdd-HHMMSS
# ----------------------------------------------------------------------------
set -euo pipefail

: "${ACR_NAME:?ACR_NAME is required}"
: "${FOUNDRY_RG:?FOUNDRY_RG is required}"
: "${FOUNDRY_NAME:?FOUNDRY_NAME is required}"
: "${FOUNDRY_PROJECT:?FOUNDRY_PROJECT is required}"
: "${AZURE_TENANT_ID:?AZURE_TENANT_ID is required}"
: "${AZURE_SUBSCRIPTION:?AZURE_SUBSCRIPTION is required}"

AGENT_NAME="${AGENT_NAME:-lisa}"
IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d-%H%M%S)}"
IMAGE="${ACR_NAME}.azurecr.io/lisa-foundry-agent:${IMAGE_TAG}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VL_CONFIG_FILE="${SCRIPT_DIR}/voice-live-config.json"
[[ -f "$VL_CONFIG_FILE" ]] || { echo "Missing $VL_CONFIG_FILE"; exit 1; }

# 1) Make sure we are on the right tenant + sub (avoid wrong-tenant token errors)
echo "▶ az login (tenant: $AZURE_TENANT_ID)"
az account show --query 'tenantId' -o tsv | grep -q "$AZURE_TENANT_ID" \
  || az login --tenant "$AZURE_TENANT_ID" --only-show-errors >/dev/null
az account set --subscription "$AZURE_SUBSCRIPTION"

# 2) Build & push.
#    Prefer `az acr build` (server-side build in ACR — no local Docker daemon
#    required). Fall back to local docker build/push if the user explicitly
#    sets USE_LOCAL_DOCKER=1.
echo "▶ Building $IMAGE"
if [[ "${USE_LOCAL_DOCKER:-0}" == "1" ]]; then
  az acr login -n "$ACR_NAME"
  docker build --platform linux/amd64 -t "$IMAGE" "$SCRIPT_DIR"
  docker push "$IMAGE"
else
  # Force UTF-8 to avoid Windows cp1252 cli crash on emoji-laden ACR build logs.
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 az acr build \
    --registry "$ACR_NAME" \
    --image "lisa-foundry-agent:${IMAGE_TAG}" \
    --platform linux/amd64 \
    --no-logs \
    "$SCRIPT_DIR"
fi

# 2b) Resolve digest and pin the agent to it.
#     Foundry pins by digest at version-create time — using digest avoids any
#     ambiguity with floating tags and lets us verify what we actually deployed.
echo "▶ Resolving image digest"
IMAGE_DIGEST=""
if [[ "${USE_LOCAL_DOCKER:-0}" == "1" ]]; then
  IMAGE_DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' "$IMAGE" 2>/dev/null \
    | grep -oE '@sha256:[a-f0-9]{64}' | head -n1 || true)
fi
if [[ -z "${IMAGE_DIGEST:-}" ]]; then
  # Query ACR for the manifest digest of the tag we just pushed/built.
  IMAGE_DIGEST="@$(az acr repository show \
      --name "$ACR_NAME" \
      --image "lisa-foundry-agent:${IMAGE_TAG}" \
      --query 'digest' -o tsv)"
fi
[[ "$IMAGE_DIGEST" == @sha256:* ]] || { echo "Failed to resolve image digest"; exit 1; }
IMAGE_REF="${ACR_NAME}.azurecr.io/lisa-foundry-agent${IMAGE_DIGEST}"
IMAGE_TAG_REF="$IMAGE"
echo "  digest: $IMAGE_DIGEST"
echo "  ref:    $IMAGE_REF"

# 3) Pack the voice-live config into a single-line metadata value
VL_CONFIG=$(python -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1]))))" "$VL_CONFIG_FILE")

# 4) Create a new agent version pinned to digest.
#    (re-pushing the same tag does NOT update a running version — always new version.)
#    Note: as of Nov 2025 the CLI no longer supports --resource-group, --port,
#    or --metadata. Port is read from the Dockerfile EXPOSE; metadata is
#    attached via REST PATCH below.
echo "▶ Creating agent version: $AGENT_NAME"
# CLI streams progress text to stdout (not JSON) — capture is not useful, so
# just run it and read the result back via `agent show` afterward.
# Env vars the agent container needs at runtime to reach its model.
# Without these the v1 protocol container starts but every response.create hangs
# forever (no response.done) is returned.
: "${FOUNDRY_MODEL_DEPLOYMENT_NAME:=gpt-4.1-mini}"
FOUNDRY_PROJECT_ENDPOINT_DEFAULT="https://${FOUNDRY_NAME}.services.ai.azure.com/api/projects/${FOUNDRY_PROJECT}"
: "${FOUNDRY_PROJECT_ENDPOINT:=$FOUNDRY_PROJECT_ENDPOINT_DEFAULT}"

az cognitiveservices agent create \
  --account-name "$FOUNDRY_NAME" \
  --project-name "$FOUNDRY_PROJECT" \
  --name "$AGENT_NAME" \
  --image "$IMAGE_TAG_REF" \
  --memory 2Gi \
  --protocol responses \
  --protocol-version 1.0.0 \
  --env \
    "AGENT_FOUNDRY_MODEL_DEPLOYMENT_NAME=${FOUNDRY_MODEL_DEPLOYMENT_NAME}" \
    "AGENT_FOUNDRY_PROJECT_ENDPOINT=${FOUNDRY_PROJECT_ENDPOINT}" \
    "AGENT_AZURE_OPENAI_API_VERSION=${AGENT_AZURE_OPENAI_API_VERSION:-2025-01-01-preview}"

# Read back the new version number from the agent definition. Schema (Nov 2025):
#   { "versions": { "latest": { "version": "<n>", "status": "active", ... } } }
NEW_VERSION=$(az cognitiveservices agent show \
    --account-name "$FOUNDRY_NAME" \
    --project-name "$FOUNDRY_PROJECT" --name "$AGENT_NAME" \
    --query 'versions.latest.version' -o tsv 2>/dev/null || true)
[[ -n "${NEW_VERSION:-}" ]] || { echo "Could not determine new agent version"; exit 1; }
echo "  new version: $NEW_VERSION"

# 5) Poll until the version reports active. Bail on failed.
echo "▶ Waiting for agent '$AGENT_NAME' v$NEW_VERSION to reach active"
DEADLINE=$(( $(date +%s) + 900 ))   # 15 min hard cap
LAST_STATE=""
while : ; do
  STATE=$(az cognitiveservices agent show \
      --account-name "$FOUNDRY_NAME" \
      --project-name "$FOUNDRY_PROJECT" --name "$AGENT_NAME" \
      --query "versions.latest.status" \
      -o tsv 2>/dev/null || true)
  STATE="${STATE:-Unknown}"
  if [[ "$STATE" != "$LAST_STATE" ]]; then
    echo "  state: $STATE"
    LAST_STATE="$STATE"
  fi
  case "$STATE" in
    active|Active|Running|Succeeded) break ;;
    failed|Failed|Canceled)   echo "❌ Agent reached terminal failure state: $STATE"; exit 1 ;;
  esac
  if [[ $(date +%s) -gt $DEADLINE ]]; then
    echo "❌ Timed out waiting for agent to become active (last state: $STATE)"
    exit 1
  fi
  sleep 10
done

echo "✅ Deployed image $IMAGE_REF as agent '$AGENT_NAME' v$NEW_VERSION (active)"
echo "   Wire the sidecar's AGENT_FOUNDRY_AGENT_VERSION=$NEW_VERSION"

# 6) Attach microsoft.voice-live.configuration metadata via REST PATCH.
#    The new CLI does not expose --metadata; this is required for Voice Live
#    agent mode (voice/VAD/transcription/avatar config).
FOUNDRY_HOST="https://${FOUNDRY_NAME}.services.ai.azure.com"
API_VERSION="2025-11-15-preview"
VERSION_URL="${FOUNDRY_HOST}/api/projects/${FOUNDRY_PROJECT}/agents/${AGENT_NAME}/versions/${NEW_VERSION}?api-version=${API_VERSION}"
echo "▶ Attaching voice-live metadata to v$NEW_VERSION"
PATCH_BODY=$(python -c "import json,sys; print(json.dumps({'metadata':{'microsoft.voice-live.configuration': sys.argv[1]}}))" "$VL_CONFIG")
if az rest --method patch --url "$VERSION_URL" \
    --headers "Content-Type=application/merge-patch+json" \
    --body "$PATCH_BODY" -o none 2>/tmp/vl_patch_err; then
  echo "  ok: metadata attached"
else
  echo "  WARN: metadata PATCH failed (see /tmp/vl_patch_err). Voice Live may need manual config."
  cat /tmp/vl_patch_err || true
fi

# Emit machine-readable lines for orchestrators.
echo "AGENT_DEPLOY_VERSION=$NEW_VERSION"
echo "AGENT_DEPLOY_IMAGE=$IMAGE_TAG_REF"
echo "AGENT_DEPLOY_IMAGE_DIGEST=$IMAGE_REF"
