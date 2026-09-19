#!/usr/bin/env bash
# Migration 2.0 (spec 2.0 Part 2, section 3) - `domain-monitor-server
# migrate-to root@NEW_HOST [-i key] [--ssh-port N] [--target-public-url
# URL] [--dry-run]`. Runs ON the SOURCE server. Builds a migration
# package (same format/code as migrate_export.sh - a migration package
# IS a backup), ships the whole current checkout PLUS that package to
# the target over SSH/rsync (so the target runs the exact same code
# version, no dependency on the target reaching GitHub), then runs it
# there in STANDBY (spec 3.8: Telegram polling off) via
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
SCRIPTS_DIR="$(dirname "${BASH_SOURCE[0]}")"
ENV_FILE="$PROJECT_DIR/.env"
source "$SCRIPTS_DIR/lib.sh"

if [ ! -f "$ENV_FILE" ]; then
    echo "✗ .env не найден по пути $ENV_FILE" >&2
    exit 1
fi

TARGET="${1:-}"
SSH_KEY=""
SSH_PORT="22"
TARGET_PUBLIC_URL=""
DRY_RUN=0
shift || true
while [ $# -gt 0 ]; do
    case "$1" in
        -i) SSH_KEY="${2:-}"; shift 2 ;;
        --ssh-port) SSH_PORT="${2:-22}"; shift 2 ;;
        --target-public-url) TARGET_PUBLIC_URL="${2:-}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        *) echo "Неизвестный параметр: $1" >&2; exit 1 ;;
    esac
done

if [ -z "$TARGET" ]; then
    echo "Использование: domain-monitor-server migrate-to root@NEW_HOST [-i /path/to/ssh/key] [--ssh-port N] [--target-public-url https://monitor.example.com] [--dry-run]" >&2
    echo "Нужен уже настроенный беспарольный SSH-доступ (ключ) к новому серверу." >&2
    exit 1
fi
[[ "$TARGET" == *"@"* ]] || TARGET="root@${TARGET}"

# spec 3.7 "stable hostname": nudge toward --target-public-url if it
# wasn't given - a DNS name on the new server means a LATER migration
# needs only a DNS change, not another migrate-to.
if [ -z "$TARGET_PUBLIC_URL" ]; then
    echo "💡 Без --target-public-url новый сервер получит PUBLIC_URL по своему голому IP."
    echo "   Если заведёшь DNS A-запись на новый сервер заранее, можно передать её:"
    echo "   migrate-to $TARGET --target-public-url http://monitor.example.com:8280"
    echo
fi

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -p "$SSH_PORT")
[ -n "$SSH_KEY" ] && SSH_OPTS+=(-i "$SSH_KEY")
# scp/rsync's ssh transport takes -P (capital) for the port, not -p.
SCP_OPTS=("${SSH_OPTS[@]/-p/-P}")

[ "$DRY_RUN" -eq 1 ] && echo "═══ DRY RUN - ничего не будет реально перенесено/установлено ═══"
echo

# --- Шаг 1: doctor старого (этого) сервера - spec 3.2.1 ------------------
echo "[*] Диагностика этого сервера перед миграцией (doctor) ..."
if ! bash "$SCRIPTS_DIR/doctor.sh"; then
    echo "⚠️ doctor нашёл проблемы на этом сервере (вывод выше) - миграция с них может унаследовать те же проблемы." >&2
    read -r -p "Продолжить всё равно? (y/n): " confirm </dev/tty
    [[ "$confirm" =~ ^[Yy]$ ]] || { echo "Отменено."; exit 1; }
fi
echo

echo "[*] Проверяю SSH-доступ к $TARGET (порт $SSH_PORT) ..."
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

# --- Шаг: check OS - spec 3.2.5 -------------------------------------------
remote_os="$(ssh "${SSH_OPTS[@]}" "$TARGET" "cat /etc/os-release 2>/dev/null | grep -oP '^PRETTY_NAME=\"\K[^\"]+' || uname -s")"
echo "[*] ОС цели: $remote_os"
if ! echo "$remote_os" | grep -qiE "ubuntu|debian"; then
    echo "⚠️ Автоустановка Docker/Compose/UFW рассчитана на Debian/Ubuntu - на другой ОС может не сработать." >&2
fi

