#!/usr/bin/env bash
# Restores ./data and .env from a backup tarball made by backup.sh. Stops
# the container first (a restore into a live database is asking for
# trouble), replaces the current data, then starts it back up.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$PROJECT_DIR/backups"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root (use sudo)." >&2
    exit 1
fi

ARCHIVE="${1:-}"
if [ -z "$ARCHIVE" ]; then
    echo "Usage: domain-monitor-server restore <backup-file.tar.gz>"
    echo
    echo "Available backups in $BACKUP_DIR:"
    ls -1t "$BACKUP_DIR" 2>/dev/null || echo "  (none found)"
    exit 1
fi
if [ ! -f "$ARCHIVE" ]; then
    ARCHIVE="$BACKUP_DIR/$ARCHIVE"
fi
if [ ! -f "$ARCHIVE" ]; then
    echo "Backup file not found: $1" >&2
    exit 1
fi

echo "This will REPLACE the current database and .env with the contents of:"
echo "  $ARCHIVE"
read -r -p "Continue? (yes/no): " confirm </dev/tty
if [[ ! "$confirm" =~ ^[Yy] ]]; then
    echo "Cancelled."
    exit 0
fi

cd "$PROJECT_DIR"
compose stop 2>/dev/null || true

mv -f data "data.pre-restore.$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
tar -xzf "$ARCHIVE" -C "$PROJECT_DIR"

compose up -d
echo "[OK] Restore complete, container restarted."
