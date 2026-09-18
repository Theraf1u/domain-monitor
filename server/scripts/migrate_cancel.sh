#!/usr/bin/env bash
# Migration 2.0 - `domain-monitor-server migrate-cancel <job_id>`. Marks
# the job 'cancelled' so the heartbeat endpoint stops offering
# migration_target_url (spec: rollback safety - no agent that hasn't
# already switched will ever be told to). Never touches the standby
# server or this one - if any agents already switched (job was in
# 'cutover'), they stay switched; this only stops NEW ones from starting
# to. Tearing down the standby install itself is left to the admin.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

JOB_ID="${1:-}"
if [ -z "$JOB_ID" ]; then
    echo "Использование: domain-monitor-server migrate-cancel <job_id>" >&2
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
if [[ "$job_status" =~ ^(completed|cancelled|failed)$ ]]; then
    echo "✗ Задача #${JOB_ID} уже в конечном статусе '${job_status}' - отменять нечего." >&2
    exit 1
fi

api -X PATCH "http://127.0.0.1:${PORT}/api/v1/migration/jobs/${JOB_ID}" \
    -H "Content-Type: application/json" -d '{"status":"cancelled"}' >/dev/null

echo "✅ Задача #${JOB_ID} отменена (была в статусе '${job_status}')."
if [ "$job_status" = "cutover" ]; then
    echo "   Ноды, которые УЖЕ успели переключиться на новый сервер, останутся там -"
    echo "   отмена не двигает их обратно. Смотри, кто где: подключись к новому серверу и проверь ноды."
fi
echo "   Standby-сервер (если поднят) не остановлен - удали вручную, если он больше не нужен."
