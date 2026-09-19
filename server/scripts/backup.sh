#!/usr/bin/env bash
# Backs up ./data (database + logs) and .env into a single timestamped
# tarball under ./backups/. Does not stop the running container - sqlite's
# WAL mode makes a plain file copy safe enough for a periodic backup
# (a restore always starts from a stopped container anyway).
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$PROJECT_DIR/backups"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/domain-monitor-server-backup-$STAMP.tar.gz"

mkdir -p "$BACKUP_DIR"
cd "$PROJECT_DIR"

tar_args=(-czf "$OUT")
[ -d data ] && tar_args+=(data)
[ -f .env ] && tar_args+=(.env)

tar "${tar_args[@]}"
# Contains .env (BOT_TOKEN, ADMIN_API_KEY) - never leave it at the
# process umask's default (often world-readable) on a multi-user box
# (spec 2.0 Part 2, section 9).
chmod 600 "$OUT"
echo "[OK] Бэкап записан в $OUT ($(du -h "$OUT" | cut -f1))"
