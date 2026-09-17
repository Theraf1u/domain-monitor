#!/usr/bin/env bash
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
    echo "Использование: domain-monitor-agent restore <файл-бэкапа.tar.gz>"
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

echo "Это ЗАМЕНИТ текущий локальный буфер и .env содержимым файла:"
echo "  $ARCHIVE"
read -r -p "Продолжить? (yes/no): " confirm </dev/tty
if [[ ! "$confirm" =~ ^[Yy] ]]; then
    echo "Отменено."
    exit 0
fi

cd "$PROJECT_DIR"
compose stop 2>/dev/null || true

mv -f data "data.pre-restore.$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
tar -xzf "$ARCHIVE" -C "$PROJECT_DIR"

compose up -d
echo "[OK] Восстановление завершено, контейнер перезапущен."
