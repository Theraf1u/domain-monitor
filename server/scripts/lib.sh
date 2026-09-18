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

# Opens the API port in UFW if UFW is the thing actually active on this
# box - a node can't reach a port that never got heartbeats/events from
# them for reasons invisible on the server side. Anything else (firewalld,
# a cloud provider's security group, iptables managed by hand) is left
# alone: this only touches what it can safely identify and reverse.
open_firewall_port() {
    local port="$1"
    if ! command -v ufw >/dev/null 2>&1; then
        return
    fi
    if ! ufw status 2>/dev/null | grep -q "^Status: active"; then
        return
    fi
    if ufw status 2>/dev/null | grep -qE "^${port}([/ ]|$)"; then
        echo "[*] UFW уже разрешает порт ${port}."
        return
    fi
    ufw allow "${port}/tcp" >/dev/null 2>&1
    echo "[*] UFW активен - открыл порт ${port}/tcp для входящих (иначе внешние ноды не достучатся)."
}

# Mirror of open_firewall_port() for uninstall - only closes a rule that
# looks like the one install.sh itself would have added (a bare
# "<port>/tcp ALLOW Anywhere" rule, no extra restriction), so it never
# removes something the admin added by hand for a different reason.
close_firewall_port() {
    local port="$1"
    [ -z "$port" ] && return
    if ! command -v ufw >/dev/null 2>&1; then
        return
    fi
    if ! ufw status 2>/dev/null | grep -q "^Status: active"; then
        return
    fi
    if ! ufw status 2>/dev/null | grep -qE "^${port}/tcp[[:space:]]+ALLOW[[:space:]]+Anywhere[[:space:]]*$"; then
        return
    fi
    ufw delete allow "${port}/tcp" >/dev/null 2>&1
    echo "[*] UFW: закрыл порт ${port}/tcp, который открывал установщик."
}