# --- Шаг: dependencies (без установки - только read-only проверка) - spec 3.2.6 ---
remote_docker="$(ssh "${SSH_OPTS[@]}" "$TARGET" "command -v docker >/dev/null 2>&1 && echo present || echo absent")"
remote_compose="$(ssh "${SSH_OPTS[@]}" "$TARGET" "(docker compose version >/dev/null 2>&1 && echo plugin) || (command -v docker-compose >/dev/null 2>&1 && echo standalone) || echo absent")"
remote_ufw="$(ssh "${SSH_OPTS[@]}" "$TARGET" "command -v ufw >/dev/null 2>&1 && echo present || echo absent")"
echo "[*] На цели: Docker=$remote_docker, Compose=$remote_compose, UFW=$remote_ufw (будет установлено автоматически при отсутствии)"

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
PACKAGE="$(bash "$SCRIPTS_DIR/migrate_export.sh" | grep -oP '(?<=Пакет миграции готов: )\S+')"
if [ -z "$PACKAGE" ] || [ ! -f "$PACKAGE" ]; then
    echo "✗ Не удалось собрать пакет миграции." >&2
    exit 1
fi
chmod 600 "$PACKAGE"
echo "[OK] Пакет: $PACKAGE"

# --- Шаг: verify snapshot - spec 3.2.3 ------------------------------------
echo "[*] Проверяю целостность пакета (список файлов, БД внутри архива) ..."
if ! tar -tzf "$PACKAGE" >/dev/null 2>&1; then
    echo "✗ Собранный архив повреждён - не архив gzip." >&2
    rm -f "$PACKAGE"
    exit 1
fi
package_listing="$(tar -tzf "$PACKAGE" 2>/dev/null)"
if ! grep -q '^data/domain_monitor\.db$' <<<"$package_listing"; then
    echo "✗ В собранном пакете нет data/domain_monitor.db - экспорт сломан." >&2
    rm -f "$PACKAGE"
    exit 1
fi
echo "[OK] Пакет читается и содержит базу данных."

if [ "$DRY_RUN" -eq 1 ]; then
    cat <<EOF

═══ DRY RUN завершён - ничего не перенесено и не установлено ═══

Что было бы сделано дальше:
  1) Код проекта скопирован на $TARGET (rsync, порт $SSH_PORT)
  2) Пакет миграции ($(du -h "$PACKAGE" | cut -f1)) скопирован на $TARGET, права 0600
  3) На $TARGET выполнен install.sh server-migrate-target - установка
     Docker/Compose/UFW (то, чего не хватает: смотри выше), стандартный
     standby-запуск сервера${TARGET_PUBLIC_URL:+ с PUBLIC_URL=$TARGET_PUBLIC_URL}
  4) Проверка /healthz, Telegram API и целостности БД на цели
  5) Регистрация задачи миграции в статусе standby

Всё выглядит готовым к реальному запуску (без --dry-run).
EOF
    rm -f "$PACKAGE"
    exit 0
fi

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
scp "${SCP_OPTS[@]}" -p "$PACKAGE" "$TARGET:$REMOTE_PACKAGE" >/dev/null
ssh "${SSH_OPTS[@]}" "$TARGET" "chmod 600 $REMOTE_PACKAGE"

echo
echo "[*] Разворачиваю standby-сервер на $TARGET (Docker/Compose/UFW при необходимости) ..."
REMOTE_OUTPUT="$(ssh "${SSH_OPTS[@]}" "$TARGET" "bash $REMOTE_PROJECT_DIR/install.sh server-migrate-target $REMOTE_PACKAGE ${TARGET_PUBLIC_URL:-}")"
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

# --- Шаг: DB check на цели - spec 3.2.17 ----------------------------------
echo "[*] Проверяю целостность БД на цели ..."
db_check="$(ssh "${SSH_OPTS[@]}" "$TARGET" "docker exec domain-monitor-server python3 -c \"
from app.database import Database
db = Database('/data/domain_monitor.db')
print('ok' if db.integrity_check() else 'FAILED')
\"" 2>/dev/null || echo "не удалось проверить")"
if [ "$db_check" = "ok" ]; then
    echo "[OK] Целостность БД на цели подтверждена."
else
    echo "⚠️ Не удалось подтвердить целостность БД на цели ($db_check) - проверь вручную перед cutover." >&2
fi

# --- Шаг: Telegram connectivity check на цели - spec 3.2.18 ---------------
echo "[*] Проверяю доступность Telegram API с целевого сервера ..."
tg_check="$(ssh "${SSH_OPTS[@]}" "$TARGET" "
BOT_TOKEN=\$(grep -oP '^BOT_TOKEN=\K.*' $REMOTE_PROJECT_DIR/server/.env 2>/dev/null)
curl -fsS -m 8 \"https://api.telegram.org/bot\${BOT_TOKEN}/getMe\" 2>/dev/null | grep -q '\"ok\":true' && echo ok || echo failed
" 2>/dev/null || echo "failed")"
if [ "$tg_check" = "ok" ]; then
    echo "[OK] Telegram API доступен с целевого сервера."
else
    echo "⚠️ Telegram API недоступен напрямую с целевого сервера - если он заблокирован там, задай TELEGRAM_PROXY в .env на цели перед cutover." >&2
fi

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
