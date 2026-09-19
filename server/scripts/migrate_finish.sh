#!/usr/bin/env bash
# Migration 2.0 (spec 3.8) - `domain-monitor-server migrate-finish
# <job_id> [-i /path/to/ssh/key]`. Run once migrate-status shows every
# node has switched (or the admin has otherwise decided to proceed).
# Resolves the single-Telegram-poller conflict the safe way round: turns
# OFF polling on THIS (old) server first, THEN turns it on on the target
# - there is a brief window with neither server polling (a few seconds,
# same as any other server restart this project already does), never a
# window with both polling at once, which is the case Telegram itself
# would reject with a 409 Conflict and can knock either one offline.
#
# Never touches this server's own data or uninstalls anything - spec 3:
# "никогда не удалять старый сервер/данные автоматически". The admin
# decides when (and whether) to tear the old one down, by hand.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

JOB_ID="${1:-}"
SSH_KEY=""
SSH_PORT="22"
shift || true
while [ $# -gt 0 ]; do
    case "$1" in
        -i) SSH_KEY="${2:-}"; shift 2 ;;
        --ssh-port) SSH_PORT="${2:-22}"; shift 2 ;;
        *) echo "Неизвестный параметр: $1" >&2; exit 1 ;;
    esac
done
if [ -z "$JOB_ID" ]; then
    echo "Использование: domain-monitor-server migrate-finish <job_id> [-i /path/to/ssh/key] [--ssh-port N]" >&2
    exit 1
fi

ADMIN_API_KEY="$(grep -oP '^ADMIN_API_KEY=\K.*' "$ENV_FILE")"
PORT="$(grep -oP '^PORT=\K.*' "$ENV_FILE" 2>/dev/null || echo 8280)"
api() { curl -fsS -m 15 -H "X-Admin-Key: ${ADMIN_API_KEY}" "$@"; }

job_json="$(api "http://127.0.0.1:${PORT}/api/v1/migration/jobs/${JOB_ID}")" || {
    echo "✗ Задача миграции #${JOB_ID} не найдена." >&2
    exit 1
}
job_status="$(echo "$job_json" | grep -oP '"status"\s*:\s*"\K[^"]+')"
target_url="$(echo "$job_json" | grep -oP '"target_url"\s*:\s*"\K[^"]+')"

if [ "$job_status" != "cutover" ]; then
    echo "✗ Задача #${JOB_ID} в статусе '${job_status}', а не 'cutover' - сначала выполни migrate-cutover." >&2
    exit 1
fi

echo "Финальный статус перед завершением:"
bash "$(dirname "${BASH_SOURCE[0]}")/migrate_status.sh" "$JOB_ID" || true
echo
read -r -p "Продолжить и переключить Telegram-бота на новый сервер? (y/n): " confirm </dev/tty
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Отменено - задача остаётся в статусе 'cutover', можно вернуться к ней позже."
    exit 0
fi

target_host="$(echo "$target_url" | grep -oP '(?<=://)[^:/]+')"
SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -p "$SSH_PORT")
[ -n "$SSH_KEY" ] && SSH_OPTS+=(-i "$SSH_KEY")

echo "[*] Выключаю Telegram-опрос на ЭТОМ (старом) сервере ..."
if grep -q '^TELEGRAM_POLLING_ENABLED=' "$ENV_FILE"; then
    sed -i 's|^TELEGRAM_POLLING_ENABLED=.*|TELEGRAM_POLLING_ENABLED=false|' "$ENV_FILE"
else
    echo "TELEGRAM_POLLING_ENABLED=false" >>"$ENV_FILE"
fi
(cd "$PROJECT_DIR" && compose up -d)

echo "[*] Жду, пока старый сервер снова ответит (уже без опроса Telegram) ..."
for _ in $(seq 1 30); do
    curl -fsS -m 2 "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1 && break
    sleep 1
done

echo "[*] Включаю Telegram-опрос на НОВОМ сервере ($target_host) ..."
ssh "${SSH_OPTS[@]}" "root@${target_host}" \
    "sed -i 's|^TELEGRAM_POLLING_ENABLED=.*|TELEGRAM_POLLING_ENABLED=true|' /opt/domain-monitor/server/.env && cd /opt/domain-monitor/server && (docker compose up -d || docker-compose up -d)"

api -X PATCH "http://127.0.0.1:${PORT}/api/v1/migration/jobs/${JOB_ID}" \
    -H "Content-Type: application/json" -d '{"status":"completed"}' >/dev/null

cat <<EOF

✅ Миграция #${JOB_ID} завершена. Telegram-бот теперь отвечает с нового
   сервера: $target_url

Этот (старый) сервер НЕ тронут и НЕ удалён - его данные, бэкапы и .env
остались на месте. Когда убедишься, что всё работает на новом сервере,
можешь остановить или удалить старый вручную:
  domain-monitor-server stop
  domain-monitor-server uninstall
EOF
