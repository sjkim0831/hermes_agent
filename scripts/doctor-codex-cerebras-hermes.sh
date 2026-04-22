#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Checking codex-cerebras launcher..."
if command -v codex-cerebras >/dev/null 2>&1; then
  echo "  ok: $(command -v codex-cerebras)"
else
  echo "  missing: run $ROOT/scripts/install-codex-cerebras.sh"
fi

echo "Checking Hermes Python integration..."
cd "$ROOT"
if [[ -f venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

python3 - <<'PY'
from hermes_cli.env_loader import load_hermes_dotenv
load_hermes_dotenv()

from hermes_cli.config import get_compatible_custom_providers, load_config
from hermes_cli.runtime_provider import resolve_runtime_provider

cfg = load_config()
providers = [
    p for p in get_compatible_custom_providers(cfg)
    if isinstance(p, dict) and p.get("provider_key") == "codex-cerebras-cli"
]
print(f"  custom Cerebras providers: {len(providers)}")
gemini_providers = [
    p for p in get_compatible_custom_providers(cfg)
    if isinstance(p, dict) and p.get("provider_key") == "gemini"
]
print(f"  custom Gemini providers: {len(gemini_providers)}")

runtime = resolve_runtime_provider(requested="custom:cerebras-api-101")
print(f"  runtime provider: {runtime.get('provider')}")
print(f"  command: {runtime.get('command')}")
print(f"  model: {runtime.get('model')}")
print(f"  api key loaded: {bool(runtime.get('api_key') and runtime.get('api_key') != 'no-key-required')}")

if gemini_providers:
    gemini_runtime = resolve_runtime_provider(requested="custom:gemini-api-101")
    print(f"  gemini runtime provider: {gemini_runtime.get('provider')}")
    print(f"  gemini model: {gemini_runtime.get('model')}")
    print(f"  gemini api key loaded: {bool(gemini_runtime.get('api_key') and gemini_runtime.get('api_key') != 'no-key-required')}")

import agent.codex_cerebras_cli_client as bridge
print(f"  bridge marker: {bridge.CODEX_CEREBRAS_MARKER_BASE_URL}")
PY
