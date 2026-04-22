#!/usr/bin/env bash
set -euo pipefail

# Configure the current Hermes checkout to use the codex-cerebras/codex-gemini
# backends and register multiple selectable API accounts without storing secrets
# in git.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
CONFIG_PATH="${HERMES_CONFIG_PATH:-$HERMES_HOME/config.yaml}"
ENV_PATH="${HERMES_ENV_PATH:-$HERMES_HOME/.env}"
CEREBRAS_ACCOUNT_COUNT="${CEREBRAS_ACCOUNT_COUNT:-19}"
CEREBRAS_MODEL="${CEREBRAS_MODEL:-qwen-3-235b-a22b-instruct-2507}"
CEREBRAS_ALT_MODEL="${CEREBRAS_ALT_MODEL:-llama3.1-8b}"
GEMINI_ACCOUNT_COUNT="${GEMINI_ACCOUNT_COUNT:-19}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-2.5-flash}"

mkdir -p "$HERMES_HOME"

if [[ "${INSTALL_CODEX_CEREBRAS:-1}" != "0" ]]; then
  "$SCRIPT_DIR/install-codex-cerebras.sh"
fi
if [[ "${INSTALL_CODEX_GEMINI:-1}" != "0" ]]; then
  "$SCRIPT_DIR/install-codex-gemini.sh"
fi

if [[ ! -f "$ENV_PATH" ]]; then
  cp "$REPO_ROOT/packaging/codex-cerebras-hermes/hermes.env.example" "$ENV_PATH"
  echo "Created $ENV_PATH from template. Fill in your CEREBRAS_API_KEY_101... and GEMINI_API_KEY_101... values."
else
  for i in $(seq 101 $((100 + CEREBRAS_ACCOUNT_COUNT))); do
    if ! grep -q "^CEREBRAS_API_KEY_${i}=" "$ENV_PATH"; then
      printf '\nCEREBRAS_API_KEY_%s=\n' "$i" >> "$ENV_PATH"
    fi
  done
  for i in $(seq 101 $((100 + GEMINI_ACCOUNT_COUNT))); do
    if ! grep -q "^GEMINI_API_KEY_${i}=" "$ENV_PATH"; then
      printf '\nGEMINI_API_KEY_%s=\n' "$i" >> "$ENV_PATH"
    fi
  done
fi

export CONFIG_PATH CEREBRAS_ACCOUNT_COUNT CEREBRAS_MODEL CEREBRAS_ALT_MODEL GEMINI_ACCOUNT_COUNT GEMINI_MODEL
python3 - <<'PY'
import os
from pathlib import Path

import yaml

config_path = Path(os.environ["CONFIG_PATH"])
cerebras_account_count = int(os.environ["CEREBRAS_ACCOUNT_COUNT"])
cerebras_model_name = os.environ["CEREBRAS_MODEL"]
cerebras_alt_model_name = os.environ["CEREBRAS_ALT_MODEL"]
gemini_account_count = int(os.environ["GEMINI_ACCOUNT_COUNT"])
gemini_model_name = os.environ["GEMINI_MODEL"]

if config_path.exists():
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
else:
    config = {}

model = config.get("model")
if not isinstance(model, dict):
    model = {"default": str(model or cerebras_model_name)}
config["model"] = model
model["provider"] = "custom:cerebras-api-101"
model["default"] = cerebras_model_name
model.pop("base_url", None)
model.pop("api_key", None)

providers = []
cerebras_models = []
for candidate in (cerebras_model_name, cerebras_alt_model_name):
    candidate = str(candidate or "").strip()
    if candidate and candidate not in cerebras_models:
        cerebras_models.append(candidate)

for i in range(101, 101 + cerebras_account_count):
    providers.append({
        "name": f"Cerebras API {i}",
        "base_url": "https://api.cerebras.ai/v1",
        "key_env": f"CEREBRAS_API_KEY_{i}",
        "model": cerebras_model_name,
        "models": cerebras_models,
        "api_mode": "chat_completions",
        "provider_key": "codex-cerebras-cli",
    })
for i in range(101, 101 + gemini_account_count):
    providers.append({
        "name": f"Gemini API {i}",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_env": f"GEMINI_API_KEY_{i}",
        "model": gemini_model_name,
        "api_mode": "chat_completions",
        "provider_key": "codex-gemini-cli",
    })

existing = config.get("custom_providers")
if not isinstance(existing, list):
    existing = []

kept = [
    entry for entry in existing
    if not (
        isinstance(entry, dict)
        and (
            (
                str(entry.get("provider_key", "")).strip() == "codex-cerebras-cli"
                and str(entry.get("name", "")).strip().startswith("Cerebras API ")
            )
            or (
                str(entry.get("provider_key", "")).strip() == "codex-gemini-cli"
                and str(entry.get("name", "")).strip().startswith("Gemini API ")
            )
        )
    )
]
config["custom_providers"] = providers + kept
config.setdefault("providers", {})

config_path.write_text(
    yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
)
PY

echo "Hermes codex-cerebras/codex-gemini config installed:"
echo "  config: $CONFIG_PATH"
echo "  env:    $ENV_PATH"
echo
echo "Run:"
echo "  source $REPO_ROOT/venv/bin/activate"
echo "  hermes --tui"
