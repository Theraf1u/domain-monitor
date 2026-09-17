#!/usr/bin/env bash
# Adds a node via the local REST API (no name required - the server
# auto-assigns a placeholder, renamed to the node's real IP on its first
# heartbeat), mirroring the bot's one-tap "Добавить" button. Prints one
# ready-to-paste install command for the new node - server URL and token
# are baked in, nothing to type there.
set -uo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "✗ .env не найден по пути $ENV_FILE" >&2
    echo "  -> Запусти install.sh, чтобы его создать." >&2
    exit 1
fi

ADMIN_API_KEY="$(grep -oP '^ADMIN_API_KEY=\K.*' "$ENV_FILE" 2>/dev/null || true)"
PORT="$(grep -oP '^PORT=\K.*' "$ENV_FILE" 2>/dev/null || echo 8280)"
PUBLIC_URL="$(grep -oP '^PUBLIC_URL=\K.*' "$ENV_FILE" 2>/dev/null || echo "http://localhost:${PORT}")"

if [ -z "$ADMIN_API_KEY" ]; then
    echo "✗ ADMIN_API_KEY пуст/отсутствует в .env" >&2
    exit 1
fi

response="$(curl -fsS -m 10 -X POST "http://localhost:${PORT}/api/v1/nodes" \
    -H "X-Admin-Key: ${ADMIN_API_KEY}" \
    -H "Content-Type: application/json" \
    -d '{}')" || {
    echo "✗ Не удалось обратиться к серверу на порту ${PORT}." >&2
    echo "  -> Проверь, что контейнер запущен: domain-monitor-server status" >&2
    exit 1
}

name="$(echo "$response" | grep -oP '"name":\s*"\K[^"]+')"
token="$(echo "$response" | grep -oP '"token":\s*"\K[^"]+')"

if [ -z "$token" ]; then
    echo "✗ Сервер вернул неожиданный ответ:" >&2
    echo "$response" >&2
    exit 1
fi

cat <<EOF
✅ Нода ${name} создана.

Выполните на новой ноде одну команду (сервер и токен уже внутри):

curl -fsSL https://raw.githubusercontent.com/Theraf1u/domain-monitor/main/install.sh | sudo bash -s -- agent "${PUBLIC_URL}" "${token}"

Токен показывается только сейчас - если команду не скопировать, придётся создать ноду заново.

После установки нода сама переименуется в свой IP.
EOF
