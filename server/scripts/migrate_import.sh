#!/usr/bin/env bash
# Imports a migration package made by migrate_export.sh on another
# server. Unlike a plain restore, this does NOT blindly overwrite this
# server's own .env - PORT and PUBLIC_URL are host-specific (this box's
# free port, this box's address) and must stay as this install's wizard
# set them, while BOT_TOKEN/ADMIN_ID/ADMIN_API_KEY need to come from the
# old server so the same bot and the same admins keep working. Ends by
# printing one ready-to-run command per existing node, to repoint each
# one at this server without touching its token.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$PROJECT_DIR/backups"
ENV_FILE="$PROJECT_DIR/.env"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ "$(id -u)" -ne 0 ]; then
    echo "Этот скрипт нужно запускать от root (используй sudo)." >&2
    exit 1
fi

ARCHIVE="${1:-}"
if [ -z "$ARCHIVE" ]; then
    echo "Использование: domain-monitor-server migrate-import <файл-пакета-миграции.tar.gz>"
    exit 1
fi
if [ ! -f "$ARCHIVE" ] && [ -f "$BACKUP_DIR/$ARCHIVE" ]; then
    ARCHIVE="$BACKUP_DIR/$ARCHIVE"
fi
if [ ! -f "$ARCHIVE" ]; then
    echo "✗ Файл не найден: $1" >&2
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
    echo "✗ На этом сервере ещё нет своего .env - сначала установи Server (install.sh)." >&2
    exit 1
fi

if ! tar -tzf "$ARCHIVE" 2>/dev/null | grep -q '^data/domain_monitor\.db$'; then
    echo "✗ Это не похоже на пакет миграции (нет data/domain_monitor.db внутри): $ARCHIVE" >&2
    exit 1
fi

echo "Это заменит текущую базу данных этого сервера содержимым пакета:"
echo "  $ARCHIVE"
echo "Токен бота, ID админов и admin-ключ API будут взяты из пакета."
echo "Порт и адрес сервера (PORT/PUBLIC_URL) этого сервера не изменятся."
read -r -p "Продолжить? (y/n): " confirm </dev/tty
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Отменено."
    exit 0
fi

cd "$PROJECT_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"

echo "[*] Останавливаю сервер ..."
compose stop 2>/dev/null || true

echo "[*] Сохраняю текущие данные на случай отката ..."
[ -d data ] && mv -f data "data.pre-migration.$STAMP"
cp "$ENV_FILE" "$ENV_FILE.pre-migration.$STAMP"

echo "[*] Разворачиваю пакет ..."
TMP_DIR="$(mktemp -d)"
tar -xzf "$ARCHIVE" -C "$TMP_DIR"
mv -f "$TMP_DIR/data" "$PROJECT_DIR/data"

if [ -f "$TMP_DIR/.env" ]; then
    for key in BOT_TOKEN ADMIN_ID ADMIN_API_KEY TELEGRAM_PROXY; do
        value="$(grep -oP "^${key}=\K.*" "$TMP_DIR/.env" 2>/dev/null || true)"
        if [ -n "$value" ]; then
            if grep -q "^${key}=" "$ENV_FILE"; then
                sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
            else
                echo "${key}=${value}" >>"$ENV_FILE"
            fi
        fi
    done
fi
rm -rf "$TMP_DIR"

PORT="$(grep -oP '^PORT=\K.*' "$ENV_FILE" 2>/dev/null || echo 8280)"
PUBLIC_URL="$(grep -oP '^PUBLIC_URL=\K.*' "$ENV_FILE" 2>/dev/null || echo "http://localhost:${PORT}")"

echo "[*] Запускаю сервер с перенесёнными данными ..."
compose up -d

echo "[*] Жду готовности ..."
ready=0
for _ in $(seq 1 60); do
    if curl -fsS -m 2 "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
if [ "$ready" -ne 1 ]; then
    echo "[ОШИБКА] Сервер не отвечает после переноса. Старые данные сохранены как:" >&2
    echo "         data.pre-migration.$STAMP и .env.pre-migration.$STAMP" >&2
    echo "         Смотри: domain-monitor-server logs" >&2
    exit 1
fi

echo
echo "[OK] Перенос завершён, сервер работает на новом адресе: $PUBLIC_URL"
echo

NODES="$(docker exec -w /app domain-monitor-server python3 -c "
import sys
sys.path.insert(0, '/app')
from app.database import Database
db = Database('/data/domain_monitor.db')
for n in db.list_nodes():
    print(f'{n.id}\t{n.name}')
" 2>/dev/null || true)"

if [ -n "$NODES" ]; then
    echo "На каждой существующей ноде нужно перенаправить агента на новый сервер"
    echo "(токен ноды не меняется, команда безопасна):"
    echo
    while IFS=$'\t' read -r node_id node_name; do
        [ -z "$node_id" ] && continue
        echo "  # нода: $node_name"
        echo "  domain-monitor-agent set-server \"$PUBLIC_URL\""
        echo
    done <<<"$NODES"
else
    echo "Нод в перенесённой базе не найдено - ничего перенаправлять не нужно."
fi

echo "После того как убедишься, что всё работает на новом сервере,"
echo "останови старый: domain-monitor-server stop"
