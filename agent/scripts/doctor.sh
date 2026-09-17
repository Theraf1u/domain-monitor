#!/usr/bin/env bash
# Diagnostics for the agent. Exits 0 if everything checked out, 1 otherwise.
set -uo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

FAILED=0

ok()   { echo "✓ $1"; }
fail() { echo "✗ $1"; [ -n "${2:-}" ] && echo "  -> $2"; FAILED=1; }

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ok "Docker установлен и запущен"
else
    fail "Docker недоступен/не запущен" "Установи Docker или запусти демон Docker."
fi

if [ "$(have_compose)" != "none" ]; then
    ok "Docker Compose доступен"
else
    fail "Docker Compose не найден" "Установи плагин 'docker compose' или 'docker-compose'."
fi

if [ -f "$ENV_FILE" ]; then
    ok ".env существует"
    grep -q '^SERVER_URL=https\?://.\+' "$ENV_FILE" && ok "SERVER_URL задан" || fail "SERVER_URL пуст/отсутствует в .env" "Запусти install-agent.sh заново."
    grep -q '^NODE_TOKEN=nmt_.\+' "$ENV_FILE" && ok "NODE_TOKEN задан" || fail "NODE_TOKEN пуст/отсутствует в .env" "Получи токен на сервере (Ноды -> Добавить) и пропиши в .env."
else
    fail ".env не найден по пути $ENV_FILE" "Запусти install-agent.sh, чтобы его создать."
fi

if docker inspect domain-monitor-agent >/dev/null 2>&1; then
    running="$(docker inspect --format '{{.State.Running}}' domain-monitor-agent)"
    if [ "$running" = "true" ]; then
        ok "Контейнер запущен"
        health="$(docker inspect --format '{{.State.Health.Status}}' domain-monitor-agent 2>/dev/null || echo none)"
        [ "$health" = "healthy" ] || [ "$health" = "none" ] && ok "Healthcheck контейнера: $health" || fail "Healthcheck контейнера: $health" "Смотри логи: domain-monitor-agent logs"

        if docker exec domain-monitor-agent tshark -v >/dev/null 2>&1; then
            ok "tshark доступен внутри контейнера"
        else
            fail "tshark не найден/не работает внутри контейнера" "Образ может быть устаревшим, попробуй: domain-monitor-agent update"
        fi

        caps="$(docker inspect --format '{{.HostConfig.CapAdd}}' domain-monitor-agent 2>/dev/null)"
        if echo "$caps" | grep -q NET_ADMIN && echo "$caps" | grep -q NET_RAW; then
            ok "Права на захват трафика (NET_ADMIN, NET_RAW) заданы"
        else
            fail "Отсутствуют права NET_ADMIN/NET_RAW" "Пересоздай контейнер через docker-compose.yml (не запускай вручную через 'docker run')."
        fi
    else
        fail "Контейнер существует, но не запущен" "domain-monitor-agent start"
    fi
else
    fail "Контейнер 'domain-monitor-agent' не существует" "Запусти install-agent.sh."
fi

if [ -f "$ENV_FILE" ] && grep -q '^SERVER_URL=' "$ENV_FILE"; then
    SERVER_URL="$(grep -oP '^SERVER_URL=\K.*' "$ENV_FILE")"
    if curl -fsS -m 5 "${SERVER_URL}/healthz" >/dev/null 2>&1; then
        ok "Сервер доступен по адресу $SERVER_URL"
    else
        fail "Сервер недоступен по адресу $SERVER_URL" "Проверь сеть/файрвол между этой нодой и сервером."
    fi
fi

DB_FILE="$PROJECT_DIR/data/agent_buffer.db"
if [ -f "$DB_FILE" ]; then
    if command -v sqlite3 >/dev/null 2>&1; then
        if sqlite3 "$DB_FILE" "PRAGMA integrity_check;" 2>/dev/null | grep -q "^ok$"; then
            ok "Проверка целостности локального буфера пройдена"
        else
            fail "Проверка целостности локального буфера провалена" "./data/agent_buffer.db можно удалить - это просто буфер повторной отправки, а не источник истины."
        fi
    else
        ok "Файл локального буфера существует (sqlite3 CLI не установлен на хосте, проверка целостности пропущена)"
    fi
else
    ok "Локальный буфер ещё не создан (агент мог только что запуститься)"
fi

AVAIL_KB="$(df -Pk "$PROJECT_DIR" | awk 'NR==2 {print $4}')"
AVAIL_MB=$((AVAIL_KB / 1024))
if [ "$AVAIL_MB" -ge 200 ]; then
    ok "Место на диске: ${AVAIL_MB}МБ свободно"
else
    fail "Мало места на диске: ${AVAIL_MB}МБ свободно" "Освободи место - переполненный диск может застопорить локальный буфер событий."
fi

echo
if [ "$FAILED" -eq 0 ]; then
    echo "Все проверки пройдены."
else
    echo "Некоторые проверки провалены - см. рекомендации выше."
fi
exit "$FAILED"
