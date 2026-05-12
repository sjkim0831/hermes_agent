# Hermes Codex Orchestrator Stack Scripts

## Install on a new PC

```bash
cd /opt/util/hermes
bash scripts/install-hermes-orchestrator-stack.sh
```

This installs:

- editable `hermes-agent[orchestration]`
- `codex-cerebras`
- `codex-gemini`
- local Ollama sidecar routing for finder/reader/summarizer
- account-specific Cerebras launchers for slots 101–120
- multi-account Cerebras/Gemini provider config
- TUI build with `/orchestrate`, `/quota`, `/routing`
- doctor verification

To install only the Ollama portion later:

```bash
bash scripts/install-hermes-ollama-stack.sh
```

Recommended local defaults:

- `qwen2.5-coder:14b-instruct` for search-heavy stages
- `qwen2.5-coder:32b-instruct` for strong local reasoning
- `llama3.1:8b-instruct` as a lightweight fallback

Ollama bootstrap:

- The installer now prefers a user-local install under `~/.local` when `sudo` is unavailable.
- Start the local server with:
  - `export PATH="$HOME/.local/bin:$PATH"`
  - `ollama serve`
- Pull the recommended models with:
  - `ollama pull qwen2.5-coder:14b-instruct`
  - `ollama pull qwen2.5-coder:32b-instruct`
  - `ollama pull llama3.1:8b-instruct`

For local-model work, Hermes can also be paired with an Ollama sidecar. See:

- [Local Model Routing & Ollama](../docs/local-model-routing-and-ollama.md)

Recommended defaults on this machine:

- single model: `qwen2.5-coder:32b-instruct`
- split helper/implementer: `qwen2.5-coder:14b-instruct` + `qwen2.5-coder:32b-instruct`

Secrets are not stored in git. Fill keys in:

```bash
~/.hermes/.env
```

Run Hermes:

```bash
cd /opt/util/hermes
source venv/bin/activate
hermes --tui
```

## Verify

```bash
bash scripts/doctor-codex-cerebras-hermes.sh
```

Cerebras account launchers are installed with:

```bash
bash scripts/install-codex-cerebras-account-launchers.sh
codex-cerebras-101-qwen
codex-cerebras-101-llama31-8b
```

## Package `/opt`

```bash
SUDO_PASSWORD='<sudo-password>' bash scripts/package-wsl-opt.sh
```

Default exclude:

```text
/opt/util/cubrid/11.2/backup
```

The script writes the archive path to:

```bash
/tmp/wsl_opt_archive_path
```

## Restore `/opt`

Local restore:

```bash
ARCHIVE=/var/tmp/wsl-opt-YYYYmmdd-HHMMSS-no-cubrid-backup.tar.zst \
  bash scripts/restore-wsl-opt-archive.sh
```

Remote restore:

```bash
ARCHIVE=/var/tmp/wsl-opt-YYYYmmdd-HHMMSS-no-cubrid-backup.tar.zst \
REMOTE=root@100.116.50.74 \
SSHPASS='<ssh-password>' \
bash scripts/restore-wsl-opt-archive.sh
```

Warning: restore clears remote `/opt` before extraction.
