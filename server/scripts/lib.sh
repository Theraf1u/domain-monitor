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

# detect_compose() (spec 2.0 Part 2, section 4.1): the single resolver
# every script uses, in order - (1) plugin already there, (2) legacy
# docker-compose already there, (3) try installing the plugin package,
# (4) fatal. Never reinstalls what's already present (section 4.2) - the
# is-it-there check always runs first and short-circuits.
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

# ensure_docker() (spec 4.2): if Docker's already installed, this never
# reinstalls it - only checks `docker info` (daemon actually up, not just
# the binary present) and, if the daemon is down and systemd manages it,
# starts/enables the existing installation. Only installs from scratch
# when the `docker` command itself is missing entirely.
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

# ssh_port_in_use() (spec 5.1): the port to make sure UFW allows BEFORE
# ever enabling it, so a first `ufw enable` on a freshly-provisioned box
# can never lock out the very session running the installer. Tries, in
# order: the actual running sshd's effective config (most authoritative -
# reflects any Port/ListenAddress override, even a non-standard one),
# then the current SSH session's own connection info, then a raw grep of
# sshd_config, then the universal fallback of 22 - matching the spec's
# own fallback chain exactly.
ssh_port_in_use() {
    local port
    port="$(sshd -T 2>/dev/null | awk '/^port / {print $2; exit}')"
    if [ -n "${port:-}" ]; then
        echo "$port"
        return 0
    fi
    if [ -n "${SSH_CONNECTION:-}" ]; then
        port="$(echo "$SSH_CONNECTION" | awk '{print $4}')"
        if [ -n "${port:-}" ]; then
            echo "$port"
            return 0
        fi
    fi
    port="$(grep -oP '^\s*Port\s+\K\d+' /etc/ssh/sshd_config 2>/dev/null | head -1)"
    if [ -n "${port:-}" ]; then
        echo "$port"
        return 0
    fi
    echo 22
}

# ufw_safe_enable() (spec 5.1-5.3): idempotent - if UFW is already
# active, this only makes sure the SSH port and any extra ports passed in
# are allowed (with our own comment, spec 5.2) and does NOT touch
# anything else already configured (never `ufw reset`, never removes a
# rule it didn't add itself). If UFW isn't active yet, allows SSH + the
# extra ports FIRST, only then runs `ufw --force enable` - the ordering
# is the entire point, since enabling with a default-deny policy before
# SSH is explicitly allowed is exactly how a fresh box locks itself out.
ufw_safe_enable() {
    if ! command -v ufw >/dev/null 2>&1; then
        if command -v apt-get >/dev/null 2>&1; then
            echo "[*] UFW не найден - устанавливаю..." >&2
            apt-get update -qq >/dev/null 2>&1
            apt-get install -y -qq ufw >/dev/null 2>&1 || true
        fi
        if ! command -v ufw >/dev/null 2>&1; then
            echo "UFW недоступен и автоустановка не удалась - брандмауэр не настроен, настрой вручную при необходимости." >&2
            return 1
        fi
    fi

    local ssh_port
    ssh_port="$(ssh_port_in_use)"
    ufw_allow_tagged "$ssh_port" "tcp" "Domain Monitor SSH safety"
    for extra_port in "$@"; do
        ufw_allow_tagged "$extra_port" "tcp" "Domain Monitor API"
    done

    if ufw status 2>/dev/null | grep -q "^Status: active"; then
        echo "[*] UFW уже активен - только убедился, что нужные порты открыты."
        return 0
    fi
    echo "[*] Включаю UFW (SSH-порт $ssh_port уже разрешён, чтобы не потерять доступ)..." >&2
    ufw --force enable >/dev/null 2>&1
    echo "[*] UFW включён."
}

# ufw_allow_tagged() - idempotent "allow" with our own comment, so a
# later uninstall (or a repeat install) can tell OUR rule apart from
# anything the admin configured by hand for the same port (spec 5.2/5.3:
# comments + no duplicate rules on repeat runs).
ufw_allow_tagged() {
    local port="$1" proto="${2:-tcp}" comment="${3:-Domain Monitor}"
    if ! command -v ufw >/dev/null 2>&1; then
        return 0
    fi
    if ufw status 2>/dev/null | grep -qE "^${port}(/${proto})?([[:space:]]|$)"; then
        return 0  # already allowed (by us or anyone else) - idempotent, no duplicate
    fi
    ufw allow "${port}/${proto}" comment "$comment" >/dev/null 2>&1
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
# alone: this only touches what it can safely identify and reverse. Tags
# the rule with a comment (spec 5.2) so uninstall can prove it's the one
# that created it, instead of pattern-matching a bare allow rule that
# could coincidentally be something the admin added by hand.
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
    ufw_allow_tagged "$port" "tcp" "Domain Monitor API"
    echo "[*] UFW активен - открыл порт ${port}/tcp для входящих (иначе внешние ноды не достучатся)."
}

# Mirror of open_firewall_port() for uninstall - only closes a rule
# tagged with OUR OWN comment (spec 5.4: "не удалять, если нельзя
# доказать, что он создан исключительно проектом"), never a bare
# "<port>/tcp ALLOW Anywhere" rule that could just as easily be something
# the admin added by hand for an unrelated reason.
close_firewall_port() {
    local port="$1"
    [ -z "$port" ] && return
    if ! command -v ufw >/dev/null 2>&1; then
        return
    fi
    if ! ufw status 2>/dev/null | grep -q "^Status: active"; then
        return
    fi
    if ! ufw status 2>/dev/null | grep -qE "^${port}/tcp[[:space:]].*#[[:space:]]*Domain Monitor API[[:space:]]*$"; then
        return
    fi
    ufw delete allow "${port}/tcp" >/dev/null 2>&1
    echo "[*] UFW: закрыл порт ${port}/tcp, который открывал установщик."
}
