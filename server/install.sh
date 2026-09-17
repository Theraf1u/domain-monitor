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

run_wizard() {
    echo
    echo "== Domain Monitor Server - setup =="
    echo

    local admin_key public_url port bot_token admin_id

    local generated_key
    generated_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))' 2>/dev/null || head -c32 /dev/urandom | base64 | tr -d '/+=' | head -c43)"
    prompt admin_key "Admin API key (used for node management)" "$generated_key"

    prompt public_url "Public URL agents/browsers will reach this server at" "http://$(curl -s -4 -m 3 ifconfig.me 2>/dev/null || hostname)"
    prompt port "Port to listen on" "8000"

    echo
    echo "Telegram bot is optional - leave blank to skip and use Web Admin only."
    prompt bot_token "Telegram BOT_TOKEN (optional)" ""
    if [ -n "$bot_token" ]; then
        prompt admin_id "Telegram ADMIN_ID (your numeric Telegram user id)" ""
    fi

    cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
    sed -i "s|^ADMIN_API_KEY=.*|ADMIN_API_KEY=${admin_key}|" "$ENV_FILE"
    sed -i "s|^PUBLIC_URL=.*|PUBLIC_URL=${public_url}|" "$ENV_FILE"
    sed -i "s|^PORT=.*|PORT=${port}|" "$ENV_FILE"
    if [ -n "${bot_token:-}" ]; then
        {
            echo "BOT_TOKEN=${bot_token}"
            echo "ADMIN_ID=${admin_id}"
        } >> "$ENV_FILE"
    fi
    chmod 600 "$ENV_FILE"

    echo
    echo "[*] Building and starting the server ..."
    (cd "$PROJECT_DIR" && compose up -d --build)

    install_cli_wrapper
    echo
    echo "[OK] Server installed and running."
    echo "     Web Admin:  ${public_url}/admin/  (first visit creates the Owner account)"
    echo "     API docs:   ${public_url}/docs"
    echo "     Status:     domain-monitor-server status"
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
