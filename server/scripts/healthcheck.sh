#!/usr/bin/env bash
set -uo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if ! docker inspect domain-monitor-server >/dev/null 2>&1; then
    echo "NOT_INSTALLED: container 'domain-monitor-server' does not exist"
    exit 2
fi

running="$(docker inspect --format '{{.State.Running}}' domain-monitor-server)"
if [ "$running" != "true" ]; then
    echo "DOWN: container exists but is not running"
    exit 1
fi

health="$(docker inspect --format '{{.State.Health.Status}}' domain-monitor-server 2>/dev/null || echo "none")"
case "$health" in
    healthy|starting) echo "$health" | tr '[:lower:]' '[:upper:]'; exit 0 ;;
    *)
        echo "UNHEALTHY: $health"
        echo "--- last 20 log lines ---"
        (cd "$PROJECT_DIR" && compose logs --tail 20)
        exit 1
        ;;
esac
