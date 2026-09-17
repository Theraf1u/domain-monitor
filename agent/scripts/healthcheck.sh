#!/usr/bin/env bash
# Standalone health check. Exits 0 healthy, 1 degraded/unreachable, 2 not installed.
set -uo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if ! docker inspect domain-monitor-agent >/dev/null 2>&1; then
    echo "НЕ УСТАНОВЛЕН: контейнер 'domain-monitor-agent' не существует"
    exit 2
fi

running="$(docker inspect --format '{{.State.Running}}' domain-monitor-agent)"
if [ "$running" != "true" ]; then
    echo "ОСТАНОВЛЕН: контейнер существует, но не запущен"
    exit 1
fi

health="$(docker inspect --format '{{.State.Health.Status}}' domain-monitor-agent 2>/dev/null || echo "none")"
case "$health" in
    healthy|starting) echo "$health" | tr '[:lower:]' '[:upper:]'; exit 0 ;;
    *)
        echo "НЕЗДОРОВ: $health"
        echo "--- последние 20 строк лога ---"
        (cd "$PROJECT_DIR" && compose logs --tail 20)
        exit 1
        ;;
esac
