#!/usr/bin/env bash
# Pulls the latest code and rebuilds/restarts the container. Never touches
# .env or ./data, so config and the local event buffer survive an update.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ "$(id -u)" -ne 0 ]; then
    echo "Этот скрипт нужно запускать от root (используй sudo)." >&2
    exit 1
fi

cd "$PROJECT_DIR"

if [ -d .git ] || [ -d ../.git ]; then
    echo "[*] Забираю последние изменения..."
    git -C "$(git rev-parse --show-toplevel 2>/dev/null || echo .)" fetch --quiet origin
    git -C "$(git rev-parse --show-toplevel 2>/dev/null || echo .)" reset --quiet --hard origin/HEAD
else
    echo "[!] Это не git-репозиторий - пересобираю локальные файлы как есть."
fi

echo "[*] Пересобираю образ..."
compose build

echo "[*] Перезапускаю..."
compose up -d

echo "[OK] Обновление завершено."
