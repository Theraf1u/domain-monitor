#!/usr/bin/env bash
# Repoints this agent at a different server without touching its node
# token - used when the control center (bot + API) has been migrated to
# a new host. The token stays valid; only where events/heartbeats are
# sent changes.
set -uo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ ! -f "$ENV_FILE" ]; then
    echo "✗ .env не найден по пути $ENV_FILE" >&2
    echo "  -> Агент ещё не настроен, запусти install-agent.sh." >&2
    exit 1
fi

NEW_URL="${1:-}"
if [[ ! "$NEW_URL" =~ ^https?:// ]]; then
    echo "Использование: domain-monitor-agent set-server <новый Server URL>" >&2
    echo "Пример: domain-monitor-agent set-server http://45.137.202.118" >&2
    exit 1
fi

OLD_URL="$(grep -oP '^SERVER_URL=\K.*' "$ENV_FILE" 2>/dev/null || echo "?")"
sed -i "s|^SERVER_URL=.*|SERVER_URL=${NEW_URL}|" "$ENV_FILE"

# A prior automated migration (spec 3.3) may have persisted a runtime
# override in data/runtime.json that would otherwise take precedence over
# the .env value just written above (see app/runtime_config.py -
# effective_server_url() prefers the override). A manual set-server here
# is an explicit admin decision and must always win, so clear it.
RUNTIME_OVERRIDE="$PROJECT_DIR/data/runtime.json"
if [ -f "$RUNTIME_OVERRIDE" ]; then
    rm -f "$RUNTIME_OVERRIDE"
    echo "[*] Снят предыдущий override миграции (data/runtime.json)."
fi

echo "SERVER_URL: ${OLD_URL} -> ${NEW_URL}"
echo "[*] Перезапускаю агент ..."
# --force-recreate: a real bug caught during live Migration 2.0 testing
# (2026-09-19) - `docker compose up -d` alone does not reliably detect
# that .env's CONTENT changed (only that the file reference is
# unchanged), so a plain `compose up -d` after this script's own sed
# above could leave the agent running against the OLD SERVER_URL
# indefinitely, invisibly - .env says the new URL, but the actual
# running process never picked it up. See server/scripts/migrate_finish.sh
# for the full story (same bug, caught there first).
(cd "$PROJECT_DIR" && compose up -d --force-recreate)
echo "[OK] Агент теперь отправляет данные на ${NEW_URL}. Токен ноды не менялся."
