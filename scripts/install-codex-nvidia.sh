#!/usr/bin/env bash
set -euo pipefail

# Install a pinned Codex CLI runtime used only by the codex-nvidia launcher.
# This mirrors the codex-gemini bridge and targets NVIDIA's OpenAI-compatible
# chat-completions endpoint.

CODEX_NVIDIA_VERSION="${CODEX_NVIDIA_VERSION:-0.1.2505172129}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
RUNTIME_DIR="${CODEX_NVIDIA_RUNTIME_DIR:-$CODEX_HOME/nvidia-legacy-cli}"
LEGACY_HOME="${CODEX_NVIDIA_HOME:-$CODEX_HOME/nvidia-legacy-home}"
BIN_DIR="${CODEX_NVIDIA_BIN_DIR:-$HOME/.local/bin}"
LAUNCHER="$BIN_DIR/codex-nvidia"
NVIDIA_BASE_URL="${NVIDIA_BASE_URL:-https://integrate.api.nvidia.com/v1}"
NVIDIA_MODEL="${CODEX_NVIDIA_MODEL:-minimaxai/minimax-m2.7}"
export LEGACY_HOME NVIDIA_BASE_URL NVIDIA_MODEL

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required to install the pinned Codex runtime." >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR" "$LEGACY_HOME/.codex" "$BIN_DIR"

cat > "$RUNTIME_DIR/package.json" <<JSON
{
  "name": "codex-nvidia-launcher",
  "private": true,
  "version": "1.0.0",
  "description": "Pinned legacy Codex CLI runtime for NVIDIA chat-completions provider",
  "license": "UNLICENSED",
  "dependencies": {
    "@openai/codex": "$CODEX_NVIDIA_VERSION"
  }
}
JSON

npm install --prefix "$RUNTIME_DIR"

python3 - <<'PY'
import json
import os
from pathlib import Path

cfg = {
    "provider": "nvidia",
    "model": os.environ.get("CODEX_NVIDIA_MODEL", "minimaxai/minimax-m2.7"),
    "providers": {
        "nvidia": {
            "name": "NVIDIA",
            "baseURL": os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
            "envKey": "NVIDIA_API_KEY",
        }
    },
}
Path(os.environ["LEGACY_HOME"], ".codex", "config.json").write_text(
    json.dumps(cfg, indent=2),
    encoding="utf-8",
)
PY

cat > "$LAUNCHER" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
LEGACY_HOME="${CODEX_NVIDIA_HOME:-$CODEX_HOME/nvidia-legacy-home}"
LEGACY_CLI_DIR="${CODEX_NVIDIA_RUNTIME_DIR:-$CODEX_HOME/nvidia-legacy-cli}"
LEGACY_CODEX="$LEGACY_CLI_DIR/node_modules/.bin/codex"
NODE22="/home/sjkim/.local/node-v22.22.2-linux-x64/bin"

set -a
[[ -f /opt/util/hermes/.env ]] && . /opt/util/hermes/.env
[[ -f "$HOME/.hermes/.env" ]] && . "$HOME/.hermes/.env"
set +a

if [[ ! -x "$LEGACY_CODEX" ]]; then
  echo "codex-nvidia is not installed yet." >&2
  echo "Run: scripts/install-codex-nvidia.sh" >&2
  exit 1
fi

if [[ -z "${NVIDIA_API_KEY:-}" ]]; then
  echo "Missing NVIDIA_API_KEY." >&2
  echo "Export it first, for example:" >&2
  echo "  export NVIDIA_API_KEY=your_key_here" >&2
  exit 1
fi

export OPENAI_API_KEY="${OPENAI_API_KEY:-$NVIDIA_API_KEY}"
export HOME="$LEGACY_HOME"
if [[ -x "$NODE22/node" ]]; then
  export PATH="$NODE22:$PATH"
fi

exec "$LEGACY_CODEX" --provider nvidia "$@"
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

echo "codex-nvidia installed:"
echo "  $LAUNCHER"
