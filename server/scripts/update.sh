#!/usr/bin/env bash
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

# Baked into the image as app/GIT_REV (see Dockerfile) - read back by
# app/version.py for the "🖥 Сервер"/"ℹ️ О системе"/"🔄 Обновления"
# Settings screens. Without exporting this, the ARG defaults to "unknown"
# and those screens can't tell whether an update is even available.
export GIT_REV="$(git -C "$(git rev-parse --show-toplevel 2>/dev/null || echo .)" rev-parse --short HEAD 2>/dev/null || echo unknown)"

echo "[*] Пересобираю образ (GIT_REV=$GIT_REV)..."
compose build

echo "[*] Перезапускаю..."
compose up -d

echo "[OK] Обновление завершено."
