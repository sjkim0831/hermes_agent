#!/usr/bin/env bash
set -euo pipefail

# Package /opt into a zstd-compressed tar archive.
# The CUBRID physical backup directory is excluded by default.
#
# Usage:
#   bash scripts/package-wsl-opt.sh
#
# Optional env:
#   OUTPUT_DIR=/var/tmp
#   OPT_SOURCE=/opt
#   EXCLUDE_CUBRID_BACKUP=1
#   SUDO_PASSWORD='<sudo-password>'

OPT_SOURCE="${OPT_SOURCE:-/opt}"
OUTPUT_DIR="${OUTPUT_DIR:-/var/tmp}"
EXCLUDE_CUBRID_BACKUP="${EXCLUDE_CUBRID_BACKUP:-1}"
SUDO_PASSWORD="${SUDO_PASSWORD:-}"

if ! command -v tar >/dev/null 2>&1; then
  echo "tar is required." >&2
  exit 1
fi
if ! command -v zstd >/dev/null 2>&1; then
  echo "zstd is required." >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
archive="$OUTPUT_DIR/wsl-opt-$(date +%Y%m%d-%H%M%S)-no-cubrid-backup.tar.zst"
log="$archive.log"

tar_args=(
  --warning=no-file-changed
  --checkpoint=20000
  "--checkpoint-action=echo=checkpoint: %u files archived"
)

if [[ "$EXCLUDE_CUBRID_BACKUP" == "1" ]]; then
  tar_args+=(--exclude=opt/util/cubrid/11.2/backup --exclude=/opt/util/cubrid/11.2/backup)
fi

echo "$archive" > /tmp/wsl_opt_archive_path
echo "archive=$archive"
echo "log=$log"

set -o pipefail
if [[ -n "$SUDO_PASSWORD" ]]; then
  printf '%s\n' "$SUDO_PASSWORD" \
    | sudo -S -p '' tar "${tar_args[@]}" -C / -cf - "${OPT_SOURCE#/}" 2> >(tee "$log" >&2) \
    | zstd -T0 -10 -f -o "$archive" 2> >(tee -a "$log" >&2)
else
  tar "${tar_args[@]}" -C / -cf - "${OPT_SOURCE#/}" 2> >(tee "$log" >&2) \
    | zstd -T0 -10 -f -o "$archive" 2> >(tee -a "$log" >&2)
fi

zstd -t "$archive"
ls -lh "$archive" "$log"
echo "done: $archive"
