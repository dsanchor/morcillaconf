#!/usr/bin/env bash
# Prints the recipe book HTML to PDF with headless Chromium (no npm or pip).
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_html="$repo_root/data/knowledge/recipes/recetario.html"
output_pdf="$repo_root/data/knowledge/recipes/recetario.pdf"

usage() {
  cat <<'USAGE'
Uso:
  ./scripts/build-recetario-pdf.sh

Genera data/knowledge/recipes/recetario.pdf a partir de recetario.html con
Chromium sin interfaz. Versiona el HTML y el PDF juntos.

Busca, por este orden: CHROME_BIN, chrome-headless-shell de Playwright en su
caché, y chrome-headless-shell, chromium o google-chrome en el PATH.
USAGE
}

case "${1:-}" in
  "") ;;
  -h|--help) usage; exit 0 ;;
  *) printf 'Opción desconocida: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
esac

find_chrome() {
  if [[ -n "${CHROME_BIN:-}" ]]; then
    printf '%s\n' "$CHROME_BIN"
    return 0
  fi
  local candidates=() candidate
  for candidate in \
    "$HOME"/Library/Caches/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-mac-*/chrome-headless-shell \
    "$HOME"/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux*/chrome-headless-shell; do
    [[ -x "$candidate" ]] && candidates+=("$candidate")
  done
  if ((${#candidates[@]} > 0)); then
    # The glob sorts by version: take the newest installed build.
    printf '%s\n' "${candidates[${#candidates[@]}-1]}"
    return 0
  fi
  for candidate in chrome-headless-shell chromium chromium-browser google-chrome; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

chrome="$(find_chrome)" || {
  printf 'No encuentro Chromium. Define CHROME_BIN con la ruta del ejecutable.\n' >&2
  exit 1
}
[[ -f "$source_html" ]] || { printf 'No existe %s\n' "$source_html" >&2; exit 1; }

profile="$(mktemp -d)"
trap 'rm -rf "$profile"' EXIT

"$chrome" --headless --disable-gpu --no-first-run --user-data-dir="$profile" \
  --no-pdf-header-footer --export-tagged-pdf \
  --print-to-pdf="$output_pdf" "file://$source_html" >/dev/null 2>&1

[[ -s "$output_pdf" ]] || { printf 'Chromium no generó el PDF.\n' >&2; exit 1; }
printf 'PDF generado: %s (%s bytes)\n' "${output_pdf#"$repo_root"/}" "$(wc -c <"$output_pdf" | tr -d ' ')"
