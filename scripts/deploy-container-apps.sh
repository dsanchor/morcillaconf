#!/usr/bin/env bash
set -Eeuo pipefail

trap 'printf "ERROR: deployment script failed at line %s.\n" "$LINENO" >&2' ERR

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
readonly ENV_FILE="${1:-$SCRIPT_DIR/container-apps.env}"
readonly WORK_DIR="$REPO_ROOT/.container-apps-deploy.$$"

cleanup() {
  rm -rf -- "$WORK_DIR"
}
trap cleanup EXIT

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '==> %s\n' "$*"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

require_vars() {
  local name value
  for name in "$@"; do
    value="$(printenv "$name" || true)"
    [[ -n "$value" ]] || fail "Required variable $name is missing or empty in $ENV_FILE"
    [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] ||
      fail "Variable $name must be a single line"
  done
}

validate_match() {
  local name="$1"
  local pattern="$2"
  local description="$3"
  local value
  value="$(printenv "$name" || true)"
  [[ "$value" =~ $pattern ]] ||
    fail "$name is invalid; expected $description"
}

[[ -f "$ENV_FILE" ]] || fail "Environment file not found: $ENV_FILE"
require_command az
require_command python3

while IFS= read -r -d '' name && IFS= read -r -d '' value; do
  printf -v "$name" '%s' "$value"
  export "$name"
done < <(
  python3 - "$ENV_FILE" <<'PY'
import re
import shlex
import sys

path = sys.argv[1]
key_pattern = re.compile(r"^[A-Z][A-Z0-9_]*$")
with open(path, encoding="utf-8") as stream:
    for number, raw in enumerate(stream, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SystemExit(f"{path}:{number}: expected NAME=value")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key_pattern.fullmatch(key):
            raise SystemExit(f"{path}:{number}: invalid variable name")
        try:
            values = shlex.split(raw_value, comments=True, posix=True)
        except ValueError as exc:
            raise SystemExit(f"{path}:{number}: {exc}") from exc
        if len(values) != 1:
            raise SystemExit(f"{path}:{number}: expected exactly one value")
        sys.stdout.buffer.write(key.encode() + b"\0" + values[0].encode() + b"\0")
PY
)

require_vars \
  AZURE_SUBSCRIPTION_ID AZURE_LOCATION AZURE_RESOURCE_GROUP \
  MANAGED_IDENTITY_NAME CONTAINERAPPS_ENVIRONMENT STORAGE_ACCOUNT_NAME \
  FRONTEND_APP_NAME BFF_APP_NAME RESTAURANT_AGENT_APP_NAME MCP_APP_NAME \
  BFF_STORAGE_NAME MCP_STORAGE_NAME \
  BFF_FILE_SHARE_NAME MCP_FILE_SHARE_NAME AZURE_FILES_QUOTA_GB \
  FRONTEND_IMAGE BFF_IMAGE RESTAURANT_AGENT_IMAGE MCP_IMAGE \
  FOUNDRY_PROJECT_RESOURCE_ID FOUNDRY_PROJECT_ENDPOINT AZURE_AI_MODEL_DEPLOYMENT_NAME \
  MEMORY_MAX_ITEMS WAITER_MAX_TURNS WAITER_AGENT_TIMEOUT_SECONDS \
  BFF_SESSION_TTL_HOURS BFF_EVENT_RETENTION \
  BFF_SSE_HEARTBEAT_SECONDS BFF_ROOM_CACHE_SECONDS SEATING_MCP_TIMEOUT_SECONDS \
  SEATING_LAYOUT_ID SEATING_LAYOUT_FILE SEATING_HOLD_MINUTES

validate_match AZURE_SUBSCRIPTION_ID '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' "an Azure subscription UUID"
validate_match AZURE_LOCATION '^[a-z0-9]+$' "an Azure location name such as spaincentral"
validate_match AZURE_RESOURCE_GROUP '^[[:alnum:]_.()_-]{1,90}$' "1-90 letters, numbers, underscores, periods, parentheses, or hyphens"
[[ "$AZURE_RESOURCE_GROUP" != *. ]] || fail "AZURE_RESOURCE_GROUP cannot end with a period"
validate_match STORAGE_ACCOUNT_NAME '^[a-z0-9]{3,24}$' "3-24 lowercase letters or numbers"
validate_match MANAGED_IDENTITY_NAME '^[[:alnum:]][[:alnum:]_-]{2,127}$' "3-128 letters, numbers, underscores, or hyphens, starting with a letter or number"
validate_match CONTAINERAPPS_ENVIRONMENT '^[a-z][a-z0-9-]{0,58}[a-z0-9]$' "2-60 lowercase letters, numbers, or hyphens, starting with a letter"
validate_match FOUNDRY_PROJECT_ENDPOINT '^https://[^[:space:]]+/api/projects/[^/[:space:]]+/?$' "an HTTPS Foundry project endpoint"
validate_match FOUNDRY_PROJECT_RESOURCE_ID '^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices/accounts/[^/]+/projects/[^/]+$' "a Foundry project Azure resource ID"
validate_match AZURE_FILES_QUOTA_GB '^[0-9]+$' "an integer from 1 to 5120"
((AZURE_FILES_QUOTA_GB >= 1 && AZURE_FILES_QUOTA_GB <= 5120)) ||
  fail "AZURE_FILES_QUOTA_GB must be from 1 to 5120"
[[ "$AZURE_SUBSCRIPTION_ID" != "00000000-0000-0000-0000-000000000000" ]] ||
  fail "Replace the AZURE_SUBSCRIPTION_ID placeholder"
[[ "$AZURE_AI_MODEL_DEPLOYMENT_NAME" != replace-* ]] ||
  fail "Replace the AZURE_AI_MODEL_DEPLOYMENT_NAME placeholder"
[[ "$FOUNDRY_PROJECT_RESOURCE_ID" != *"/subscriptions/00000000-0000-0000-0000-000000000000/"* ]] ||
  fail "Replace the FOUNDRY_PROJECT_RESOURCE_ID placeholder"

for name in FRONTEND_APP_NAME BFF_APP_NAME RESTAURANT_AGENT_APP_NAME MCP_APP_NAME \
  BFF_STORAGE_NAME MCP_STORAGE_NAME; do
  validate_match "$name" '^[a-z][a-z0-9-]{0,30}[a-z0-9]$' "2-32 lowercase letters, numbers, or hyphens, starting with a letter"
done
for name in BFF_FILE_SHARE_NAME MCP_FILE_SHARE_NAME; do
  validate_match "$name" '^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$' "3-63 lowercase letters, numbers, or nonconsecutive hyphens"
  [[ "$(printenv "$name")" != *--* ]] || fail "$name cannot contain consecutive hyphens"
done
validate_ghcr_image() {
  local name="$1"
  local image
  image="$(printenv "$name")"
  [[ "$image" =~ ^ghcr\.io/[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)+(@sha256:[a-f0-9]{64}|:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})$ ]] ||
    fail "$name must be a full public ghcr.io image reference with a digest or explicit tag"
  [[ "$image" != *:latest ]] || fail "$name cannot use the mutable latest tag"
  [[ "$image" != *replace-* ]] || fail "Replace the $name placeholder"
}
for name in FRONTEND_IMAGE BFF_IMAGE RESTAURANT_AGENT_IMAGE MCP_IMAGE; do
  validate_ghcr_image "$name"
