#!/usr/bin/env bash
# Migration 2.0 (spec 2.0 Part 2, section 3) - `domain-monitor-server
# migrate-to root@NEW_HOST [-i /path/to/key]`. Runs ON the SOURCE server.
# Builds a migration package (same format/code as migrate_export.sh - a
# migration package IS a backup), ships the whole current checkout PLUS
# that package to the target over SSH/rsync (so the target runs the exact
# same code version, no dependency on the target reaching GitHub), then
# runs it there in STANDBY (spec 3.8: Telegram polling off) via
# `install.sh server-migrate-target`.
#
# Deliberately stops BEFORE cutover: this only gets the target verified
# and creates a 'standby' migration_jobs row. Nothing here touches this
# server's own Telegram polling or tells any agent to switch - that's
# `migrate-cutover`, a separate, explicit step (spec 3: "не переключаться
# сразу").
#
# Requires the SOURCE server to already have password-less SSH access to
# the target (its own key, or -i pointing at one) - this script never
# prompts for or accepts a password, the same rule that applies to every
# other credential in this project.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ ! -f "$ENV_FILE" ]; then
    echo "✗ .env не найден по пути $ENV_FILE" >&2
    exit 1
fi

TARGET="${1:-}"
SSH_KEY=""
shift || true
while [ $# -gt 0 ]; do
    case "$1" in
        -i) SSH_KEY="${2:-}"; shift 2 ;;
        *) echo "Неизвестный параметр: $1" >&2; exit 1 ;;
    esac
done

if [ -z "$TARGET" ]; then
    echo "Использование: domain-monitor-server migrate-to root@NEW_HOST [-i /path/to/ssh/key]" >&2
    echo "Нужен уже настроенный беспарольный SSH-доступ (ключ) к новому серверу." >&2
    exit 1
fi
[[ "$TARGET" == *"@"* ]] || TARGET="root@${TARGET}"

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
[ -n "$SSH_KEY" ] && SSH_OPTS+=(-i "$SSH_KEY")

echo "[*] Проверяю SSH-доступ к $TARGET ..."
if ! ssh "${SSH_OPTS[@]}" "$TARGET" "id -u" >/dev/null 2>&1; then
    echo "✗ Не удалось подключиться по SSH к $TARGET (беспарольно, по ключу)." >&2
    echo "  -> Убедись, что публичный ключ этого сервера добавлен в ~/.ssh/authorized_keys на $TARGET," >&2
    echo "     либо укажи ключ явно: migrate-to $TARGET -i /path/to/key" >&2
    exit 1
fi
remote_uid="$(ssh "${SSH_OPTS[@]}" "$TARGET" "id -u")"
if [ "$remote_uid" != "0" ]; then
    echo "✗ $TARGET подключается не как root (uid=$remote_uid) - миграции нужен root на целевом сервере." >&2
    exit 1
fi
echo "[OK] SSH-доступ подтверждён (root)."

if ! command -v rsync >/dev/null 2>&1; then
    echo "[*] rsync не найден на этом (исходном) сервере - устанавливаю ..."
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -qq >/dev/null 2>&1
        apt-get install -y -qq rsync >/dev/null 2>&1
    fi
    command -v rsync >/dev/null 2>&1 || { echo "✗ Не удалось установить rsync - поставь вручную и повтори." >&2; exit 1; }
fi

ADMIN_API_KEY="$(grep -oP '^ADMIN_API_KEY=\K.*' "$ENV_FILE")"
PORT="$(grep -oP '^PORT=\K.*' "$ENV_FILE" 2>/dev/null || echo 8280)"
api() { curl -fsS -m 15 -H "X-Admin-Key: ${ADMIN_API_KEY}" "$@"; }

existing_job="$(api "http://127.0.0.1:${PORT}/api/v1/migration/jobs/active" || true)"
if [ -n "$existing_job" ] && [ "$existing_job" != "null" ]; then
    echo "✗ Уже есть незавершённая миграция: $existing_job" >&2
    echo "  -> Заверши (migrate-finish) или отмени её, прежде чем начинать новую." >&2
    exit 1
fi

echo
echo "[*] Собираю пакет миграции (данные, фильтры, настройки, .env) ..."
PACKAGE="$(bash "$(dirname "${BASH_SOURCE[0]}")/migrate_export.sh" | grep -oP '(?<=Пакет миграции готов: )\S+')"
if [ -z "$PACKAGE" ] || [ ! -f "$PACKAGE" ]; then
    echo "✗ Не удалось собрать пакет миграции." >&2
    exit 1
