#!/usr/bin/env bash
# Creates or updates the restaurant's Foundry IQ knowledge layer: a storage
# account with the carta and the recipe book, an Azure AI Search service with
# the recipe index, three knowledge sources and one knowledge base. Idempotent;
# the resource group and the Foundry project are prerequisites.
set -Eeuo pipefail

trap 'printf "ERROR: knowledge provisioning failed at line %s.\n" "$LINENO" >&2' ERR

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
readonly ENV_FILE="${1:-$SCRIPT_DIR/knowledge.env}"
readonly DEFINITIONS="$REPO_ROOT/infra/knowledge"
readonly CONTENT="$REPO_ROOT/data/knowledge"
# The knowledge base MCP endpoint only exists in this preview version; the
# waiter pins the same one.
readonly SEARCH_API_VERSION="2026-08-01-preview"
readonly ROLE_WAIT_SECONDS=600
readonly INDEXER_WAIT_SECONDS=600
WORK_DIR="$(mktemp -d)"
readonly WORK_DIR

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
require_command curl
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
  AZURE_SUBSCRIPTION_ID KNOWLEDGE_RESOURCE_GROUP KNOWLEDGE_LOCATION \
  AZURE_SEARCH_SERVICE_NAME STORAGE_ACCOUNT_NAME \
  FOUNDRY_PROJECT_RESOURCE_ID KNOWLEDGE_BASE_MODEL_DEPLOYMENT KNOWLEDGE_BASE_NAME
# Exported so that validate_match, which reads the environment, sees the default.
export AZURE_SEARCH_SKU="${AZURE_SEARCH_SKU:-basic}"
AGENT_IDENTITY_NAME="${AGENT_IDENTITY_NAME:-}"
AGENT_IDENTITY_RESOURCE_GROUP="${AGENT_IDENTITY_RESOURCE_GROUP:-$KNOWLEDGE_RESOURCE_GROUP}"

validate_match AZURE_SUBSCRIPTION_ID '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' "an Azure subscription UUID"
[[ "$AZURE_SUBSCRIPTION_ID" != "00000000-0000-0000-0000-000000000000" ]] ||
  fail "Replace the AZURE_SUBSCRIPTION_ID placeholder"
validate_match KNOWLEDGE_RESOURCE_GROUP '^[[:alnum:]_.()_-]{1,90}$' "1-90 letters, numbers, underscores, periods, parentheses, or hyphens"
validate_match KNOWLEDGE_LOCATION '^[a-z0-9]+$' "an Azure location with agentic retrieval, such as swedencentral"
validate_match AZURE_SEARCH_SERVICE_NAME '^[a-z0-9][a-z0-9-]{0,58}[a-z0-9]$' "2-60 lowercase letters, numbers, or hyphens"
[[ "$AZURE_SEARCH_SERVICE_NAME" != *--* ]] || fail "AZURE_SEARCH_SERVICE_NAME cannot contain consecutive hyphens"
validate_match AZURE_SEARCH_SKU '^(basic|standard|standard2|standard3)$' "basic or a standard tier (the free tier has no managed identity)"
validate_match STORAGE_ACCOUNT_NAME '^[a-z0-9]{3,24}$' "3-24 lowercase letters or numbers"
validate_match FOUNDRY_PROJECT_RESOURCE_ID '^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices/accounts/[^/]+/projects/[^/]+$' "a Foundry project Azure resource ID"
[[ "$FOUNDRY_PROJECT_RESOURCE_ID" != *"/subscriptions/00000000-0000-0000-0000-000000000000/"* ]] ||
  fail "Replace the FOUNDRY_PROJECT_RESOURCE_ID placeholder"
validate_match KNOWLEDGE_BASE_MODEL_DEPLOYMENT '^[A-Za-z0-9._-]{1,64}$' "a model deployment name of the Foundry resource"
validate_match KNOWLEDGE_BASE_NAME '^[a-z0-9]([a-z0-9-]{0,126}[a-z0-9])?$' "lowercase letters, numbers, or hyphens"

