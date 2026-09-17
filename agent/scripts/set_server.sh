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

echo "SERVER_URL: ${OLD_URL} -> ${NEW_URL}"
echo "[*] Перезапускаю агент ..."
(cd "$PROJECT_DIR" && compose up -d)
echo "[OK] Агент теперь отправляет данные на ${NEW_URL}. Токен ноды не менялся."
