#!/usr/bin/env bash
set -euo pipefail

# Configure Hermes for a local Ollama sidecar and optionally pull recommended
# models for the current machine.
#
# This script does not vendor Ollama into git. It wires Hermes to a local
# OpenAI-compatible Ollama endpoint and stores the routing in ~/.hermes/config.yaml.
#
# Optional env:
#   HERMES_HOME=$HOME/.hermes
#   HERMES_CONFIG_PATH=$HERMES_HOME/config.yaml
#   HERMES_ENV_PATH=$HERMES_HOME/.env
#   OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
#   OLLAMA_WORKER_COUNT=3
#   OLLAMA_SEARCH_MODEL=qwen2.5-coder:14b-instruct
#   OLLAMA_REASON_MODEL=qwen2.5-coder:32b-instruct
#   OLLAMA_FALLBACK_MODEL=llama3.1:8b-instruct
#   OLLAMA_DEFAULT_MODEL=qwen2.5-coder:14b-instruct
#   INSTALL_OLLAMA_BINARY=1
#   PULL_OLLAMA_MODELS=1
#   OLLAMA_INSTALL_SCOPE=user|system (default: user)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
CONFIG_PATH="${HERMES_CONFIG_PATH:-$HERMES_HOME/config.yaml}"
ENV_PATH="${HERMES_ENV_PATH:-$HERMES_HOME/.env}"
OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434/v1}"
OLLAMA_WORKER_COUNT="${OLLAMA_WORKER_COUNT:-3}"
OLLAMA_SEARCH_MODEL="${OLLAMA_SEARCH_MODEL:-qwen2.5-coder:14b-instruct}"
OLLAMA_REASON_MODEL="${OLLAMA_REASON_MODEL:-qwen2.5-coder:32b-instruct}"
OLLAMA_FALLBACK_MODEL="${OLLAMA_FALLBACK_MODEL:-llama3.1:8b-instruct}"
OLLAMA_DEFAULT_MODEL="${OLLAMA_DEFAULT_MODEL:-$OLLAMA_SEARCH_MODEL}"
OLLAMA_INSTALL_SCOPE="${OLLAMA_INSTALL_SCOPE:-user}"

install_ollama_user_local() {
  local install_root="$HOME/.local"
  local archive="/tmp/ollama-linux-amd64.tar.zst"
  mkdir -p "$install_root"
  echo "[ollama-stack] downloading Ollama into $install_root"
  curl -fL https://github.com/ollama/ollama/releases/latest/download/ollama-linux-amd64.tar.zst -o "$archive"
  tar -I zstd -xf "$archive" -C "$install_root"
  rm -f "$archive"
}

mkdir -p "$HERMES_HOME"

if [[ "${INSTALL_OLLAMA_BINARY:-0}" == "1" ]] && ! command -v ollama >/dev/null 2>&1; then
  echo "[ollama-stack] installing Ollama binary (scope=$OLLAMA_INSTALL_SCOPE)"
  if [[ "$OLLAMA_INSTALL_SCOPE" == "system" ]] && command -v sudo >/dev/null 2>&1; then
    curl -fsSL https://ollama.com/install.sh | sh
  else
    install_ollama_user_local
  fi
fi

if [[ ! -f "$ENV_PATH" ]]; then
  cat > "$ENV_PATH" <<EOF
OLLAMA_BASE_URL=$OLLAMA_BASE_URL
OLLAMA_API_KEY=ollama
OLLAMA_DEFAULT_MODEL=$OLLAMA_DEFAULT_MODEL
OLLAMA_SEARCH_MODEL=$OLLAMA_SEARCH_MODEL
OLLAMA_REASON_MODEL=$OLLAMA_REASON_MODEL
OLLAMA_FALLBACK_MODEL=$OLLAMA_FALLBACK_MODEL
EOF
else
  for key in OLLAMA_BASE_URL OLLAMA_API_KEY OLLAMA_DEFAULT_MODEL OLLAMA_SEARCH_MODEL OLLAMA_REASON_MODEL OLLAMA_FALLBACK_MODEL; do
    if ! grep -q "^${key}=" "$ENV_PATH"; then
      case "$key" in
        OLLAMA_BASE_URL) value="$OLLAMA_BASE_URL" ;;
        OLLAMA_API_KEY) value="ollama" ;;
        OLLAMA_DEFAULT_MODEL) value="$OLLAMA_DEFAULT_MODEL" ;;
        OLLAMA_SEARCH_MODEL) value="$OLLAMA_SEARCH_MODEL" ;;
        OLLAMA_REASON_MODEL) value="$OLLAMA_REASON_MODEL" ;;
        OLLAMA_FALLBACK_MODEL) value="$OLLAMA_FALLBACK_MODEL" ;;
      esac
      printf '\n%s=%s\n' "$key" "$value" >> "$ENV_PATH"
    fi
  done
