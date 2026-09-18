#!/usr/bin/env bash
# Fully removes the Domain Monitor Server: container, image, CLI wrapper,
# and (on confirmation) the database and the project directory itself.
set -uo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI_TARGET="/usr/local/bin/domain-monitor-server"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Read before anything below can remove .env, so the port to close is
# still known even if the admin chooses to wipe the whole project dir.
PORT="$(grep -oP '^PORT=\K.*' "$PROJECT_DIR/.env" 2>/dev/null || echo '')"

if [ "$(id -u)" -ne 0 ]; then
    echo "Этот скрипт нужно запускать от root (используй sudo)." >&2
    exit 1
fi

echo "Это остановит и удалит контейнер и Docker-образ Domain Monitor Server."
echo "Все подключённые агенты не смогут отправлять события, пока сервер не переустановлен."
read -r -p "Продолжить? (y/n): " confirm </dev/tty
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Отменено."
    exit 0
fi

(cd "$PROJECT_DIR" && compose down --rmi local 2>/dev/null) || true
close_firewall_port "$PORT"

if [ -f "$CLI_TARGET" ]; then
    rm -f "$CLI_TARGET"
    echo "Удалено: $CLI_TARGET"
fi

read -r -p "Удалить также базу данных (все ноды/домены/события/пользователи) в ./data? (y/n): " wipe_data </dev/tty
if [[ "$wipe_data" =~ ^[Yy]$ ]]; then
    rm -rf "${PROJECT_DIR:?}/data"
    echo "Данные удалены."
fi

if [ -d "$PROJECT_DIR/backups" ]; then
    read -r -p "Удалить также бэкапы в ./backups? (y/n): " wipe_backups </dev/tty
    if [[ "$wipe_backups" =~ ^[Yy]$ ]]; then
        rm -rf "${PROJECT_DIR:?}/backups"
        echo "Бэкапы удалены."
    fi
fi

read -r -p "Удалить также всю папку проекта ($PROJECT_DIR)? (y/n): " wipe_all </dev/tty
if [[ "$wipe_all" =~ ^[Yy]$ ]]; then
    cd /
    rm -rf "${PROJECT_DIR:?}"
    echo "Папка проекта удалена. Удаление завершено."
else
    echo "Удаление завершено. Файлы проекта остались в $PROJECT_DIR (удали вручную, если больше не нужны)."
fi
