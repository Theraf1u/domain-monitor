#!/usr/bin/env bash
# Backs up ./data (local retry buffer) and .env into a timestamped tarball
# under ./backups/. The buffer is just a short-lived retry queue, not
# historical data (that lives on the Server), so this mainly protects
# .env/config; kept symmetric with the Server's backup command regardless.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$PROJECT_DIR/backups"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/domain-monitor-agent-backup-$STAMP.tar.gz"

mkdir -p "$BACKUP_DIR"
cd "$PROJECT_DIR"

tar_args=(-czf "$OUT")
[ -d data ] && tar_args+=(data)
[ -f .env ] && tar_args+=(.env)

tar "${tar_args[@]}"
echo "[OK] Backup written to $OUT ($(du -h "$OUT" | cut -f1))"