fi

export CONFIG_PATH OLLAMA_BASE_URL OLLAMA_WORKER_COUNT OLLAMA_SEARCH_MODEL OLLAMA_REASON_MODEL OLLAMA_FALLBACK_MODEL OLLAMA_DEFAULT_MODEL
python3 - <<'PY'
import os
from pathlib import Path

import yaml

config_path = Path(os.environ["CONFIG_PATH"])
base_url = str(os.environ["OLLAMA_BASE_URL"] or "").strip()
worker_count = max(1, min(4, int(os.environ.get("OLLAMA_WORKER_COUNT", "3") or "3")))
search_model = str(os.environ["OLLAMA_SEARCH_MODEL"] or "").strip()
reason_model = str(os.environ["OLLAMA_REASON_MODEL"] or "").strip()
fallback_model = str(os.environ["OLLAMA_FALLBACK_MODEL"] or "").strip()
default_model = str(os.environ["OLLAMA_DEFAULT_MODEL"] or search_model).strip() or search_model

if config_path.exists():
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
else:
    config = {}

providers = config.get("providers")
if not isinstance(providers, dict):
    providers = {}

for key in list(providers.keys()):
    if str(key).startswith("ollama-local-"):
        providers.pop(key, None)

models = [model for model in (search_model, reason_model, fallback_model) if model]
for index in range(worker_count):
    slot = index + 1
    key = f"ollama-local-{slot:02d}"
    providers[key] = {
        "name": f"Ollama Local {slot}",
        "api": base_url,
        "base_url": base_url,
        "api_key": "ollama",
        "default_model": default_model,
        "model": default_model,
        "models": models,
        "transport": "chat_completions",
        "provider_key": key,
    }

config["providers"] = providers

# Keep the current default model pointed at the local search model if unset.
model_cfg = config.get("model")
if not isinstance(model_cfg, dict):
    model_cfg = {"default": default_model}
config["model"] = model_cfg
if not model_cfg.get("provider"):
    model_cfg["provider"] = f"custom:ollama-local-01"
if not model_cfg.get("default"):
    model_cfg["default"] = default_model
if not model_cfg.get("base_url"):
    model_cfg["base_url"] = base_url

custom_providers = config.get("custom_providers")
if isinstance(custom_providers, list):
    config["custom_providers"] = [
        entry for entry in custom_providers
        if not (
            isinstance(entry, dict)
            and (
                str(entry.get("provider_key", "")).strip().startswith("ollama-local-")
                or str(entry.get("name", "")).strip().lower().startswith("ollama local")
            )
        )
    ]

config_path.write_text(
    yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
)
PY

if command -v ollama >/dev/null 2>&1 && [[ "${PULL_OLLAMA_MODELS:-1}" != "0" ]]; then
  for model in "$OLLAMA_SEARCH_MODEL" "$OLLAMA_REASON_MODEL" "$OLLAMA_FALLBACK_MODEL"; do
    if [[ -n "$model" ]]; then
      echo "[ollama-stack] pulling model: $model"
      ollama pull "$model" || true
    fi
  done
else
  cat <<EOF
[ollama-stack] Ollama binary not found or model pulls skipped.
[ollama-stack] Start a local Ollama server (e.g. '$HOME/.local/bin/ollama serve') and then pull:
  $OLLAMA_SEARCH_MODEL
  $OLLAMA_REASON_MODEL
  $OLLAMA_FALLBACK_MODEL
EOF
fi

cat <<EOF

Hermes Ollama routing installed.

Config:
  $CONFIG_PATH

Env:
  $ENV_PATH

Recommended local split:
  finder / reader / summarizer -> Ollama
  implementer / verifier       -> Cerebras

Run:
  cd "$REPO_ROOT"
  source "$REPO_ROOT/venv/bin/activate"
  export PATH="$HOME/.local/bin:$PATH"
  hermes --tui

EOF