export AZURE_CORE_OUTPUT=none
export AZURE_CORE_ONLY_SHOW_ERRORS=true

az account show >/dev/null 2>&1 || fail "Azure CLI is not authenticated; run az login first"
az account set --subscription "$AZURE_SUBSCRIPTION_ID"
[[ "$(az group exists --name "$KNOWLEDGE_RESOURCE_GROUP" --output tsv)" == "true" ]] ||
  fail "Resource group $KNOWLEDGE_RESOURCE_GROUP does not exist; create it before running this script"

# Caller (a user or a service principal) that uploads blobs and creates the
# search objects; it also gets read access to query the knowledge base.
if [[ "$(az account show --query user.type --output tsv)" == "user" ]]; then
  CALLER_ID="$(az ad signed-in-user show --query id --output tsv)"
  CALLER_TYPE="User"
else
  CALLER_ID="$(az ad sp show --id "$(az account show --query user.name --output tsv)" --query id --output tsv)"
  CALLER_TYPE="ServicePrincipal"
fi

ensure_role() {
  local principal="$1"
  local principal_type="$2"
  local role="$3"
  local scope="$4"
  local count
  count="$(az role assignment list --assignee-object-id "$principal" --scope "$scope" \
    --role "$role" --query 'length(@)' --output tsv)"
  if [[ "$count" == "0" ]]; then
    az role assignment create --assignee-object-id "$principal" \
      --assignee-principal-type "$principal_type" --role "$role" --scope "$scope"
    printf '    %s on %s for a %s\n' "$role" "${scope##*/}" "$principal_type"
  fi
}

# Runs a storage data-plane command, waiting while new role assignments propagate.
storage_command() {
  local deadline=$((SECONDS + ROLE_WAIT_SECONDS))
  until "$@" >/dev/null 2>"$WORK_DIR/storage-error.txt"; do
    if ((SECONDS >= deadline)) ||
      ! grep -q -E 'AuthorizationPermissionMismatch|AuthorizationFailure|required permissions|not authorized|403' "$WORK_DIR/storage-error.txt"; then
      cat "$WORK_DIR/storage-error.txt" >&2
      fail "Storage command failed: $*"
    fi
    printf '    waiting for the storage role assignments...\n'
    sleep 20
  done
}

document_version() {
  python3 - "$1" <<'PY'
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r"[Vv]ersión:? (\d+)", text)
if not match:
    raise SystemExit(f"No version found in {sys.argv[1]}")
print(match.group(1))
PY
}

render() {
  python3 - "$1" "$2" <<'PY'
import json
import os
import string
import sys

template, output = sys.argv[1:]
with open(template, encoding="utf-8") as stream:
    text = string.Template(stream.read()).substitute(os.environ)
json.loads(text)
with open(output, "w", encoding="utf-8") as stream:
    stream.write(text)
PY
}

write_search_headers() {
  local token
  token="$(az account get-access-token --resource https://search.azure.com --query accessToken --output tsv)"
  (umask 077 && printf 'Authorization: Bearer %s\nContent-Type: application/json\n' "$token" >"$WORK_DIR/headers.txt")
}

