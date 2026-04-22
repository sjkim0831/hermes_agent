#!/usr/bin/env bash
set -euo pipefail

# Install a pinned Codex CLI runtime used only by the codex-gemini launcher.
# This keeps the Gemini bridge stable even when the user's normal Codex CLI
# is upgraded independently.

CODEX_GEMINI_VERSION="${CODEX_GEMINI_VERSION:-0.1.2505172129}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
RUNTIME_DIR="${CODEX_GEMINI_RUNTIME_DIR:-$CODEX_HOME/gemini-legacy-cli}"
LEGACY_HOME="${CODEX_GEMINI_HOME:-$CODEX_HOME/gemini-legacy-home}"
BIN_DIR="${CODEX_GEMINI_BIN_DIR:-$HOME/.local/bin}"
LAUNCHER="$BIN_DIR/codex-gemini"

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required to install the pinned Codex runtime." >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR" "$LEGACY_HOME/.codex" "$BIN_DIR"

cat > "$RUNTIME_DIR/package.json" <<JSON
{
  "name": "codex-gemini-launcher",
  "private": true,
  "version": "1.0.0",
  "description": "Pinned legacy Codex CLI runtime for Gemini chat-completions provider",
  "license": "UNLICENSED",
  "dependencies": {
    "@openai/codex": "$CODEX_GEMINI_VERSION"
  }
}
JSON

npm install --prefix "$RUNTIME_DIR"

cat > "$LEGACY_HOME/.codex/config.json" <<'JSON'
{
  "provider": "gemini",
  "model": "gemini-2.5-flash",
  "providers": {
    "gemini": {
      "name": "Gemini",
      "baseURL": "https://generativelanguage.googleapis.com/v1beta/openai",
      "envKey": "GEMINI_API_KEY"
    }
  }
}
JSON

cat > "$LAUNCHER" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
LEGACY_HOME="${CODEX_GEMINI_HOME:-$CODEX_HOME/gemini-legacy-home}"
LEGACY_CLI_DIR="${CODEX_GEMINI_RUNTIME_DIR:-$CODEX_HOME/gemini-legacy-cli}"
LEGACY_CODEX="$LEGACY_CLI_DIR/node_modules/.bin/codex"

if [[ ! -x "$LEGACY_CODEX" ]]; then
  echo "codex-gemini is not installed yet." >&2
  echo "Run: scripts/install-codex-gemini.sh" >&2
  exit 1
fi

if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  echo "Missing GEMINI_API_KEY." >&2
  echo "Export it first, for example:" >&2
  echo "  export GEMINI_API_KEY=your_key_here" >&2
  exit 1
fi

export OPENAI_API_KEY="${OPENAI_API_KEY:-$GEMINI_API_KEY}"

if [[ -n "${CODEX_GEMINI_BASE_URL:-}" ]]; then
  export RUNTIME_HOME
  RUNTIME_HOME="$(mktemp -d)"
  mkdir -p "$RUNTIME_HOME/.codex"
  python3 - <<'PY'
import json
import os
from pathlib import Path

cfg = {
    "provider": "gemini",
    "model": "gemini-2.5-flash",
    "providers": {
        "gemini": {
            "name": "Gemini",
            "baseURL": os.environ["CODEX_GEMINI_BASE_URL"],
            "envKey": "GEMINI_API_KEY",
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

exec "$LEGACY_CODEX" --provider gemini "$@"
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

echo "codex-gemini installed:"
echo "  $LAUNCHER"
