#!/usr/bin/env bash
# Domain Monitor Agent installer.
#
#   curl -fsSL https://.../install-agent.sh | sudo bash
#
# Idempotent: safe to re-run. If a .env already exists, re-running goes
# straight to the management menu instead of the setup wizard.
set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$PROJECT_DIR/scripts"
ENV_FILE="$PROJECT_DIR/.env"
CLI_TARGET="/usr/local/bin/domain-monitor-agent"

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
    echo "== Domain Monitor Agent - setup =="
    echo

    local server_url node_token node_name interface

    while true; do
        prompt server_url "Server URL (e.g. https://monitor.example.com)" ""
        [[ "$server_url" =~ ^https?:// ]] && break
        echo "Must start with http:// or https://"
    done

    while true; do
        prompt node_token "Node Token (from the Server's Add Node)" ""
        [[ "$node_token" == nmt_* ]] && break
        echo "Doesn't look like a node token (expected to start with 'nmt_')"
    done

    prompt node_name "Node Name (local label, cosmetic only)" "$(hostname)"
    prompt interface "Network interface to capture on" "any"

    cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
    sed -i "s|^SERVER_URL=.*|SERVER_URL=${server_url}|" "$ENV_FILE"
    sed -i "s|^NODE_TOKEN=.*|NODE_TOKEN=${node_token}|" "$ENV_FILE"
    sed -i "s|^NODE_NAME=.*|NODE_NAME=${node_name}|" "$ENV_FILE"
    sed -i "s|^INTERFACE=.*|INTERFACE=${interface}|" "$ENV_FILE"
    chmod 600 "$ENV_FILE"

    echo
    echo "[*] Building and starting the agent ..."
    (cd "$PROJECT_DIR" && compose up -d --build)

    install_cli_wrapper
    echo
    echo "[OK] Agent installed and running."
    echo "     Check status:  domain-monitor-agent status"
    echo "     Follow logs:   domain-monitor-agent logs"
}

install_cli_wrapper() {
    chmod +x "$PROJECT_DIR/bin/domain-monitor-agent"
    ln -sf "$PROJECT_DIR/bin/domain-monitor-agent" "$CLI_TARGET"
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
