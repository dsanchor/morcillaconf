#!/usr/bin/env bash
# Asks the knowledge base one question, over REST (retrieve) or its MCP endpoint.
set -euo pipefail

readonly API_VERSION="2026-08-01-preview"

usage() {
  cat <<'USAGE'
Uso:
  ./scripts/query-knowledge.sh [--mcp] "pregunta"

Consulta la base de conocimiento indicada por AZURE_SEARCH_ENDPOINT y
KNOWLEDGE_BASE_NAME con la sesión de Azure CLI, que necesita el rol
Search Index Data Reader sobre el servicio de búsqueda.

Sin opciones usa la acción retrieve y muestra qué fuentes se consultaron y las
referencias devueltas. Con --mcp llama a knowledge_base_retrieve por el
endpoint MCP de la base, igual que el camarero.
USAGE
}

mode=rest
question=""
while (($# > 0)); do
  case "$1" in
    --mcp) mode=mcp; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) printf 'Opción desconocida: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    *) question="$1"; shift ;;
  esac
done
[[ -n "$question" ]] || { usage >&2; exit 2; }
: "${AZURE_SEARCH_ENDPOINT:?Define AZURE_SEARCH_ENDPOINT, por ejemplo https://<servicio>.search.windows.net}"
: "${KNOWLEDGE_BASE_NAME:?Define KNOWLEDGE_BASE_NAME}"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
token="$(az account get-access-token --resource https://search.azure.com --query accessToken --output tsv)"
(umask 077 && printf 'Authorization: Bearer %s\nContent-Type: application/json\n' "$token" >"$work/headers.txt")
base="${AZURE_SEARCH_ENDPOINT%/}/knowledgebases/$KNOWLEDGE_BASE_NAME"

if [[ "$mode" == "rest" ]]; then
  python3 - "$question" >"$work/body.json" <<'PY'
import json
import sys

print(json.dumps({
    "messages": [{"role": "user", "content": [{"type": "text", "text": sys.argv[1]}]}],
    "includeActivity": True,
}, ensure_ascii=False))
PY
  status="$(curl --silent --show-error --output "$work/response.json" --write-out '%{http_code}' \
    --request POST --header "@$work/headers.txt" --data-binary "@$work/body.json" \
    "$base/retrieve?api-version=$API_VERSION")"
  [[ "$status" == 2?? ]] || { printf 'HTTP %s\n' "$status" >&2; cat "$work/response.json" >&2; exit 1; }
  python3 - "$work/response.json" <<'PY'
import json
import sys

result = json.load(open(sys.argv[1], encoding="utf-8"))
print("Fuentes consultadas:")
for item in result.get("activity") or []:
    if item.get("knowledgeSourceName"):
        print(f"  - {item['knowledgeSourceName']} ({item.get('type')}): "
              f"{item.get('count', '?')} resultados, {item.get('elapsedMs', '?')} ms")
print("Referencias:")
for reference in result.get("references") or []:
    where = reference.get("url") or reference.get("blobUrl") or reference.get("docKey") or ""
    title = reference.get("title") or ""
    print(f"  [{reference.get('id')}] {reference.get('type')} · {title} {where}".rstrip())
for message in result.get("response") or []:
    for content in message.get("content") or []:
        text = content.get("text") or ""
        print("Contenido (inicio):")
        print("  " + text[:800].replace("\n", "\n  "))
PY
else
  python3 - "$question" >"$work/body.json" <<'PY'
import json
import sys

print(json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
    "params": {"name": "knowledge_base_retrieve", "arguments": {"query_variants": [sys.argv[1]]}},
}, ensure_ascii=False))
PY
  status="$(curl --silent --show-error --output "$work/response.txt" --write-out '%{http_code}' \
    --request POST --header "@$work/headers.txt" --header 'Accept: application/json, text/event-stream' \
    --data-binary "@$work/body.json" "$base/mcp?api-version=$API_VERSION")"
  [[ "$status" == 2?? ]] || { printf 'HTTP %s\n' "$status" >&2; cat "$work/response.txt" >&2; exit 1; }
  python3 - "$work/response.txt" <<'PY'
import json
import sys

raw = open(sys.argv[1], encoding="utf-8").read()
payloads = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")] or [raw]
for payload in payloads:
    message = json.loads(payload)
    result = message.get("result") or {}
    if message.get("error") or result.get("isError"):
        print(json.dumps(message, ensure_ascii=False, indent=2))
        continue
    for content in result.get("content") or []:
        text = content.get("text") or ""
        try:
            print(json.dumps(json.loads(text), ensure_ascii=False, indent=2)[:3000])
        except ValueError:
            print(text[:3000])
PY
fi
