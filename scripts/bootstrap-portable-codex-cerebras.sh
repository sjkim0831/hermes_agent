#!/usr/bin/env bash
set -euo pipefail

# One-command setup for a fresh machine after cloning this Hermes fork.
# Assumes a normal developer machine with Python 3.11+ and Node/npm available.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$ROOT"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Missing Python. Install Python 3.11+ first." >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "Missing npm. Install Node.js/npm or Codex CLI's Node runtime first." >&2
  exit 1
fi

if [[ ! -d venv ]]; then
  "$PYTHON_BIN" -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[cli]"

if [[ -d ui-tui ]]; then
  npm install --prefix ui-tui
  npm run build --prefix ui-tui
fi

bash scripts/install-hermes-codex-cerebras.sh
bash scripts/doctor-codex-cerebras-hermes.sh

echo
echo "Portable Hermes + codex-cerebras setup complete."
echo "Fill ~/.hermes/.env with your CEREBRAS_API_KEY_101... values, then run:"
echo "  cd $ROOT"
echo "  source venv/bin/activate"
echo "  hermes --tui"
