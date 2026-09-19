#!/usr/bin/env bash
# Packages everything needed to move the control center (bot + API) to a
# different host: the database (nodes, domains, events, filter rules,
# settings), every automatic backup the bot has made (data/backups/*),
# and .env (bot token, admin ids, admin API key). Same tar layout as
# backup.sh on purpose - a migration package IS a backup, just one meant
# to be restored on a different machine via migrate-import instead of
# restore. Doesn't stop the server (same WAL-mode reasoning as backup.sh).
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="$PROJECT_DIR/backups"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/domain-monitor-migration-$STAMP.tar.gz"

mkdir -p "$BACKUP_DIR"
cd "$PROJECT_DIR"

tar_args=(-czf "$OUT")
[ -d data ] && tar_args+=(data)
[ -f .env ] && tar_args+=(.env)
tar "${tar_args[@]}"
# Contains .env (BOT_TOKEN, ADMIN_API_KEY) in plain text - never leave it
# at the process umask's default (spec 2.0 Part 2, section 9).
chmod 600 "$OUT"

size="$(du -h "$OUT" | cut -f1)"

cat <<EOF
✅ Пакет миграции готов: $OUT ($size)

Внутри: база данных (ноды, домены, события, фильтры, настройки), все
автобэкапы бота (data/backups/*) и .env (токен бота, ID админов,
admin-ключ API).

Дальше:
  1) Скопируй файл на новый сервер:
       scp "$OUT" root@NEW_HOST:/tmp/

  2) На новом сервере установи Server, если ещё не установлен:
       curl -fsSL https://raw.githubusercontent.com/Theraf1u/domain-monitor/main/install.sh | sudo bash

  3) На новом сервере выполни:
       domain-monitor-server migrate-import /tmp/$(basename "$OUT")

  4) Убедись, что бот на новом сервере отвечает (/start в Telegram),
     и только после этого останови старый сервер - иначе два процесса
     с одним токеном бота будут конфликтовать в Telegram:
       domain-monitor-server stop
EOF
