#!/usr/bin/env bash
# Shared helpers, sourced by every other script in scripts/ and bin/.
set -uo pipefail

have_compose() {
    if docker compose version >/dev/null 2>&1; then
        echo "plugin"
    elif command -v docker-compose >/dev/null 2>&1; then
        echo "standalone"
    else
        echo "none"
    fi
}

# detect_compose()/ensure_docker() (spec 2.0 Part 2, sections 4.1/4.2) -
# identical to the server's copy in server/scripts/lib.sh (kept in sync
# by hand, same as have_compose()/compose()/compose_build_quiet() already
# were). No UFW helpers here on purpose: the agent makes only outbound
# connections to the server and never needs an inbound firewall rule of
# its own (spec 5.5), so there's nothing for it to open/close.
detect_compose() {
    local mode
    mode="$(have_compose)"
    if [ "$mode" != "none" ]; then
        echo "$mode"
        return 0
    fi
    echo "[*] Docker Compose не найден - пробую поставить плагин 'docker compose'..." >&2
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -qq >/dev/null 2>&1
        apt-get install -y -qq docker-compose-plugin >/dev/null 2>&1 || true
    fi
    mode="$(have_compose)"
    if [ "$mode" != "none" ]; then
        echo "$mode"
        return 0
    fi
    echo "Docker Compose недоступен и автоустановка плагина не удалась. Поставь вручную: https://docs.docker.com/compose/install/" >&2
    echo "none"
    return 1
}

ensure_docker() {
    if command -v docker >/dev/null 2>&1; then
        if docker info >/dev/null 2>&1; then
            echo "[*] Docker уже установлен и запущен."
            return 0
        fi
        echo "[*] Docker установлен, но демон не отвечает - пробую запустить через systemd..." >&2
        if command -v systemctl >/dev/null 2>&1; then
            systemctl enable --now docker >/dev/null 2>&1 || true
        fi
        if docker info >/dev/null 2>&1; then
            echo "[*] Docker демон запущен."
            return 0
        fi
        echo "Docker установлен, но демон не запускается. Проверь вручную: systemctl status docker" >&2
        return 1
    fi

    if ! command -v apt-get >/dev/null 2>&1; then
        echo "Docker не найден, а автоустановка поддерживается только для Debian/Ubuntu (apt-get). Поставь Docker вручную: https://docs.docker.com/engine/install/" >&2
        return 1
    fi
    echo "[*] Docker не найден - устанавливаю (docker.io + плагин compose)..." >&2
    apt-get update -qq
    apt-get install -y -qq docker.io docker-compose-plugin
    if command -v systemctl >/dev/null 2>&1; then
        systemctl enable --now docker >/dev/null 2>&1 || true
    fi
    if docker info >/dev/null 2>&1; then
        echo "[*] Docker установлен и запущен."
        return 0
    fi
    echo "Установка Docker завершилась, но демон не отвечает - смотри: systemctl status docker" >&2
    return 1
}

compose() {
    case "$(have_compose)" in
        plugin) docker compose "$@" ;;
        standalone) docker-compose "$@" ;;
        *) echo "Docker Compose не найден (нет ни 'docker compose', ни 'docker-compose')." >&2; exit 1 ;;
    esac
}

port_is_free() {
    ! ss -tulpn 2>/dev/null | grep -q ":$1 "
}

# Scans upward from $1 (default 8280) for the first free port - used as
# the wizard's default so pressing Enter almost always just works,
# instead of always suggesting 8280 and making the user retype it when
# it's already taken (which is common on a box running other services).
find_free_port() {
    local port="${1:-8280}"
    while ! port_is_free "$port"; do
        port=$((port + 1))
    done
    echo "$port"
}

# Runs `compose up -d --build` without spamming the terminal with
# buildkit's full TTY progress UI (which reads as noise, not signal, to
# someone just installing the thing) - shows a short "building..."
# progress indicator instead, and only dumps the real build log if it
# actually fails.
compose_build_quiet() {
    local logfile
    logfile="$(mktemp)"
    compose up -d --build >"$logfile" 2>&1 &
    local build_pid=$!
    printf "[*] Собираю и запускаю (обычно 30-90 сек)"
    while kill -0 "$build_pid" 2>/dev/null; do
        printf "."
        sleep 2
    done
    wait "$build_pid"
    local status=$?
    if [ "$status" -eq 0 ]; then
        echo " готово."
    else
        echo " ОШИБКА."
        echo "--- Последние строки лога сборки ---"
        tail -40 "$logfile"
    fi
    rm -f "$logfile"
    return "$status"
}