fi
chmod 600 "$PACKAGE"
echo "[OK] Пакет: $PACKAGE"

REMOTE_PROJECT_DIR="/opt/domain-monitor"
REMOTE_PACKAGE="/root/$(basename "$PACKAGE")"

echo
echo "[*] Копирую код проекта на $TARGET (чтобы там работала та же версия, что и здесь) ..."
ssh "${SSH_OPTS[@]}" "$TARGET" "mkdir -p $REMOTE_PROJECT_DIR"
rsync -az --delete \
    --exclude='server/data' --exclude='server/.env' --exclude='server/backups' \
    --exclude='agent/data' --exclude='agent/.env' \
    --exclude='.git' \
    -e "ssh ${SSH_OPTS[*]}" \
    "$PROJECT_DIR/../" "$TARGET:$REMOTE_PROJECT_DIR/"

echo "[*] Копирую пакет миграции на $TARGET (права 0600) ..."
scp "${SSH_OPTS[@]}" -p "$PACKAGE" "$TARGET:$REMOTE_PACKAGE" >/dev/null
ssh "${SSH_OPTS[@]}" "$TARGET" "chmod 600 $REMOTE_PACKAGE"

echo
echo "[*] Разворачиваю standby-сервер на $TARGET (Docker/Compose/UFW при необходимости) ..."
REMOTE_OUTPUT="$(ssh "${SSH_OPTS[@]}" "$TARGET" "bash $REMOTE_PROJECT_DIR/install.sh server-migrate-target $REMOTE_PACKAGE")"
echo "$REMOTE_OUTPUT" | sed 's/^/    /'

TARGET_URL="$(echo "$REMOTE_OUTPUT" | grep -oP '(?<=MIGRATE_TARGET_URL=)\S+' | tail -1)"
if [ -z "$TARGET_URL" ]; then
    echo "✗ Standby-установка на $TARGET не сообщила свой адрес - что-то пошло не так, смотри вывод выше." >&2
    exit 1
fi

echo
echo "[*] Проверяю /healthz на $TARGET_URL с этой (исходной) стороны ..."
if ! curl -fsS -m 10 "${TARGET_URL}/healthz" >/dev/null 2>&1; then
    echo "✗ $TARGET_URL/healthz недоступен с этого сервера (порт мог не открыться наружу)." >&2
    echo "  -> Проверь UFW/сетевые правила на $TARGET и попробуй снова." >&2
    exit 1
fi
echo "[OK] Standby-сервер отвечает: $TARGET_URL"

echo
echo "[*] Регистрирую задачу миграции ..."
job_json="$(api -X POST "http://127.0.0.1:${PORT}/api/v1/migration/jobs" \
    -H "Content-Type: application/json" -d "{\"target_url\":\"${TARGET_URL}\"}")"
job_id="$(echo "$job_json" | grep -oP '"id"\s*:\s*\K[0-9]+')"
api -X PATCH "http://127.0.0.1:${PORT}/api/v1/migration/jobs/${job_id}" \
    -H "Content-Type: application/json" -d '{"status":"standby"}' >/dev/null

rm -f "$PACKAGE"

cat <<EOF

✅ Standby-сервер готов и проверен: $TARGET_URL
   Задача миграции #${job_id}, статус: standby (агенты ЕЩЁ НЕ переключаются).

Дальше:
  1) Убедись, что на $TARGET_URL всё выглядит правильно (открой бота там
     вручную нельзя - Telegram-опрос выключен - но /healthz и API уже
     живые; данные - точная копия текущей базы на момент экспорта).
  2) Когда готов переключить агентов - запусти:
       domain-monitor-server migrate-cutover ${job_id}
     Это переведёт задачу в статус 'cutover': каждый агент при следующем
     heartbeat сам проверит новый сервер и переключится (без ручных
     команд на нодах). Текущий сервер продолжит отвечать на heartbeat
     до тех пор, пока последний агент не переключится.
  3) Смотри прогресс: domain-monitor-server migrate-status ${job_id}
  4) Когда все ноды переключились - заверши:
       domain-monitor-server migrate-finish ${job_id}
     Это остановит Telegram-опрос на СТАРОМ сервере и включит его на
     новом (без этого шага оба сервера продолжат отвечать на API - это
     безопасно, но бот будет отвечать только на старом сервере).

Откатить: пока задача не 'completed', старый сервер и его данные не
трогаются - просто не выполняй migrate-cutover/migrate-finish, старые
агенты никогда не получат migration_target_url и продолжат работать как
прежде. Отменить явно: domain-monitor-server migrate-cancel ${job_id}
EOF
