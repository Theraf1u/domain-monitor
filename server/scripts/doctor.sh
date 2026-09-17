#!/usr/bin/env bash
# Diagnostics: checks the pieces the server actually depends on and prints
# clear pass/fail lines plus a short recommendation on failure. Exits 0 if
# everything checked out, 1 if anything failed.
set -uo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

FAILED=0

ok()   { echo "✓ $1"; }
fail() { echo "✗ $1"; [ -n "${2:-}" ] && echo "  -> $2"; FAILED=1; }

# Docker
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ok "Docker установлен и запущен"
else
    fail "Docker недоступен/не запущен" "Установи Docker или запусти демон Docker."
fi

# Compose
if [ "$(have_compose)" != "none" ]; then
    ok "Docker Compose доступен"
else
    fail "Docker Compose не найден" "Установи плагин 'docker compose' или 'docker-compose'."
fi

# .env
if [ -f "$ENV_FILE" ]; then
    ok ".env существует"
    if grep -q '^ADMIN_API_KEY=.\+' "$ENV_FILE"; then
        ok "ADMIN_API_KEY задан"
    else
        fail "ADMIN_API_KEY пуст/отсутствует в .env" "Запусти установщик заново или задай вручную."
    fi
else
    fail ".env не найден по пути $ENV_FILE" "Запусти install.sh, чтобы его создать."
fi

# Container
if docker inspect domain-monitor-server >/dev/null 2>&1; then
    running="$(docker inspect --format '{{.State.Running}}' domain-monitor-server)"
    if [ "$running" = "true" ]; then
        ok "Контейнер запущен"
        health="$(docker inspect --format '{{.State.Health.Status}}' domain-monitor-server 2>/dev/null || echo none)"
        if [ "$health" = "healthy" ] || [ "$health" = "none" ]; then
            ok "Healthcheck контейнера: $health"
        else
            fail "Healthcheck контейнера: $health" "Смотри логи: domain-monitor-server logs"
        fi
    else
        fail "Контейнер существует, но не запущен" "domain-monitor-server start"
    fi
else
    fail "Контейнер 'domain-monitor-server' не существует" "Запусти install.sh."
fi

# HTTP reachability
PORT="$(grep -oP '^PORT=\K.*' "$ENV_FILE" 2>/dev/null || echo 8280)"
if curl -fsS -m 5 "http://localhost:${PORT}/healthz" >/dev/null 2>&1; then
    ok "HTTP /healthz отвечает на порту $PORT"
else
    fail "HTTP /healthz не отвечает на порту $PORT" "Контейнер может ещё стартовать, либо порт заблокирован."
fi

# Database
DB_FILE="$PROJECT_DIR/data/domain_monitor.db"
if [ -f "$DB_FILE" ]; then
    if command -v sqlite3 >/dev/null 2>&1; then
        if sqlite3 "$DB_FILE" "PRAGMA integrity_check;" 2>/dev/null | grep -q "^ok$"; then
            ok "Проверка целостности БД пройдена"
        else
            fail "Проверка целостности БД провалена" "Рассмотри восстановление из бэкапа: domain-monitor-server restore"
        fi
        applied="$(sqlite3 "$DB_FILE" "SELECT COUNT(*) FROM schema_migrations;" 2>/dev/null || echo 0)"
        ok "Применено миграций: $applied"
    else
        ok "Файл БД существует (sqlite3 CLI не установлен на хосте, проверка целостности пропущена)"
    fi
else
    fail "Файл БД не найден по пути $DB_FILE" "Создаётся при первом запуске; если долго отсутствует - смотри логи контейнера."
fi

# Telegram - the only management interface, so this is a hard requirement.
if grep -q '^BOT_TOKEN=.\+' "$ENV_FILE" 2>/dev/null; then
    BOT_TOKEN="$(grep -oP '^BOT_TOKEN=\K.*' "$ENV_FILE")"
    PROXY="$(grep -oP '^TELEGRAM_PROXY=\K.*' "$ENV_FILE" 2>/dev/null || true)"
    CURL_PROXY_ARG=()
    [ -n "$PROXY" ] && CURL_PROXY_ARG=(-x "$PROXY")
    if curl -fsS -m 8 "${CURL_PROXY_ARG[@]}" "https://api.telegram.org/bot${BOT_TOKEN}/getMe" | grep -q '"ok":true'; then
        ok "Telegram API доступен, токен бота верный"
    else
        fail "Telegram API недоступен или токен неверный" \
            "Проверь BOT_TOKEN, и TELEGRAM_PROXY если Telegram заблокирован на этой сети. Также проверь доступ изнутри контейнера: docker logs domain-monitor-server"
    fi
else
    fail "BOT_TOKEN не задан в .env" "Telegram - единственный способ управления. Запусти install.sh заново или задай BOT_TOKEN/ADMIN_ID вручную."
fi

# Disk space
AVAIL_KB="$(df -Pk "$PROJECT_DIR" | awk 'NR==2 {print $4}')"
AVAIL_MB=$((AVAIL_KB / 1024))
if [ "$AVAIL_MB" -ge 500 ]; then
    ok "Место на диске: ${AVAIL_MB}МБ свободно"
else
    fail "Мало места на диске: ${AVAIL_MB}МБ свободно" "Освободи место или перенеси ./data на диск побольше."
fi

echo
if [ "$FAILED" -eq 0 ]; then
    echo "Все проверки пройдены."
else
    echo "Некоторые проверки провалены - см. рекомендации выше."
fi
exit "$FAILED"