# Calls the search data plane. Waits while role assignments propagate (401/403)
# and accepts the extra HTTP statuses given as the fourth argument.
SEARCH_STATUS=""
search_request() {
  local method="$1"
  local path="$2"
  local body="${3:-}"
  local accepted="${4:-}"
  local separator="?"
  local deadline=$((SECONDS + ROLE_WAIT_SECONDS))
  [[ "$path" != *\?* ]] || separator="&"
  local url="$SEARCH_ENDPOINT/$path${separator}api-version=$SEARCH_API_VERSION"
  local args=(--silent --show-error --output "$WORK_DIR/response.json" --write-out '%{http_code}'
    --request "$method" --header "@$WORK_DIR/headers.txt")
  if [[ -n "$body" ]]; then
    args+=(--data-binary "@$body")
  elif [[ "$method" == "POST" ]]; then
    args+=(--data-binary '')
  fi
  while true; do
    write_search_headers
    SEARCH_STATUS="$(curl "${args[@]}" "$url")"
    case "$SEARCH_STATUS" in
      2??) return 0 ;;
      401|403)
        ((SECONDS < deadline)) || break
        printf '    %s %s: HTTP %s, waiting for the search role assignments...\n' "$method" "${path%%\?*}" "$SEARCH_STATUS"
        sleep 20
        ;;
      *)
        [[ " $accepted " != *" $SEARCH_STATUS "* ]] || return 0
        break
        ;;
    esac
  done
  printf 'ERROR: %s %s returned HTTP %s:\n' "$method" "${path%%\?*}" "$SEARCH_STATUS" >&2
  cat "$WORK_DIR/response.json" >&2 || true
  printf '\n' >&2
  exit 1
}

json_field() {
  python3 - "$WORK_DIR/response.json" "$1" <<'PY'
import json
import sys

value = json.load(open(sys.argv[1], encoding="utf-8"))
for key in sys.argv[2].split("."):
    value = (value or {}).get(key) if isinstance(value, dict) else None
print("" if value is None else value)
PY
}

indexer_state() {
  search_request GET "indexers/$1/status"
  python3 - "$WORK_DIR/response.json" <<'PY'
import json
import sys

last = json.load(open(sys.argv[1], encoding="utf-8")).get("lastResult") or {}
print(last.get("status") or "none", last.get("endTime") or "-")
PY
}

wait_until_idle() {
  local deadline=$((SECONDS + INDEXER_WAIT_SECONDS))
  local status end
  while true; do
    read -r status end < <(indexer_state "$1")
    case "$status" in
      inProgress|reset) ;;
      *) return 0 ;;
    esac
    ((SECONDS < deadline)) || fail "Indexer $1 is still running after ${INDEXER_WAIT_SECONDS}s"
    sleep 10
  done
}

# Reprocesses every document so changed content or definitions take effect.
reindex() {
  local indexer="$1"
  local status end previous_end
  wait_until_idle "$indexer"
  read -r status previous_end < <(indexer_state "$indexer")
  search_request POST "indexers/$indexer/reset"
  search_request POST "indexers/$indexer/run" "" "409"
  local deadline=$((SECONDS + INDEXER_WAIT_SECONDS))
  while true; do
    read -r status end < <(indexer_state "$indexer")
    # After a reset the latest entry is the reset itself: wait for the run.
    case "$status" in
      success|transientFailure|persistentFailure)
        [[ "$end" == "$previous_end" ]] || break
        ;;
    esac
    ((SECONDS < deadline)) || fail "Indexer $indexer did not finish in ${INDEXER_WAIT_SECONDS}s"
    sleep 10
  done
  search_request GET "indexers/$indexer/status"
  python3 - "$WORK_DIR/response.json" "$indexer" <<'PY'
import json
import sys

last = json.load(open(sys.argv[1], encoding="utf-8"))["lastResult"]
errors = [error.get("errorMessage") for error in last.get("errors") or []]
print(f"    {sys.argv[2]}: {last['status']}, {last.get('itemsProcessed', 0)} documents, "
      f"{last.get('itemsFailed', 0)} failed")
if last["status"] != "success" or last.get("itemsFailed") or not last.get("itemsProcessed"):
    raise SystemExit(f"Indexer {sys.argv[2]} failed: {errors or last.get('errorMessage')}")
PY
}

