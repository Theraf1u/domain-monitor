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
category() { echo; echo "── $1 ──"; }

# ------------------------------------------------------------------
# DOCKER
# ------------------------------------------------------------------
category "DOCKER"

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    engine_version="$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?')"
    ok "Docker установлен и запущен (Engine $engine_version)"
else
    fail "Docker недоступен/не запущен" "Установи Docker или запусти демон Docker."
fi

# Compose - reports which mode (plugin vs legacy standalone), per spec 4.5,
# since the two have different upgrade paths and it's not obvious from
# the outside which one a given box is actually running.
compose_mode="$(have_compose)"
if [ "$compose_mode" = "plugin" ]; then
    compose_version="$(docker compose version --short 2>/dev/null || echo '?')"
    ok "Docker Compose доступен (plugin, v$compose_version)"
elif [ "$compose_mode" = "standalone" ]; then
    compose_version="$(docker-compose version --short 2>/dev/null || echo '?')"
    ok "Docker Compose доступен (legacy docker-compose, v$compose_version)"
else
    fail "Docker Compose не найден" "Установи плагин 'docker compose' или 'docker-compose'."
fi

# ------------------------------------------------------------------
# SECURITY
# ------------------------------------------------------------------
category "SECURITY"

# .env
if [ -f "$ENV_FILE" ]; then
    ok ".env существует"
    if grep -q '^ADMIN_API_KEY=.\+' "$ENV_FILE"; then
        ok "ADMIN_API_KEY задан"
    else
        fail "ADMIN_API_KEY пуст/отсутствует в .env" "Запусти установщик заново или задай вручную."
    fi
    env_perms="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || echo '')"
    if [ -n "$env_perms" ]; then
        case "$env_perms" in
            *00) ok ".env недоступен для чтения другим пользователям (права $env_perms)" ;;
            *)   fail ".env доступен для чтения не только владельцу (права $env_perms)" \
                    "chmod 600 $ENV_FILE - там секреты (BOT_TOKEN, ADMIN_API_KEY)." ;;
        esac
    fi
else
    fail ".env не найден по пути $ENV_FILE" "Запусти install.sh, чтобы его создать."
fi

# Backup archives, if any exist, should be similarly locked down (spec 9:
# "проверить права ... backup archives").
BACKUP_DIR="$PROJECT_DIR/data/backups"
if [ -d "$BACKUP_DIR" ]; then
    world_readable_backups="$(find "$BACKUP_DIR" -maxdepth 1 -name '*.tar.gz' -perm -044 2>/dev/null | wc -l)"
    if [ "${world_readable_backups:-0}" -eq 0 ]; then
        ok "Файлы бэкапов не читаются другими пользователями"
    else
        fail "$world_readable_backups файл(ов) бэкапа доступны для чтения не только владельцу" "chmod 600 $BACKUP_DIR/*.tar.gz"
    fi
fi

# ------------------------------------------------------------------
# SERVER
# ------------------------------------------------------------------
category "SERVER"

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

# ------------------------------------------------------------------
# NETWORK
# ------------------------------------------------------------------
category "NETWORK"

# HTTP reachability
PORT="$(grep -oP '^PORT=\K.*' "$ENV_FILE" 2>/dev/null || echo 8280)"
if curl -fsS -m 5 "http://localhost:${PORT}/healthz" >/dev/null 2>&1; then
    ok "HTTP /healthz отвечает на порту $PORT"
else
    fail "HTTP /healthz не отвечает на порту $PORT" "Контейнер может ещё стартовать, либо порт заблокирован."
fi

# PUBLIC_URL - this is what gets baked into every agent's SERVER_URL, so a
# missing/wrong port here means every remote node silently fails to
# report in, with no error visible on the server side at all.
PUBLIC_URL="$(grep -oP '^PUBLIC_URL=\K.*' "$ENV_FILE" 2>/dev/null || true)"
if [ -z "$PUBLIC_URL" ]; then
    fail "PUBLIC_URL не задан в .env" "Без него добавление ноды выдаст неполный адрес. Задай вручную: PUBLIC_URL=http://<адрес>:${PORT}"
elif [ "$PORT" != "80" ] && [[ "$PUBLIC_URL" != *":$PORT" ]]; then
    fail "PUBLIC_URL не содержит порт ($PUBLIC_URL, PORT=$PORT)" \
        "Новые ноды будут стучаться не туда. Исправь: PUBLIC_URL=${PUBLIC_URL}:${PORT} в .env, затем domain-monitor-server restart"
else
    ok "PUBLIC_URL содержит правильный порт ($PUBLIC_URL)"
fi

# Firewall - a locally-passing healthcheck says nothing about whether a
# remote node can actually reach this port from outside.
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "^Status: active"; then
    if ufw status 2>/dev/null | grep -qE "^${PORT}([/ ]|$)"; then
        ok "UFW разрешает входящие на порту $PORT"
    else
        fail "UFW активен, но не разрешает входящие на порту $PORT" \
            "Внешние ноды не смогут достучаться до сервера. Открой порт: ufw allow ${PORT}/tcp"
    fi
fi

category "TELEGRAM"

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

category "SYSTEM"

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
