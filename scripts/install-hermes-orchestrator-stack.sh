#!/usr/bin/env bash
set -euo pipefail

# Install the portable Hermes Codex stack used in this fork:
# - editable Hermes Python package with LangGraph orchestration extra
# - codex-cerebras and codex-gemini pinned launchers
# - multi-account Cerebras/Gemini provider config
# - TUI build containing /orchestrate and /quota
# - doctor verification
#
# Usage:
#   bash scripts/install-hermes-orchestrator-stack.sh
#
# Optional env:
#   HERMES_HOME=$HOME/.hermes
#   HERMES_VENV=/opt/util/hermes/venv
#   SKIP_NPM_BUILD=1
#   SKIP_CODEX_LAUNCHERS=1
#   CEREBRAS_ACCOUNT_COUNT=19
#   GEMINI_ACCOUNT_COUNT=19

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_VENV="${HERMES_VENV:-$ROOT/venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

log() {
  printf '[hermes-stack] %s\n' "$*"
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

need_cmd "$PYTHON_BIN"
need_cmd npm

cd "$ROOT"
mkdir -p "$HERMES_HOME"

if [[ ! -x "$HERMES_VENV/bin/python" ]]; then
  log "creating venv: $HERMES_VENV"
  "$PYTHON_BIN" -m venv "$HERMES_VENV"
fi

# shellcheck disable=SC1091
source "$HERMES_VENV/bin/activate"

log "upgrading pip tooling"
python -m pip install --upgrade pip setuptools wheel

log "installing Hermes editable package with orchestration extra"
python -m pip install -e '.[orchestration]'

if [[ "${SKIP_CODEX_LAUNCHERS:-0}" != "1" ]]; then
  log "installing codex-cerebras/codex-gemini launchers and provider config"
  INSTALL_CODEX_CEREBRAS=1 INSTALL_CODEX_GEMINI=1 "$ROOT/scripts/install-hermes-codex-cerebras.sh"
else
  log "skipping codex launchers by SKIP_CODEX_LAUNCHERS=1"
  INSTALL_CODEX_CEREBRAS=0 INSTALL_CODEX_GEMINI=0 "$ROOT/scripts/install-hermes-codex-cerebras.sh"
fi

if [[ "${SKIP_NPM_BUILD:-0}" != "1" ]]; then
  log "installing/building TUI"
  npm --prefix "$ROOT/ui-tui" install
  npm --prefix "$ROOT/ui-tui" run build
else
  log "skipping TUI build by SKIP_NPM_BUILD=1"
fi

log "running doctor"
"$ROOT/scripts/doctor-codex-cerebras-hermes.sh"

cat <<EOF

Hermes orchestrator stack installed.

Run:
  cd "$ROOT"
  source "$HERMES_VENV/bin/activate"
  hermes --tui

Useful TUI commands:
  /provider
  /routing
  /orchestrate --dry-run <task>
  /orchestrate <task>
  /quota
  /quota reset cerebras

Secrets live in:
  $HERMES_HOME/.env

EOF
