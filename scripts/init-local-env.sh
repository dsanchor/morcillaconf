#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
default_output="$repo_root/.local/restaurant.env.sh"
output_path="$default_output"
project_endpoint="${FOUNDRY_PROJECT_ENDPOINT:-}"
model_deployment="${AZURE_AI_MODEL_DEPLOYMENT_NAME:-gpt-5.6-luna}"
actor_id="${DEV_FAKE_ACTOR_ID:-}"
force=false

usage() {
  cat <<'EOF'
Uso:
  ./scripts/init-local-env.sh [opciones]

Opciones:
  --project-endpoint URL   Endpoint del proyecto Microsoft Foundry.
  --actor-id NOMBRE       Identidad y nombre del cliente local.
  --model NOMBRE          Deployment del modelo (por defecto: gpt-5.6-luna).
  --output RUTA           Fichero generado.
  --force                 Sobrescribe el fichero si ya existe.
  -h, --help              Muestra esta ayuda.

Si faltan el endpoint o la identidad, el script los solicita por terminal.
EOF
}

while (($# > 0)); do
  case "$1" in
    --project-endpoint)
      project_endpoint="${2:?Falta el valor de --project-endpoint}"
      shift 2
      ;;
    --actor-id)
      actor_id="${2:?Falta el valor de --actor-id}"
      shift 2
      ;;
    --model)
      model_deployment="${2:?Falta el valor de --model}"
      shift 2
      ;;
    --output)
      output_path="${2:?Falta el valor de --output}"
      shift 2
      ;;
    --force)
      force=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Opción desconocida: %s\n\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$project_endpoint" ]]; then
  if [[ ! -t 0 ]]; then
    printf 'FOUNDRY_PROJECT_ENDPOINT es obligatorio.\n' >&2
    exit 1
  fi
  read -r -p "Endpoint del proyecto Microsoft Foundry: " project_endpoint
fi

if [[ -z "$actor_id" ]]; then
  if [[ ! -t 0 ]]; then
    printf 'DEV_FAKE_ACTOR_ID es obligatorio.\n' >&2
    exit 1
  fi
  read -r -p "Nombre del cliente local: " actor_id
fi

if [[ "$project_endpoint" != https://* ]]; then
  printf 'El endpoint debe comenzar por https://\n' >&2
  exit 1
fi
if [[ -z "${actor_id//[[:space:]]/}" ]]; then
  printf 'La identidad local no puede estar vacía.\n' >&2
  exit 1
fi
if [[ -e "$output_path" && "$force" != true ]]; then
  printf 'El fichero ya existe: %s\n' "$output_path" >&2
  printf 'Usa --force si quieres sobrescribirlo.\n' >&2
  exit 1
fi

mkdir -p "$(dirname "$output_path")"
memory_path="$repo_root/agents/restaurant/src/restaurant_agent/data/memory.db"

{
  printf '# Generado por scripts/init-local-env.sh. No versionar.\n'
  printf 'export FOUNDRY_PROJECT_ENDPOINT=%q\n' "$project_endpoint"
  printf 'export AZURE_AI_MODEL_DEPLOYMENT_NAME=%q\n' "$model_deployment"
  printf 'export WAITER_MAX_TURNS=%q\n' "20"
  printf 'export MEMORY_DATABASE_PATH=%q\n' "$memory_path"
  printf 'export MEMORY_MAX_ITEMS=%q\n' "20"
  printf 'export APP_ENVIRONMENT=%q\n' "development"
  printf 'export ENABLE_DEV_FAKE_IDENTITY=%q\n' "true"
  printf 'export DEV_FAKE_ACTOR_ID=%q\n' "$actor_id"
  printf 'export DEV_FAKE_MEMORY_CONSENT=%q\n' "true"
} >"$output_path"

chmod 600 "$output_path"

printf 'Configuración creada en:\n  %s\n\n' "$output_path"
printf 'Antes de arrancar el agente ejecuta:\n  source %q\n' "$output_path"
printf '  ./scripts/run-local.sh\n'
