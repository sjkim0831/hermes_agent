#!/usr/bin/env bash
set -euo pipefail

# Create account-specific launchers for the pinned codex-cerebras runtime.
# Native modern Codex currently requires the Responses API for custom providers,
# while Cerebras exposes chat completions. These launchers use the working
# legacy codex-cerebras bridge and load account keys from ~/.hermes/.env.

HERMES_ENV_PATH="${HERMES_ENV_PATH:-$HOME/.hermes/.env}"
BIN_DIR="${CODEX_CEREBRAS_BIN_DIR:-$HOME/.local/bin}"
CEREBRAS_ACCOUNT_COUNT="${CEREBRAS_ACCOUNT_COUNT:-20}"
CEREBRAS_FIRST_SLOT="${CEREBRAS_FIRST_SLOT:-101}"
CEREBRAS_DEFAULT_MODEL="${CEREBRAS_DEFAULT_MODEL:-qwen-3-235b-a22b-instruct-2507}"
CEREBRAS_FAST_MODEL="${CEREBRAS_FAST_MODEL:-llama3.1-8b}"

mkdir -p "$BIN_DIR"

last_slot=$((CEREBRAS_FIRST_SLOT + CEREBRAS_ACCOUNT_COUNT - 1))
for slot in $(seq "$CEREBRAS_FIRST_SLOT" "$last_slot"); do
  launcher="$BIN_DIR/codex-cerebras-${slot}"
  cat > "$launcher" <<SH
#!/usr/bin/env bash
set -euo pipefail

HERMES_ENV_PATH="\${HERMES_ENV_PATH:-$HERMES_ENV_PATH}"
if [[ -f "\$HERMES_ENV_PATH" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "\$HERMES_ENV_PATH"
  set +a
fi

key_var="CEREBRAS_API_KEY_${slot}"
api_key="\${!key_var:-}"
if [[ -z "\$api_key" ]]; then
  echo "Missing \$key_var in \$HERMES_ENV_PATH" >&2
  exit 1
fi

export CEREBRAS_API_KEY="\$api_key"
export OPENAI_API_KEY="\${OPENAI_API_KEY:-\$api_key}"
exec codex-cerebras "\$@"
SH
  chmod +x "$launcher"

  cat > "$BIN_DIR/codex-cerebras-${slot}-qwen" <<SH
#!/usr/bin/env bash
exec "$launcher" -m "$CEREBRAS_DEFAULT_MODEL" "\$@"
SH
  chmod +x "$BIN_DIR/codex-cerebras-${slot}-qwen"

  cat > "$BIN_DIR/codex-cerebras-${slot}-llama31-8b" <<SH
#!/usr/bin/env bash
exec "$launcher" -m "$CEREBRAS_FAST_MODEL" "\$@"
SH
  chmod +x "$BIN_DIR/codex-cerebras-${slot}-llama31-8b"
done

echo "Cerebras account launchers installed:"
echo "  $BIN_DIR/codex-cerebras-${CEREBRAS_FIRST_SLOT}"
echo "  $BIN_DIR/codex-cerebras-${CEREBRAS_FIRST_SLOT}-qwen"
echo "  $BIN_DIR/codex-cerebras-${CEREBRAS_FIRST_SLOT}-llama31-8b"
echo "  ... through slot $last_slot"
