#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Checking codex-cerebras launcher..."
if command -v codex-cerebras >/dev/null 2>&1; then
  echo "  ok: $(command -v codex-cerebras)"
else
  echo "  missing: run $ROOT/scripts/install-codex-cerebras.sh"
fi

echo "Checking codex-gemini launcher..."
if command -v codex-gemini >/dev/null 2>&1; then
  echo "  ok: $(command -v codex-gemini)"
else
  echo "  missing: run $ROOT/scripts/install-codex-gemini.sh"
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
gemini_providers = [
    p for p in get_compatible_custom_providers(cfg)
    if isinstance(p, dict) and p.get("provider_key") == "codex-gemini-cli"
]
print(f"  custom Cerebras providers: {len(providers)}")
print(f"  custom Gemini providers: {len(gemini_providers)}")

runtime = resolve_runtime_provider(requested="custom:cerebras-api-101")
print(f"  runtime provider: {runtime.get('provider')}")
print(f"  command: {runtime.get('command')}")
print(f"  model: {runtime.get('model')}")
print(f"  api key loaded: {bool(runtime.get('api_key') and runtime.get('api_key') != 'no-key-required')}")

gemini_runtime = resolve_runtime_provider(requested="custom:gemini-api-101")
print(f"  gemini runtime provider: {gemini_runtime.get('provider')}")
print(f"  gemini command: {gemini_runtime.get('command')}")
print(f"  gemini model: {gemini_runtime.get('model')}")
print(f"  gemini api key loaded: {bool(gemini_runtime.get('api_key') and gemini_runtime.get('api_key') != 'no-key-required')}")

import agent.codex_cerebras_cli_client as bridge
import agent.codex_gemini_cli_client as gemini_bridge
print(f"  bridge marker: {bridge.CODEX_CEREBRAS_MARKER_BASE_URL}")
print(f"  gemini bridge marker: {gemini_bridge.CODEX_GEMINI_MARKER_BASE_URL}")

from langgraph_codex_orchestrator.graph import build_graph
graph = build_graph()
print(f"  orchestrator graph: {type(graph).__name__}")

from langgraph_codex_orchestrator.quota import QuotaStore
quota = QuotaStore().summary()
print(f"  quota families: {', '.join(sorted(quota.keys()))}")
PY

echo "Checking hermes-orchestrator CLI..."
if command -v hermes-orchestrator >/dev/null 2>&1; then
  echo "  ok: $(command -v hermes-orchestrator)"
else
  echo "  missing: hermes-orchestrator is not on PATH"
fi

echo "Checking TUI build..."
if [[ -x "$ROOT/ui-tui/dist/entry.js" ]]; then
  echo "  ok: $ROOT/ui-tui/dist/entry.js"
else
  echo "  missing: run npm --prefix $ROOT/ui-tui run build"
fi
