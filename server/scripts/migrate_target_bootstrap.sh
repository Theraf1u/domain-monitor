#!/usr/bin/env bash
# Migration 2.0 (spec 2.0 Part 2, section 3) - runs ON the migration
# TARGET, invoked non-interactively by the source server's migrate_to.sh
# over SSH (via `install.sh server-migrate-target <package>`). Brings up
# a full Server install from a transferred migration package, but in
# STANDBY: Telegram polling is off (TELEGRAM_POLLING_ENABLED=false), so
# this new instance can never fight the still-live source server for the
# same bot token (spec 3.8) - only its API/heartbeat are reachable, which
# is exactly what's needed to verify it before cutover.
#
# Unlike migrate_import.sh (which assumes an operator already ran the
# interactive wizard on this box and just wants to overlay data), this
# script writes .env itself, entirely from the package - there is no
# wizard here, by design: nobody is sitting at this terminal.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ARCHIVE="${1:-}"
if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
    echo "ОШИБКА: пакет миграции не найден: ${ARCHIVE:-<пусто>}" >&2
    exit 1
fi

if [ -f "$ENV_FILE" ]; then
    echo "ОШИБКА: на этом сервере уже есть server/.env - migrate-to предназначен только для полностью чистой установки." >&2
    echo "        Если это осознанно (повторная попытка), удали или переименуй $ENV_FILE и запусти заново." >&2
    exit 1
fi

# See migrate_import.sh for why this reads into a variable first rather
# than piping tar straight into `grep -q` (pipefail + grep's early exit
# race SIGPIPEs tar and reports a false failure even on a real match).
archive_listing="$(tar -tzf "$ARCHIVE" 2>/dev/null)"
if ! grep -q '^data/domain_monitor\.db$' <<<"$archive_listing"; then
    echo "ОШИБКА: это не похоже на пакет миграции (нет data/domain_monitor.db внутри): $ARCHIVE" >&2
    exit 1
fi

echo "[*] Разворачиваю пакет миграции ..."
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
tar -xzf "$ARCHIVE" -C "$TMP_DIR"
mv -f "$TMP_DIR/data" "$PROJECT_DIR/data"

if [ ! -f "$TMP_DIR/.env" ]; then
    echo "ОШИБКА: в пакете нет .env - нечем заполнить BOT_TOKEN/ADMIN_ID/ADMIN_API_KEY." >&2
    exit 1
fi

# Same PORT/PUBLIC_URL discovery as server/install.sh's own wizard - this
# host's own free port and own reachable address, never copied from the
# source server (which would be wrong on a different machine). $2, if
# given, is `migrate-to --target-public-url` (spec 3.1/3.7) - an admin
# who already has a stable hostname pointed at this box overrides the
# raw-IP autodetection with it.
PORT="$(find_free_port 8280)"
TARGET_PUBLIC_URL_OVERRIDE="${2:-}"
if [ -n "$TARGET_PUBLIC_URL_OVERRIDE" ]; then
    PUBLIC_URL="$TARGET_PUBLIC_URL_OVERRIDE"
else
    PUBLIC_URL="http://$(curl -s -4 -m 3 ifconfig.me 2>/dev/null || hostname)"
    [ "$PORT" != "80" ] && PUBLIC_URL="${PUBLIC_URL}:${PORT}"
fi

cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
# Locked down BEFORE any secret is written into it, not after - avoids a
# window where BOT_TOKEN/ADMIN_API_KEY briefly sit in a world-readable
# file while the sed/echo calls below fill them in (spec 9).
chmod 600 "$ENV_FILE"
sed -i "s|^PORT=.*|PORT=${PORT}|" "$ENV_FILE"
sed -i "s|^PUBLIC_URL=.*|PUBLIC_URL=${PUBLIC_URL}|" "$ENV_FILE"

for key in BOT_TOKEN ADMIN_ID ADMIN_API_KEY TELEGRAM_PROXY EVENT_RETENTION_DAYS NODE_OFFLINE_AFTER_SECONDS; do
    value="$(grep -oP "^${key}=\K.*" "$TMP_DIR/.env" 2>/dev/null || true)"
    [ -z "$value" ] && continue
    if grep -q "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        echo "${key}=${value}" >>"$ENV_FILE"
    fi
done

# The one line that makes this "standby" rather than a normal install -
# see server/app/main.py / app/config.py. Appended, not sed'd in, since
# .env.example ships it commented out.
echo "TELEGRAM_POLLING_ENABLED=false" >>"$ENV_FILE"

echo "[*] Открываю порт в файрволе, если UFW активен ..."
open_firewall_port "$PORT"

echo
export GIT_REV="$(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
if ! (cd "$PROJECT_DIR" && compose_build_quiet); then
    echo "ОШИБКА: сборка/запуск сервера в standby-режиме не завершились." >&2
    exit 1
fi

echo "[*] Жду готовности (/healthz) ..."
ready=0
for _ in $(seq 1 60); do
    if curl -fsS -m 2 "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
if [ "$ready" -ne 1 ]; then
    echo "ОШИБКА: сервер не ответил на /healthz после установки в standby-режиме." >&2
    echo "        Смотри: docker logs domain-monitor-server" >&2
    exit 1
fi

chmod +x "$PROJECT_DIR/bin/domain-monitor-server"
ln -sf "$PROJECT_DIR/bin/domain-monitor-server" /usr/local/bin/domain-monitor-server

# migrate_to.sh (running on the SOURCE server) parses exactly this last
# line to learn the target's own address - everything else printed above
# is for a human reading the SSH session directly, not for the caller.
echo "MIGRATE_TARGET_URL=${PUBLIC_URL}"
