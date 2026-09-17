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

# Scans upward from $1 (default 8000) for the first free port - used as
# the wizard's default so pressing Enter almost always just works,
# instead of always suggesting 8000 and making the user retype it when
# it's already taken (which is common on a box running other services).
find_free_port() {
    local port="${1:-8000}"
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