log "Checking the Foundry model for the knowledge base"
FOUNDRY_ACCOUNT_ID="${FOUNDRY_PROJECT_RESOURCE_ID%/projects/*}"
FOUNDRY_SUBDOMAIN="$(az resource show --ids "$FOUNDRY_ACCOUNT_ID" --query properties.customSubDomainName --output tsv)"
[[ -n "$FOUNDRY_SUBDOMAIN" ]] || fail "The Foundry resource has no custom subdomain"
# By resource ID, so the Foundry resource may live in another subscription.
KNOWLEDGE_BASE_MODEL_NAME="$(az resource show \
  --ids "$FOUNDRY_ACCOUNT_ID/deployments/$KNOWLEDGE_BASE_MODEL_DEPLOYMENT" \
  --query properties.model.name --output tsv)" ||
  fail "Model deployment $KNOWLEDGE_BASE_MODEL_DEPLOYMENT not found in the Foundry resource"
export FOUNDRY_OPENAI_ENDPOINT="https://$FOUNDRY_SUBDOMAIN.openai.azure.com"
export KNOWLEDGE_BASE_MODEL_NAME KNOWLEDGE_BASE_MODEL_DEPLOYMENT KNOWLEDGE_BASE_NAME
printf '    %s (%s) on %s\n' "$KNOWLEDGE_BASE_MODEL_DEPLOYMENT" "$KNOWLEDGE_BASE_MODEL_NAME" "$FOUNDRY_OPENAI_ENDPOINT"

log "Creating or updating the storage account"
if ! az storage account show --name "$STORAGE_ACCOUNT_NAME" \
  --resource-group "$KNOWLEDGE_RESOURCE_GROUP" >/dev/null 2>&1; then
  # Keyless: the search service reads it with its managed identity, over the
  # public endpoint, which is why public network access stays enabled.
  az storage account create --name "$STORAGE_ACCOUNT_NAME" \
    --resource-group "$KNOWLEDGE_RESOURCE_GROUP" --location "$KNOWLEDGE_LOCATION" \
    --kind StorageV2 --sku Standard_LRS --access-tier Hot --min-tls-version TLS1_2 \
    --allow-blob-public-access false --allow-shared-key-access false \
    --public-network-access Enabled
fi
az storage account update --name "$STORAGE_ACCOUNT_NAME" \
  --resource-group "$KNOWLEDGE_RESOURCE_GROUP" \
  --public-network-access Enabled --default-action Allow
export STORAGE_ACCOUNT_ID
STORAGE_ACCOUNT_ID="$(az storage account show --name "$STORAGE_ACCOUNT_NAME" \
  --resource-group "$KNOWLEDGE_RESOURCE_GROUP" --query id --output tsv)"

log "Creating or updating the Azure AI Search service"
if ! az search service show --name "$AZURE_SEARCH_SERVICE_NAME" \
  --resource-group "$KNOWLEDGE_RESOURCE_GROUP" >/dev/null 2>&1; then
  az search service create --name "$AZURE_SEARCH_SERVICE_NAME" \
    --resource-group "$KNOWLEDGE_RESOURCE_GROUP" --location "$KNOWLEDGE_LOCATION" \
    --sku "$AZURE_SEARCH_SKU" --partition-count 1 --replica-count 1 \
    --identity-type SystemAssigned --disable-local-auth true \
    --semantic-search free --knowledge-retrieval free --public-access enabled
elif [[ "$(az search service show --name "$AZURE_SEARCH_SERVICE_NAME" \
  --resource-group "$KNOWLEDGE_RESOURCE_GROUP" --query identity.type --output tsv)" != *SystemAssigned* ]]; then
  az search service update --name "$AZURE_SEARCH_SERVICE_NAME" \
    --resource-group "$KNOWLEDGE_RESOURCE_GROUP" --identity-type SystemAssigned
fi
SEARCH_ID="$(az search service show --name "$AZURE_SEARCH_SERVICE_NAME" \
  --resource-group "$KNOWLEDGE_RESOURCE_GROUP" --query id --output tsv)"
