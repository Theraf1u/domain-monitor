#!/usr/bin/env bash
# Domain Monitor Server installer.
#
#   curl -fsSL https://.../install.sh | sudo bash
#
# Idempotent: safe to re-run. If a .env already exists, re-running goes
# straight to the management menu instead of the setup wizard.
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$PROJECT_DIR/scripts"
ENV_FILE="$PROJECT_DIR/.env"
CLI_TARGET="/usr/local/bin/domain-monitor-server"

# shellcheck source=scripts/lib.sh
source "$SCRIPTS_DIR/lib.sh"

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "This installer must be run as root (use sudo)." >&2
        exit 1
    fi
}

install_docker_if_missing() {
    if command -v docker >/dev/null 2>&1; then
        return
    fi
    echo "[*] Docker not found, installing via get.docker.com ..."
    curl -fsSL https://get.docker.com | sh
}

prompt() {
    local __resultvar="$1" __message="$2" __default="${3:-}"
    local __input
    if [ -n "$__default" ]; then
        read -r -p "$__message [$__default]: " __input </dev/tty
    else
        read -r -p "$__message: " __input </dev/tty
    fi
    __input="${__input:-$__default}"
    printf -v "$__resultvar" '%s' "$__input"
}

port_is_free() {
    ! ss -tulpn 2>/dev/null | grep -q ":$1 "
}

run_wizard() {
    echo
    echo "== Domain Monitor Server - setup =="
    echo "Telegram is the only management interface - no web UI."
    echo

    local admin_key public_url port bot_token admin_id telegram_proxy

    local generated_key
    generated_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))' 2>/dev/null || head -c32 /dev/urandom | base64 | tr -d '/+=' | head -c43)"
    prompt admin_key "Admin API key (for direct REST API/scripting use)" "$generated_key"

    prompt public_url "Public address agents will reach this server at" "http://$(curl -s -4 -m 3 ifconfig.me 2>/dev/null || hostname)"

    while true; do
        prompt port "Port to listen on" "8000"
        if port_is_free "$port"; then
            break
        fi
        echo "Port $port is already in use on this host (ss -tulpn | grep :$port to see by what). Pick another."
    done

    echo
    while true; do
        prompt bot_token "Telegram BOT_TOKEN (from @BotFather)" ""
        [[ "$bot_token" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]] && break
        echo "Doesn't look like a valid bot token (expected: <digits>:<35+ chars>)."
    done
    while true; do
        prompt admin_id "Your Telegram ADMIN_ID (numeric, e.g. from @userinfobot)" ""
        [[ "$admin_id" =~ ^-?[0-9]+$ ]] && break
        echo "Must be numeric."
    done
    prompt telegram_proxy "Proxy for reaching Telegram, if this host needs one (blank = none)" ""

    cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
    sed -i "s|^ADMIN_API_KEY=.*|ADMIN_API_KEY=${admin_key}|" "$ENV_FILE"
    sed -i "s|^PUBLIC_URL=.*|PUBLIC_URL=${public_url}|" "$ENV_FILE"
    sed -i "s|^PORT=.*|PORT=${port}|" "$ENV_FILE"
    sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=${bot_token}|" "$ENV_FILE"
    sed -i "s|^ADMIN_ID=.*|ADMIN_ID=${admin_id}|" "$ENV_FILE"
    if [ -n "${telegram_proxy:-}" ]; then
        echo "TELEGRAM_PROXY=${telegram_proxy}" >> "$ENV_FILE"
    fi
    chmod 600 "$ENV_FILE"

    if [ -z "${telegram_proxy:-}" ]; then
        echo
        echo "[*] Checking Telegram reachability from this host ..."
        if curl -fsS -m 8 "https://api.telegram.org/bot${bot_token}/getMe" | grep -q '"ok":true'; then
            echo "    OK - Telegram is reachable directly."
        else
            echo "    WARNING: could not reach Telegram directly from this host."
            echo "    If Telegram is blocked on this network, set TELEGRAM_PROXY in .env"
            echo "    (proxy scheme must be plain socks5:// or http://, not socks5h://)."
        fi
    fi

    echo
    echo "[*] Building and starting the server ..."
    if ! (cd "$PROJECT_DIR" && compose up -d --build); then
        echo
        echo "[FAILED] Build/start did not complete - see the error above."
        echo "         Fix the issue, then retry with: cd $PROJECT_DIR && sudo bash install.sh"
        exit 1
    fi

    install_cli_wrapper
    echo
    echo "[OK] Server installed and running."
    echo "     Open your bot in Telegram and send /start."
    echo "     Status: domain-monitor-server status"
}

install_cli_wrapper() {
    chmod +x "$PROJECT_DIR/bin/domain-monitor-server"
    ln -sf "$PROJECT_DIR/bin/domain-monitor-server" "$CLI_TARGET"
}

main() {
    check_root
    install_docker_if_missing
    if [ ! -f "$ENV_FILE" ]; then
        run_wizard
    else
        install_cli_wrapper
        exec bash "$SCRIPTS_DIR/menu.sh"
    fi
}

main "$@"
