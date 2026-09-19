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

# spec 10: refuse while a migration is in progress - same rule the bot's
# own "backup now" button already enforces (see backup_task.py's
# _migration_in_progress_message()), applied here too since this CLI
# command bypasses that Python code entirely and talks straight to the
# filesystem.
if [ -f "$PROJECT_DIR/.env" ]; then
    ADMIN_API_KEY="$(grep -oP '^ADMIN_API_KEY=\K.*' "$PROJECT_DIR/.env" 2>/dev/null || true)"
    PORT="$(grep -oP '^PORT=\K.*' "$PROJECT_DIR/.env" 2>/dev/null || echo 8280)"
    if [ -n "$ADMIN_API_KEY" ]; then
        active_job="$(curl -fsS -m 5 -H "X-Admin-Key: ${ADMIN_API_KEY}" "http://127.0.0.1:${PORT}/api/v1/migration/jobs/active" 2>/dev/null || echo '')"
        if [ -n "$active_job" ] && [ "$active_job" != "null" ]; then
            echo "✗ Сейчас выполняется миграция - бэкап временно недоступен: $active_job" >&2
            exit 1
        fi
    fi
fi

tar_args=(-czf "$OUT")
[ -d data ] && tar_args+=(data)
[ -f .env ] && tar_args+=(.env)

tar "${tar_args[@]}"
# Contains .env (BOT_TOKEN, ADMIN_API_KEY) - never leave it at the
# process umask's default (often world-readable) on a multi-user box
# (spec 2.0 Part 2, section 9).
chmod 600 "$OUT"
echo "[OK] Бэкап записан в $OUT ($(du -h "$OUT" | cut -f1))"
