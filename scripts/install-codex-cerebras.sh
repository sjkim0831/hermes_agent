#!/usr/bin/env bash
set -euo pipefail

# Install a pinned Codex CLI runtime used only by the codex-cerebras launcher.
# This keeps the Cerebras bridge stable even when the user's normal Codex CLI
# is upgraded independently.

CODEX_CEREBRAS_VERSION="${CODEX_CEREBRAS_VERSION:-0.1.2505172129}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
RUNTIME_DIR="${CODEX_CEREBRAS_RUNTIME_DIR:-$CODEX_HOME/cerebras-legacy-cli}"
LEGACY_HOME="${CODEX_CEREBRAS_HOME:-$CODEX_HOME/cerebras-legacy-home}"
BIN_DIR="${CODEX_CEREBRAS_BIN_DIR:-$HOME/.local/bin}"
LAUNCHER="$BIN_DIR/codex-cerebras"

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required to install the pinned Codex runtime." >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR" "$LEGACY_HOME/.codex" "$BIN_DIR"

cat > "$RUNTIME_DIR/package.json" <<JSON
{
  "name": "codex-cerebras-launcher",
  "private": true,
  "version": "1.0.0",
  "description": "Pinned legacy Codex CLI runtime for Cerebras chat-completions provider",
  "license": "UNLICENSED",
  "dependencies": {
    "@openai/codex": "$CODEX_CEREBRAS_VERSION"
  }
}
JSON

npm install --prefix "$RUNTIME_DIR"

cat > "$LEGACY_HOME/.codex/config.json" <<'JSON'
{
  "provider": "cerebras",
  "model": "qwen-3-235b-a22b-instruct-2507",
  "providers": {
    "cerebras": {
      "name": "Cerebras",
      "baseURL": "https://api.cerebras.ai/v1",
      "envKey": "CEREBRAS_API_KEY"
    }
  }
}
JSON

cat > "$LAUNCHER" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
LEGACY_HOME="${CODEX_CEREBRAS_HOME:-$CODEX_HOME/cerebras-legacy-home}"
LEGACY_CLI_DIR="${CODEX_CEREBRAS_RUNTIME_DIR:-$CODEX_HOME/cerebras-legacy-cli}"
LEGACY_CODEX="$LEGACY_CLI_DIR/node_modules/.bin/codex"

if [[ ! -x "$LEGACY_CODEX" && -d /mnt/c/Users ]]; then
  for candidate_home in /mnt/c/Users/*/.codex; do
    candidate_codex="$candidate_home/cerebras-legacy-cli/node_modules/.bin/codex"
    if [[ -x "$candidate_codex" ]]; then
      LEGACY_HOME="${CODEX_CEREBRAS_HOME:-$candidate_home/cerebras-legacy-home}"
      LEGACY_CLI_DIR="${CODEX_CEREBRAS_RUNTIME_DIR:-$candidate_home/cerebras-legacy-cli}"
      LEGACY_CODEX="$candidate_codex"
      break
    fi
  done
fi

if [[ ! -x "$LEGACY_CODEX" ]]; then
  echo "codex-cerebras is not installed yet." >&2
  echo "Run: scripts/install-codex-cerebras.sh" >&2
  exit 1
fi

if [[ -z "${CEREBRAS_API_KEY:-}" ]]; then
  echo "Missing CEREBRAS_API_KEY." >&2
  echo "Export it first, for example:" >&2
  echo "  export CEREBRAS_API_KEY=your_key_here" >&2
  exit 1
fi

export OPENAI_API_KEY="${OPENAI_API_KEY:-$CEREBRAS_API_KEY}"

if [[ -n "${CODEX_CEREBRAS_BASE_URL:-}" ]]; then
  export RUNTIME_HOME
  RUNTIME_HOME="$(mktemp -d)"
  mkdir -p "$RUNTIME_HOME/.codex"
  python3 - <<'PY'
import json
import os
from pathlib import Path

cfg = {
    "provider": "cerebras",
    "model": "qwen-3-235b-a22b-instruct-2507",
    "providers": {
        "cerebras": {
            "name": "Cerebras",
            "baseURL": os.environ["CODEX_CEREBRAS_BASE_URL"],
            "envKey": "CEREBRAS_API_KEY",
        }
    },
}
Path(os.environ["RUNTIME_HOME"], ".codex", "config.json").write_text(
    json.dumps(cfg, indent=2),
    encoding="utf-8",
)
PY
  export HOME="$RUNTIME_HOME"
else
  export HOME="$LEGACY_HOME"
fi

exec "$LEGACY_CODEX" --provider cerebras "$@"
SH

chmod +x "$LAUNCHER"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    echo "Installed $LAUNCHER"
    echo "Add this to your shell profile if needed:"
    echo "  export PATH=\"$BIN_DIR:\$PATH\""
    ;;
esac

echo "codex-cerebras installed:"
echo "  $LAUNCHER"
