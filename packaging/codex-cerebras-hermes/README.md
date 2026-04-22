# Hermes + codex-cerebras portable package

This package keeps Hermes as the conversation TUI and uses `codex-cerebras` as
the execution backend. Secrets stay outside git in `~/.hermes/.env`.

## Install on a new PC

```bash
git clone <your-hermes-repo> /opt/util/hermes
cd /opt/util/hermes
bash scripts/bootstrap-portable-codex-cerebras.sh
nano ~/.hermes/.env
source venv/bin/activate
hermes --tui
```

If the machine already has the Hermes Python/Node dependencies installed, the
smaller installer is also enough:

```bash
bash scripts/install-hermes-codex-cerebras.sh
```

## After updating Codex

Normal Codex can be updated freely. The Cerebras bridge uses a pinned,
separate runtime under `~/.codex/cerebras-legacy-cli`.

If the launcher disappears or a machine is new:

```bash
bash scripts/install-codex-cerebras.sh
```

## After updating Hermes

Keep the integration changes committed in your Hermes fork. After pulling or
rebasing Hermes, run:

```bash
bash scripts/bootstrap-portable-codex-cerebras.sh
```

For a faster refresh when dependencies are already installed:

```bash
bash scripts/install-hermes-codex-cerebras.sh
bash scripts/doctor-codex-cerebras-hermes.sh
```

The installer refreshes only machine-local config:

- `~/.local/bin/codex-cerebras`
- `~/.codex/cerebras-legacy-cli`
- `~/.codex/cerebras-legacy-home`
- `~/.hermes/config.yaml`
- `~/.hermes/.env` placeholders

## Account selection

Hermes model/provider selection shows `Cerebras API 101`, `Cerebras API 102`,
and so on. Each entry reads a different environment variable from
`~/.hermes/.env`, for example `CEREBRAS_API_KEY_101`.

Do not commit real API keys.
