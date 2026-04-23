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
- multi-account Cerebras/Gemini provider config
- TUI build with `/orchestrate`, `/quota`, `/routing`
- doctor verification

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