done

app_names=("$FRONTEND_APP_NAME" "$BFF_APP_NAME" "$RESTAURANT_AGENT_APP_NAME" "$MCP_APP_NAME")
[[ "$(printf '%s\n' "${app_names[@]}" | sort -u | wc -l)" -eq 4 ]] ||
  fail "All four Container App names must be distinct"
[[ "$BFF_STORAGE_NAME" != "$MCP_STORAGE_NAME" ]] ||
  fail "BFF and MCP environment storage names must be distinct"
[[ "$BFF_FILE_SHARE_NAME" != "$MCP_FILE_SHARE_NAME" ]] ||
  fail "BFF and MCP Azure file share names must be distinct"

if [[ "$SEATING_LAYOUT_FILE" != /* ]]; then
  SEATING_LAYOUT_FILE="$REPO_ROOT/$SEATING_LAYOUT_FILE"
fi
[[ -f "$SEATING_LAYOUT_FILE" ]] || fail "SEATING_LAYOUT_FILE does not exist: $SEATING_LAYOUT_FILE"
SEATING_LAYOUT_JSON="$(python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1], encoding="utf-8")), separators=(",", ":"), sort_keys=True))' "$SEATING_LAYOUT_FILE")"
SEATING_LAYOUT_SHA256="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())' "$SEATING_LAYOUT_JSON")"

export AZURE_CORE_OUTPUT=none
export AZURE_CORE_ONLY_SHOW_ERRORS=true

az account show >/dev/null 2>&1 || fail "Azure CLI is not authenticated; run az login first"
az extension show --name containerapp >/dev/null 2>&1 ||
  fail "Azure CLI extension 'containerapp' is required; install it with: az extension add --name containerapp --upgrade"
az account set --subscription "$AZURE_SUBSCRIPTION_ID"
mkdir -p -- "$WORK_DIR"

log "Creating or updating the resource group and shared infrastructure"
az group create --name "$AZURE_RESOURCE_GROUP" --location "$AZURE_LOCATION"

if ! az identity show --name "$MANAGED_IDENTITY_NAME" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
  az identity create --name "$MANAGED_IDENTITY_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
    --location "$AZURE_LOCATION"
fi
IDENTITY_ID="$(az identity show --name "$MANAGED_IDENTITY_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --query id --output tsv)"
IDENTITY_CLIENT_ID="$(az identity show --name "$MANAGED_IDENTITY_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --query clientId --output tsv)"
IDENTITY_PRINCIPAL_ID="$(az identity show --name "$MANAGED_IDENTITY_NAME" --resource-group "$AZURE_RESOURCE_GROUP" --query principalId --output tsv)"

ensure_role() {
  local role="$1"
  local scope="$2"
  local count
  count="$(az role assignment list --assignee-object-id "$IDENTITY_PRINCIPAL_ID" \
    --scope "$scope" --role "$role" --query 'length(@)' --output tsv)"
  if [[ "$count" == "0" ]]; then
    az role assignment create --assignee-object-id "$IDENTITY_PRINCIPAL_ID" \
      --assignee-principal-type ServicePrincipal --role "$role" --scope "$scope"
  fi
}

ensure_role "Foundry User" "$FOUNDRY_PROJECT_RESOURCE_ID"

if ! az containerapp env show --name "$CONTAINERAPPS_ENVIRONMENT" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
  az containerapp env create --name "$CONTAINERAPPS_ENVIRONMENT" \
    --resource-group "$AZURE_RESOURCE_GROUP" --location "$AZURE_LOCATION"
fi
ENVIRONMENT_ID="$(az containerapp env show --name "$CONTAINERAPPS_ENVIRONMENT" \
  --resource-group "$AZURE_RESOURCE_GROUP" --query id --output tsv)"

if ! az storage account show --name "$STORAGE_ACCOUNT_NAME" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
  az storage account create --name "$STORAGE_ACCOUNT_NAME" \
    --resource-group "$AZURE_RESOURCE_GROUP" --location "$AZURE_LOCATION" \
    --sku Standard_LRS --kind StorageV2 --https-only true \
    --min-tls-version TLS1_2 --allow-blob-public-access false
fi
az storage account update --name "$STORAGE_ACCOUNT_NAME" \
  --resource-group "$AZURE_RESOURCE_GROUP" --https-only true \
  --min-tls-version TLS1_2 --allow-blob-public-access false
STORAGE_KEY="$(az storage account keys list --account-name "$STORAGE_ACCOUNT_NAME" \
  --resource-group "$AZURE_RESOURCE_GROUP" --query '[0].value' --output tsv)"
[[ -n "$STORAGE_KEY" ]] || fail "Could not obtain the storage account key"

configure_storage() {
  local storage_name="$1"
  local share_name="$2"
  az storage share-rm create --resource-group "$AZURE_RESOURCE_GROUP" \
    --storage-account "$STORAGE_ACCOUNT_NAME" --name "$share_name" \
    --quota "$AZURE_FILES_QUOTA_GB" --enabled-protocols SMB
  az containerapp env storage set --name "$CONTAINERAPPS_ENVIRONMENT" \
    --resource-group "$AZURE_RESOURCE_GROUP" --storage-name "$storage_name" \
    --azure-file-account-name "$STORAGE_ACCOUNT_NAME" \
    --azure-file-account-key "$STORAGE_KEY" --azure-file-share-name "$share_name" \
    --access-mode ReadWrite
}

configure_storage "$BFF_STORAGE_NAME" "$BFF_FILE_SHARE_NAME"
configure_storage "$MCP_STORAGE_NAME" "$MCP_FILE_SHARE_NAME"
unset STORAGE_KEY

ensure_app_base() {
  local app_name="$1"
  local image="$2"
  local ingress="$3"
  local port="$4"
  local use_identity="$5"
  shift 5
  local env_vars=("$@")
  if ! az containerapp show --name "$app_name" --resource-group "$AZURE_RESOURCE_GROUP" >/dev/null 2>&1; then
    local create_args=(
      --name "$app_name" --resource-group "$AZURE_RESOURCE_GROUP"
      --environment "$CONTAINERAPPS_ENVIRONMENT" --image "$image"
      --ingress "$ingress" --target-port "$port" --transport auto --allow-insecure false
      --min-replicas 0 --max-replicas 1
      --cpu 0.5 --memory 1Gi --env-vars "${env_vars[@]}"
    )
    if [[ "$use_identity" == "true" ]]; then
      create_args+=(--user-assigned "$IDENTITY_ID")
    fi
    az containerapp create "${create_args[@]}"
  else
    local current_environment_id
    current_environment_id="$(az containerapp show --name "$app_name" \
      --resource-group "$AZURE_RESOURCE_GROUP" --query properties.environmentId --output tsv)"
    if [[ -z "$current_environment_id" ]]; then
      current_environment_id="$(az containerapp show --name "$app_name" \
        --resource-group "$AZURE_RESOURCE_GROUP" --query properties.managedEnvironmentId --output tsv)"
    fi
    [[ "${current_environment_id,,}" == "${ENVIRONMENT_ID,,}" ]] ||
      fail "Container App $app_name already exists in a different Container Apps environment"
  fi
  if [[ "$use_identity" == "true" ]]; then
    az containerapp identity assign --name "$app_name" \
      --resource-group "$AZURE_RESOURCE_GROUP" --user-assigned "$IDENTITY_ID"
  else
    local assigned_identity
    assigned_identity="$(az containerapp identity show --name "$app_name" \
      --resource-group "$AZURE_RESOURCE_GROUP" --output json |
      python3 -c 'import json,sys; target=sys.argv[1].lower(); data=json.load(sys.stdin); print("true" if target in {key.lower() for key in (data.get("userAssignedIdentities") or {})} else "false")' \
        "$IDENTITY_ID")"
    if [[ "$assigned_identity" == "true" ]]; then
      az containerapp identity remove --name "$app_name" \
        --resource-group "$AZURE_RESOURCE_GROUP" --user-assigned "$IDENTITY_ID"
    fi
  fi
  az containerapp ingress enable --name "$app_name" --resource-group "$AZURE_RESOURCE_GROUP" \
    --type "$ingress" --target-port "$port" --transport auto --allow-insecure false
}

apply_app() {
  local app_name="$1"
  local image="$2"
  local ingress="$3"
  local port="$4"
  local storage_name="$5"
  local mount_path="$6"
  local use_identity="$7"
  shift 7
  local env_vars=("$@")
  local spec_file="$WORK_DIR/$app_name.json"

  ensure_app_base "$app_name" "$image" "$ingress" "$port" "$use_identity" "${env_vars[@]}"
  az containerapp show --name "$app_name" --resource-group "$AZURE_RESOURCE_GROUP" \
    --output json >"$spec_file"
  python3 - "$spec_file" "$image" "$storage_name" "$mount_path" "${env_vars[@]}" <<'PY'
import json
import sys

path, image, storage_name, mount_path, *pairs = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    spec = json.load(stream)
spec["properties"]["configuration"]["registries"] = []
template = spec["properties"]["template"]
container = template["containers"][0]
container["image"] = image
container["env"] = [
    {"name": name, "value": value}
    for name, value in (pair.split("=", 1) for pair in pairs)
]
if storage_name:
    container["volumeMounts"] = [{"volumeName": "data", "mountPath": mount_path}]
    template["volumes"] = [
        {"name": "data", "storageName": storage_name, "storageType": "AzureFile"}
    ]
else:
    container.pop("volumeMounts", None)
    template["volumes"] = []
template["scale"]["minReplicas"] = 1
template["scale"]["maxReplicas"] = 1
with open(path, "w", encoding="utf-8") as stream:
    json.dump(spec, stream)
PY
  az containerapp update --name "$app_name" --resource-group "$AZURE_RESOURCE_GROUP" \
    --yaml "$spec_file"
}

log "Creating or updating MCP (internal ingress)"
apply_app "$MCP_APP_NAME" "$MCP_IMAGE" internal 8080 \
  "$MCP_STORAGE_NAME" /data false \
  "SEATING_DATABASE_PATH=/data/seating.db" \
  "SEATING_LAYOUT_ID=$SEATING_LAYOUT_ID" \
  "SEATING_LAYOUT_JSON=$SEATING_LAYOUT_JSON" \
  "SEATING_LAYOUT_SHA256=$SEATING_LAYOUT_SHA256" \
  "SEATING_HOLD_MINUTES=$SEATING_HOLD_MINUTES" \
  "MCP_HOST=0.0.0.0" \
  "MCP_PORT=8080"
MCP_FQDN="$(az containerapp show --name "$MCP_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn --output tsv)"
[[ -n "$MCP_FQDN" ]] || fail "MCP internal FQDN was not assigned"

log "Creating or updating restaurant agent (internal ingress)"
apply_app "$RESTAURANT_AGENT_APP_NAME" \
  "$RESTAURANT_AGENT_IMAGE" internal 8088 \
  "" "" true \
  "FOUNDRY_PROJECT_ENDPOINT=$FOUNDRY_PROJECT_ENDPOINT" \
  "AZURE_AI_MODEL_DEPLOYMENT_NAME=$AZURE_AI_MODEL_DEPLOYMENT_NAME" \
  "AZURE_CLIENT_ID=$IDENTITY_CLIENT_ID" \
  "WAITER_MAX_TURNS=$WAITER_MAX_TURNS" \
  "MEMORY_DATABASE_PATH=/tmp/memory.db" \
  "MEMORY_MAX_ITEMS=$MEMORY_MAX_ITEMS" \
  "APP_ENVIRONMENT=production" \
  "ENABLE_DEV_FAKE_IDENTITY=false" \
  "SEATING_MCP_URL=https://$MCP_FQDN/mcp" \
  "SEATING_MCP_TIMEOUT_SECONDS=$SEATING_MCP_TIMEOUT_SECONDS"
AGENT_FQDN="$(az containerapp show --name "$RESTAURANT_AGENT_APP_NAME" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn --output tsv)"
[[ -n "$AGENT_FQDN" ]] || fail "Restaurant agent internal FQDN was not assigned"

log "Creating or updating BFF (internal ingress)"
apply_app "$BFF_APP_NAME" "$BFF_IMAGE" internal 8000 \
  "$BFF_STORAGE_NAME" /data false \
  "BFF_WAITER=remote" \
  "WAITER_AGENT_URL=https://$AGENT_FQDN" \
  "WAITER_AGENT_TIMEOUT_SECONDS=$WAITER_AGENT_TIMEOUT_SECONDS" \
  "BFF_DATABASE_PATH=/data/bff.db" \
  "MEMORY_DATABASE_PATH=/data/memory.db" \
  "BFF_SQLITE_JOURNAL_MODE=DELETE" \
  "MEMORY_MAX_ITEMS=$MEMORY_MAX_ITEMS" \
  "WAITER_MAX_TURNS=$WAITER_MAX_TURNS" \
  "APP_ENVIRONMENT=production" \
  "BFF_SESSION_TTL_HOURS=$BFF_SESSION_TTL_HOURS" \
  "BFF_EVENT_RETENTION=$BFF_EVENT_RETENTION" \
  "BFF_SSE_HEARTBEAT_SECONDS=$BFF_SSE_HEARTBEAT_SECONDS" \
  "BFF_ROOM_CACHE_SECONDS=$BFF_ROOM_CACHE_SECONDS" \
  "SEATING_MCP_URL=https://$MCP_FQDN/mcp" \
  "SEATING_MCP_TIMEOUT_SECONDS=$SEATING_MCP_TIMEOUT_SECONDS" \
  "BFF_PORT=8000"
BFF_FQDN="$(az containerapp show --name "$BFF_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn --output tsv)"
[[ -n "$BFF_FQDN" ]] || fail "BFF internal FQDN was not assigned"

log "Creating or updating frontend (external ingress)"
apply_app "$FRONTEND_APP_NAME" "$FRONTEND_IMAGE" external 8501 \
  "" "" false \
  "FRONTEND_BFF_CLIENT=http" \
  "FRONTEND_BFF_URL=https://$BFF_FQDN" \
  "FRONTEND_BFF_TIMEOUT_SECONDS=30" \
  "FRONTEND_PORT=8501"

FRONTEND_FQDN="$(az containerapp show --name "$FRONTEND_APP_NAME" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn --output tsv)"
[[ -n "$FRONTEND_FQDN" ]] || fail "Frontend external FQDN was not assigned"
printf 'Deployment completed: https://%s\n' "$FRONTEND_FQDN"
