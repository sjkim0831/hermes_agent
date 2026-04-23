#!/usr/bin/env bash
set -euo pipefail

# Restore a wsl-opt tar.zst archive into / on the current machine or a remote
# SSH target. The archive contains opt/... paths, so extraction uses -C /.
#
# Local:
#   ARCHIVE=/var/tmp/wsl-opt-....tar.zst bash scripts/restore-wsl-opt-archive.sh
#
# Remote:
#   ARCHIVE=/var/tmp/wsl-opt-....tar.zst REMOTE=root@100.116.50.74 SSHPASS='<ssh-password>' \
#     bash scripts/restore-wsl-opt-archive.sh
#
# WARNING: this clears /opt before extracting.

ARCHIVE="${ARCHIVE:-${1:-}}"
REMOTE="${REMOTE:-}"
SSHPASS_VALUE="${SSHPASS:-}"
REMOTE_ARCHIVE="${REMOTE_ARCHIVE:-}"
KEEP_REMOTE_ARCHIVE="${KEEP_REMOTE_ARCHIVE:-1}"

if [[ -z "$ARCHIVE" ]]; then
  echo "ARCHIVE is required." >&2
  exit 1
fi
if [[ ! -f "$ARCHIVE" ]]; then
  echo "Archive not found: $ARCHIVE" >&2
  exit 1
fi

run_remote() {
  if [[ -n "$SSHPASS_VALUE" ]]; then
    SSHPASS="$SSHPASS_VALUE" sshpass -e ssh -o StrictHostKeyChecking=no "$REMOTE" "$@"
  else
    ssh -o StrictHostKeyChecking=no "$REMOTE" "$@"
  fi
}

copy_remote() {
  local src="$1"
  local dst="$2"
  if [[ -n "$SSHPASS_VALUE" ]]; then
    SSHPASS="$SSHPASS_VALUE" sshpass -e scp -o StrictHostKeyChecking=no "$src" "$REMOTE:$dst"
  else
    scp -o StrictHostKeyChecking=no "$src" "$REMOTE:$dst"
  fi
}

if [[ -z "$REMOTE" ]]; then
  echo "Restoring locally from $ARCHIVE"
  zstd -t "$ARCHIVE"
  find /opt -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  tar --zstd -xpf "$ARCHIVE" -C /
  du -sh /opt
  find /opt -maxdepth 1 -mindepth 1 -printf '%f\n' | sort
  exit 0
fi

if ! command -v ssh >/dev/null 2>&1 || ! command -v scp >/dev/null 2>&1; then
  echo "ssh and scp are required for remote restore." >&2
  exit 1
fi
if [[ -n "$SSHPASS_VALUE" ]] && ! command -v sshpass >/dev/null 2>&1; then
  echo "sshpass is required when SSHPASS is set." >&2
  exit 1
fi

remote_archive="${REMOTE_ARCHIVE:-/tmp/$(basename "$ARCHIVE")}"

echo "Preparing remote: $REMOTE"
run_remote "set -e; if ! command -v zstd >/dev/null 2>&1; then if command -v apt-get >/dev/null 2>&1; then apt-get update -y && DEBIAN_FRONTEND=noninteractive apt-get install -y zstd; elif command -v dnf >/dev/null 2>&1; then dnf install -y zstd; elif command -v yum >/dev/null 2>&1; then yum install -y zstd; else echo 'zstd missing and no supported package manager' >&2; exit 1; fi; fi; mkdir -p /opt /tmp"

echo "Copying archive to $REMOTE:$remote_archive"
copy_remote "$ARCHIVE" "$remote_archive"

echo "Restoring /opt on $REMOTE"
run_remote "set -e; zstd -t '$remote_archive'; find /opt -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; tar --zstd -xpf '$remote_archive' -C /; du -sh /opt; find /opt -maxdepth 1 -mindepth 1 -printf '%f\n' | sort"

if [[ "$KEEP_REMOTE_ARCHIVE" != "1" ]]; then
  run_remote "rm -f '$remote_archive'"
fi

echo "done"
