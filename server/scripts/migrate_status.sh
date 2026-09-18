#!/usr/bin/env bash
# Migration 2.0 (spec 3.6) - `domain-monitor-server migrate-status
# [job_id]`. Per-node migrated/waiting/offline, worked out by comparing
# THIS server's own node list against the TARGET's (same ADMIN_API_KEY -
# migrate_target_bootstrap.sh copied it verbatim from the package, so it
# authenticates against the target exactly like it does here): a node
# that's now online on the target has switched; one still online here
# hasn't yet; one online on neither is just offline right now, migrated
# or not - this tool can't tell the difference for a node that isn't
# currently reachable, and says so honestly rather than guessing.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

ADMIN_API_KEY="$(grep -oP '^ADMIN_API_KEY=\K.*' "$ENV_FILE")"
PORT="$(grep -oP '^PORT=\K.*' "$ENV_FILE" 2>/dev/null || echo 8280)"
api() { curl -fsS -m 15 -H "X-Admin-Key: ${ADMIN_API_KEY}" "$@"; }

JOB_ID="${1:-}"
if [ -z "$JOB_ID" ]; then
    job_json="$(api "http://127.0.0.1:${PORT}/api/v1/migration/jobs/active")" || {
        echo "✗ Не удалось обратиться к серверу." >&2; exit 1;
    }
    if [ "$job_json" = "null" ] || [ -z "$job_json" ]; then
        echo "Нет активной задачи миграции." >&2
        exit 1
    fi
else
    job_json="$(api "http://127.0.0.1:${PORT}/api/v1/migration/jobs/${JOB_ID}")" || {
        echo "✗ Задача миграции #${JOB_ID} не найдена." >&2; exit 1;
    }
fi

job_id="$(echo "$job_json" | grep -oP '"id"\s*:\s*\K[0-9]+')"
job_status="$(echo "$job_json" | grep -oP '"status"\s*:\s*"\K[^"]+')"
target_url="$(echo "$job_json" | grep -oP '"target_url"\s*:\s*"\K[^"]+')"

echo "Задача миграции #${job_id}: статус '${job_status}', цель: ${target_url}"
echo

if ! command -v python3 >/dev/null 2>&1; then
    echo "(python3 недоступен на хосте - подробный список нод пропущен, только статус задачи выше)"
    exit 0
fi

old_nodes="$(api "http://127.0.0.1:${PORT}/api/v1/nodes" || echo '[]')"
new_nodes="$(curl -fsS -m 10 -H "X-Admin-Key: ${ADMIN_API_KEY}" "${target_url}/api/v1/nodes" 2>/dev/null || echo '[]')"

python3 - "$old_nodes" "$new_nodes" <<'PYEOF'
import json
import sys

old = json.loads(sys.argv[1] or "[]")
new = json.loads(sys.argv[2] or "[]")
new_by_id = {n["id"]: n for n in new}

migrated = waiting = offline = 0
for n in old:
    nid, name = n["id"], n["name"]
    target_copy = new_by_id.get(nid)
    if target_copy and target_copy.get("online"):
        print(f"  ✅ {name} - переключилась на новый сервер")
        migrated += 1
    elif n.get("online"):
        print(f"  🔄 {name} - ещё на старом сервере, ждёт своего heartbeat")
        waiting += 1
    else:
        print(f"  🔴 {name} - офлайн (не видна ни на старом, ни на новом сервере)")
        offline += 1

print()
print(f"Итого: {migrated} переключились, {waiting} ждут, {offline} офлайн (из {len(old)})")
if waiting == 0 and offline == 0 and migrated == len(old) and old:
    print("Все ноды переключились - можно выполнять migrate-finish.")
PYEOF
