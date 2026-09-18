#!/usr/bin/env bash
# Migration 2.0 (spec 3) - `domain-monitor-server migrate-cutover <job_id>`.
# The one command that actually starts moving agents: flips the job to
# 'cutover', which makes THIS (still fully running) server start
# returning migration_target_url in every heartbeat response (see
# app/api/nodes.py). Each agent verifies the target itself before
# switching (app/uplink.py _try_migrate) - nothing here touches any
# agent directly, and nothing here stops this server or its Telegram bot
# yet, so a still-not-switched agent keeps working exactly as before
# until it's ready.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

JOB_ID="${1:-}"
if [ -z "$JOB_ID" ]; then
    echo "Использование: domain-monitor-server migrate-cutover <job_id>" >&2
    echo "  (id задачи печатает migrate-to, или смотри: migrate-status)" >&2
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

if [ "$job_status" != "standby" ]; then
    echo "✗ Задача #${JOB_ID} в статусе '${job_status}', а не 'standby' - переключать нечего." >&2
    exit 1
fi

echo "[*] Проверяю, что цель миграции ($target_url) всё ещё жива ..."
if ! curl -fsS -m 10 "${target_url}/healthz" >/dev/null 2>&1; then
    echo "✗ $target_url/healthz недоступен прямо сейчас - переключать агентов на неотвечающий сервер опасно." >&2
    echo "  -> Проверь целевой сервер (docker logs domain-monitor-server там) и повтори." >&2
    exit 1
fi

api -X PATCH "http://127.0.0.1:${PORT}/api/v1/migration/jobs/${JOB_ID}" \
    -H "Content-Type: application/json" -d '{"status":"cutover"}' >/dev/null

cat <<EOF
✅ Задача #${JOB_ID} переведена в статус 'cutover'.

Каждый агент при своём следующем heartbeat (обычно в течение минуты)
сам увидит адрес нового сервера, проверит его и переключится - никаких
команд на нодах вручную. Этот сервер продолжает отвечать как обычно.

Смотри прогресс:  domain-monitor-server migrate-status ${JOB_ID}
Когда все переключились:  domain-monitor-server migrate-finish ${JOB_ID}
EOF
