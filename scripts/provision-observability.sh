#!/usr/bin/env bash
# Creates or updates a workspace-based Application Insights resource used by
# the services' direct OpenTelemetry trace exporters. Idempotent; the resource
# group is a prerequisite.
set -Eeuo pipefail

trap 'printf "ERROR: observability provisioning failed at line %s.\n" "$LINENO" >&2' ERR

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly ENV_FILE="${1:-$SCRIPT_DIR/observability.env}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '==> %s\n' "$*"
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

command -v az >/dev/null 2>&1 || fail "Required command not found: az"
command -v python3 >/dev/null 2>&1 || fail "Required command not found: python3"
[[ -f "$ENV_FILE" ]] || fail "Environment file not found: $ENV_FILE"

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

export LOG_ANALYTICS_RETENTION_DAYS="${LOG_ANALYTICS_RETENTION_DAYS:-30}"
require_vars \
  AZURE_SUBSCRIPTION_ID OBSERVABILITY_RESOURCE_GROUP OBSERVABILITY_LOCATION \
  LOG_ANALYTICS_WORKSPACE_NAME LOG_ANALYTICS_RETENTION_DAYS \
  APPLICATIONINSIGHTS_NAME

validate_match AZURE_SUBSCRIPTION_ID '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' "an Azure subscription UUID"
[[ "$AZURE_SUBSCRIPTION_ID" != "00000000-0000-0000-0000-000000000000" ]] ||
  fail "Replace the AZURE_SUBSCRIPTION_ID placeholder"
validate_match OBSERVABILITY_RESOURCE_GROUP '^[[:alnum:]_.()_-]{1,90}$' "an Azure resource group name"
validate_match OBSERVABILITY_LOCATION '^[a-z0-9]+$' "an Azure location such as spaincentral"
validate_match LOG_ANALYTICS_WORKSPACE_NAME '^[A-Za-z0-9][A-Za-z0-9-]{2,61}[A-Za-z0-9]$' "a 4-63 character Log Analytics workspace name"
validate_match LOG_ANALYTICS_RETENTION_DAYS '^(3[0-9]|[4-9][0-9]|[1-6][0-9]{2}|7[0-2][0-9]|730)$' "a whole number from 30 to 730"
validate_match APPLICATIONINSIGHTS_NAME '^[A-Za-z0-9][A-Za-z0-9._()-]{0,253}[A-Za-z0-9)]$' "a valid Application Insights resource name"

export AZURE_CORE_OUTPUT=none
export AZURE_CORE_ONLY_SHOW_ERRORS=true

az account show >/dev/null 2>&1 ||
  fail "Azure CLI is not authenticated; run az login first"
az account set --subscription "$AZURE_SUBSCRIPTION_ID"
[[ "$(az group exists --name "$OBSERVABILITY_RESOURCE_GROUP" --output tsv)" == "true" ]] ||
  fail "Resource group $OBSERVABILITY_RESOURCE_GROUP does not exist"
az extension show --name application-insights >/dev/null 2>&1 ||
  fail "Azure CLI extension 'application-insights' is required; install it with: az extension add --name application-insights --upgrade"

log "Creating or updating Log Analytics workspace $LOG_ANALYTICS_WORKSPACE_NAME"
az monitor log-analytics workspace create \
  --resource-group "$OBSERVABILITY_RESOURCE_GROUP" \
  --workspace-name "$LOG_ANALYTICS_WORKSPACE_NAME" \
  --location "$OBSERVABILITY_LOCATION" \
  --retention-time "$LOG_ANALYTICS_RETENTION_DAYS"

WORKSPACE_ID="$(az monitor log-analytics workspace show \
  --resource-group "$OBSERVABILITY_RESOURCE_GROUP" \
  --workspace-name "$LOG_ANALYTICS_WORKSPACE_NAME" \
  --query id --output tsv)"
[[ -n "$WORKSPACE_ID" ]] || fail "Log Analytics workspace resource ID was not returned"

log "Creating or updating workspace-based Application Insights $APPLICATIONINSIGHTS_NAME"
az monitor app-insights component create \
  --app "$APPLICATIONINSIGHTS_NAME" \
  --resource-group "$OBSERVABILITY_RESOURCE_GROUP" \
  --location "$OBSERVABILITY_LOCATION" \
  --workspace "$WORKSPACE_ID" \
  --application-type web \
  --kind web

APPLICATIONINSIGHTS_RESOURCE_ID="$(az monitor app-insights component show \
  --app "$APPLICATIONINSIGHTS_NAME" \
  --resource-group "$OBSERVABILITY_RESOURCE_GROUP" \
  --query id --output tsv)"
APPLICATIONINSIGHTS_CONNECTION_STRING="$(az monitor app-insights component show \
  --app "$APPLICATIONINSIGHTS_NAME" \
  --resource-group "$OBSERVABILITY_RESOURCE_GROUP" \
  --query connectionString --output tsv)"
[[ -n "$APPLICATIONINSIGHTS_RESOURCE_ID" ]] ||
  fail "Application Insights resource ID was not returned"
[[ -n "$APPLICATIONINSIGHTS_CONNECTION_STRING" ]] ||
  fail "Application Insights connection string was not returned"

cat <<EOF

Observability resources are ready.

APPLICATIONINSIGHTS_RESOURCE_ID="$APPLICATIONINSIGHTS_RESOURCE_ID"
APPLICATIONINSIGHTS_CONNECTION_STRING="$APPLICATIONINSIGHTS_CONNECTION_STRING"

Copy both values into container-apps.env and redeploy.
Traces and privacy-safe metrics are exported; prompts, responses and logs remain disabled.
EOF
