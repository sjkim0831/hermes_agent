#!/usr/bin/env bash
set -euo pipefail

# Install a pinned Codex CLI runtime used only by the codex-mistral launcher.

CODEX_MISTRAL_VERSION="${CODEX_MISTRAL_VERSION:-0.1.2505172129}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
RUNTIME_DIR="${CODEX_MISTRAL_RUNTIME_DIR:-$CODEX_HOME/mistral-legacy-cli}"
LEGACY_HOME="${CODEX_MISTRAL_HOME:-$CODEX_HOME/mistral-legacy-home}"
BIN_DIR="${CODEX_MISTRAL_BIN_DIR:-$HOME/.local/bin}"
LAUNCHER="$BIN_DIR/codex-mistral"
ALIAS="$BIN_DIR/codex-ministal"
MISTRAL_BASE_URL="${MISTRAL_BASE_URL:-https://api.mistral.ai/v1}"
MISTRAL_MODEL="${CODEX_MISTRAL_MODEL:-mistral-medium-latest}"
export LEGACY_HOME MISTRAL_BASE_URL MISTRAL_MODEL

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required to install the pinned Codex runtime." >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR" "$LEGACY_HOME/.codex" "$BIN_DIR"

cat > "$RUNTIME_DIR/package.json" <<JSON
{
  "name": "codex-mistral-launcher",
  "private": true,
  "version": "1.0.0",
  "description": "Pinned legacy Codex CLI runtime for Mistral chat-completions provider",
  "license": "UNLICENSED",
  "dependencies": {
    "@openai/codex": "$CODEX_MISTRAL_VERSION"
  }
}
JSON

npm install --prefix "$RUNTIME_DIR"

python3 - <<'PY'
import json
import os
from pathlib import Path

cfg = {
    "provider": "mistral",
    "model": os.environ.get("CODEX_MISTRAL_MODEL", "mistral-medium-latest"),
    "providers": {
        "mistral": {
            "name": "Mistral",
            "baseURL": os.environ.get("MISTRAL_BASE_URL", "https://api.mistral.ai/v1"),
            "envKey": "MISTRAL_API_KEY",
        }
    },
}
Path(os.environ["LEGACY_HOME"], ".codex", "config.json").write_text(
    json.dumps(cfg, indent=2),
    encoding="utf-8",
)
PY

if [[ "/home/sjkim/.local/bin/codex-mistral" != "$LAUNCHER" ]]; then
  cp /home/sjkim/.local/bin/codex-mistral "$LAUNCHER"
fi
cat > "$ALIAS" <<'SH'
#!/usr/bin/env bash
exec /home/sjkim/.local/bin/codex-mistral "$@"
SH
chmod +x "$LAUNCHER" "$ALIAS"

echo "codex-mistral installed:"
echo "  $LAUNCHER"
echo "  $ALIAS"
