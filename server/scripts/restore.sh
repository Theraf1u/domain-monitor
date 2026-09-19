#!/usr/bin/env bash
# Restores ./data and .env from a backup tarball made by backup.sh. Stops
# the container first (a restore into a live database is asking for
# trouble), replaces the current data, then starts it back up.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$PROJECT_DIR/backups"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ "$(id -u)" -ne 0 ]; then
    echo "Этот скрипт нужно запускать от root (используй sudo)." >&2
    exit 1
fi

ARCHIVE="${1:-}"
if [ -z "$ARCHIVE" ]; then
    echo "Использование: domain-monitor-server restore <файл-бэкапа.tar.gz>"
    echo
    echo "Доступные бэкапы в $BACKUP_DIR:"
    ls -1t "$BACKUP_DIR" 2>/dev/null || echo "  (не найдено)"
    exit 1
fi
if [ ! -f "$ARCHIVE" ]; then
    ARCHIVE="$BACKUP_DIR/$ARCHIVE"
fi
if [ ! -f "$ARCHIVE" ]; then
    echo "Файл бэкапа не найден: $1" >&2
    exit 1
fi

echo "Это ЗАМЕНИТ текущую базу данных и .env содержимым файла:"
echo "  $ARCHIVE"
read -r -p "Продолжить? (y/n): " confirm </dev/tty
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Отменено."
    exit 0
fi

# spec 10: refuse while a migration is in progress - checked while the
# API is still up, before the container gets stopped below.
ENV_FILE="$PROJECT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    ADMIN_API_KEY="$(grep -oP '^ADMIN_API_KEY=\K.*' "$ENV_FILE" 2>/dev/null || true)"
    PORT="$(grep -oP '^PORT=\K.*' "$ENV_FILE" 2>/dev/null || echo 8280)"
    if [ -n "$ADMIN_API_KEY" ]; then
        active_job="$(curl -fsS -m 5 -H "X-Admin-Key: ${ADMIN_API_KEY}" "http://127.0.0.1:${PORT}/api/v1/migration/jobs/active" 2>/dev/null || echo '')"
        if [ -n "$active_job" ] && [ "$active_job" != "null" ]; then
            echo "✗ Сейчас выполняется миграция - восстановление временно недоступно: $active_job" >&2
            exit 1
        fi
    fi
fi

cd "$PROJECT_DIR"
compose stop 2>/dev/null || true

mv -f data "data.pre-restore.$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
tar -xzf "$ARCHIVE" -C "$PROJECT_DIR"

compose up -d --force-recreate  # the archive may include a different .env - see migrate_finish.sh's comment
echo "[OK] Восстановление завершено, контейнер перезапущен."