SEARCH_PRINCIPAL_ID="$(az search service show --name "$AZURE_SEARCH_SERVICE_NAME" \
  --resource-group "$KNOWLEDGE_RESOURCE_GROUP" --query identity.principalId --output tsv)"
readonly SEARCH_ENDPOINT="https://$AZURE_SEARCH_SERVICE_NAME.search.windows.net"

log "Assigning roles"
ensure_role "$SEARCH_PRINCIPAL_ID" ServicePrincipal "Storage Blob Data Reader" "$STORAGE_ACCOUNT_ID"
ensure_role "$SEARCH_PRINCIPAL_ID" ServicePrincipal "Cognitive Services User" "$FOUNDRY_ACCOUNT_ID"
ensure_role "$CALLER_ID" "$CALLER_TYPE" "Storage Blob Data Contributor" "$STORAGE_ACCOUNT_ID"
ensure_role "$CALLER_ID" "$CALLER_TYPE" "Search Service Contributor" "$SEARCH_ID"
ensure_role "$CALLER_ID" "$CALLER_TYPE" "Search Index Data Reader" "$SEARCH_ID"
if [[ -n "$AGENT_IDENTITY_NAME" ]]; then
  AGENT_PRINCIPAL_ID="$(az identity show --name "$AGENT_IDENTITY_NAME" \
    --resource-group "$AGENT_IDENTITY_RESOURCE_GROUP" --query principalId --output tsv)"
  ensure_role "$AGENT_PRINCIPAL_ID" ServicePrincipal "Search Index Data Reader" "$SEARCH_ID"
fi

# The recipe indexer guesses the encoding of text files: a UTF-8 byte order
# mark keeps the accents intact. Only the uploaded copies carry it.
with_bom() {
  local copy="$WORK_DIR/bom-$(basename "$1")"
  { printf '\xef\xbb\xbf'; cat "$1"; } >"$copy"
  printf '%s\n' "$copy"
}

log "Uploading the carta, the recipe book and the ingredient sheet"
carta_version="$(document_version "$CONTENT/menu/carta.md")"
recetario_version="$(document_version "$CONTENT/recipes/recetario.html")"
ingredientes_version="$(document_version "$CONTENT/ingredients/ingredientes.md")"
for container in carta recetario; do
  storage_command az storage container create --auth-mode login \
    --account-name "$STORAGE_ACCOUNT_NAME" --name "$container"
done
storage_command az storage blob upload --auth-mode login --account-name "$STORAGE_ACCOUNT_NAME" \
  --container-name carta --name carta.md --file "$(with_bom "$CONTENT/menu/carta.md")" --overwrite \
  --content-type "text/markdown; charset=utf-8" \
  --metadata doc_type=carta "version=$carta_version"
# Blob metadata must be ASCII; the index copies it into every recipe chunk.
storage_command az storage blob upload --auth-mode login --account-name "$STORAGE_ACCOUNT_NAME" \
  --container-name recetario --name recetario.pdf --file "$CONTENT/recipes/recetario.pdf" --overwrite \
  --content-type application/pdf \
  --metadata "title=Recetario de la casa (recetario.pdf)" doc_type=recetario "version=$recetario_version"
storage_command az storage blob upload --auth-mode login --account-name "$STORAGE_ACCOUNT_NAME" \
  --container-name recetario --name ingredientes.md --file "$(with_bom "$CONTENT/ingredients/ingredientes.md")" --overwrite \
  --content-type "text/markdown; charset=utf-8" \
  --metadata "title=Ingredientes de la casa (ingredientes.md)" doc_type=ingredientes "version=$ingredientes_version"
printf '    carta v%s, recetario v%s, ingredientes v%s\n' "$carta_version" "$recetario_version" "$ingredientes_version"

log "Creating or updating the recipe index pipeline"
for definition in recetario-datasource:datasources recetario-index:indexes \
  recetario-skillset:skillsets recetario-indexer:indexers; do
  name="${definition%%:*}"
  collection="${definition##*:}"
  render "$DEFINITIONS/$name.json" "$WORK_DIR/$name.json"
  search_request PUT "$collection/$name" "$WORK_DIR/$name.json"
done
reindex recetario-indexer

log "Creating or updating the knowledge sources"
for name in carta-knowledge-source recetario-knowledge-source web-knowledge-source; do
  render "$DEFINITIONS/$name.json" "$WORK_DIR/$name.json"
  source_name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$WORK_DIR/$name.json")"
  search_request PUT "knowledgesources/$source_name" "$WORK_DIR/$name.json"
done
search_request GET "knowledgesources/carta-de-la-casa"
CARTA_INDEXER="$(json_field azureBlobParameters.createdResources.indexer)"
[[ -n "$CARTA_INDEXER" ]] || fail "The carta knowledge source did not report its indexer"
reindex "$CARTA_INDEXER"

log "Creating or updating the knowledge base $KNOWLEDGE_BASE_NAME"
render "$DEFINITIONS/knowledge-base.json" "$WORK_DIR/knowledge-base.json"
search_request PUT "knowledgebases/$KNOWLEDGE_BASE_NAME" "$WORK_DIR/knowledge-base.json"

log "Checking the knowledge base"
python3 - "$WORK_DIR/retrieve.json" <<'PY'
import json
import sys

question = "¿Qué lleva la morcilla de Burgos de la casa?"
body = {"messages": [{"role": "user", "content": [{"type": "text", "text": question}]}], "includeActivity": True}
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump(body, stream, ensure_ascii=False)
PY
search_request POST "knowledgebases/$KNOWLEDGE_BASE_NAME/retrieve" "$WORK_DIR/retrieve.json"
python3 - "$WORK_DIR/response.json" <<'PY'
import json
import sys

result = json.load(open(sys.argv[1], encoding="utf-8"))
sources = sorted({item.get("knowledgeSourceName") for item in result.get("activity") or [] if item.get("knowledgeSourceName")})
references = result.get("references") or []
print(f"    retrieve: {len(references)} references from {', '.join(sources) or 'no source'}")
if not references:
    raise SystemExit("The knowledge base returned no references for the recipe question")
PY
mcp_status="$(curl --silent --show-error --output "$WORK_DIR/mcp.txt" --write-out '%{http_code}' \
  --request POST --header "@$WORK_DIR/headers.txt" --header 'Accept: application/json, text/event-stream' \
  --data-binary '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  "$SEARCH_ENDPOINT/knowledgebases/$KNOWLEDGE_BASE_NAME/mcp?api-version=$SEARCH_API_VERSION")"
[[ "$mcp_status" == "200" ]] && grep -q '"knowledge_base_retrieve"' "$WORK_DIR/mcp.txt" ||
  fail "The knowledge base MCP endpoint did not list knowledge_base_retrieve (HTTP $mcp_status)"
printf '    MCP: knowledge_base_retrieve available\n'

cat <<SUMMARY

Knowledge base ready: $KNOWLEDGE_BASE_NAME
MCP endpoint: $SEARCH_ENDPOINT/knowledgebases/$KNOWLEDGE_BASE_NAME/mcp?api-version=$SEARCH_API_VERSION

To connect the deployed waiter, add to scripts/container-apps.env:
  AZURE_SEARCH_RESOURCE_ID="$SEARCH_ID"
  AZURE_SEARCH_ENDPOINT="$SEARCH_ENDPOINT"
  KNOWLEDGE_BASE_NAME="$KNOWLEDGE_BASE_NAME"

To connect a local waiter, export or add to its .env:
  AZURE_SEARCH_ENDPOINT="$SEARCH_ENDPOINT"
  KNOWLEDGE_BASE_NAME="$KNOWLEDGE_BASE_NAME"
SUMMARY
