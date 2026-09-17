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
        echo "Этот установщик нужно запускать от root (используй sudo)." >&2
        exit 1
    fi
}

install_docker_if_missing() {
    if command -v docker >/dev/null 2>&1; then
        return
    fi
    echo "[*] Docker не найден, устанавливаю через get.docker.com ..."
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
    echo "== Установка Agent =="
    echo

    local server_url node_token

    while true; do
        prompt server_url "Server URL (например https://monitor.example.com)" ""
        [[ "$server_url" =~ ^https?:// ]] && break
        echo "Должен начинаться с http:// или https://"
    done

    while true; do
        prompt node_token "Node Token (получен на сервере: Ноды -> Добавить)" ""
        [[ "$node_token" == nmt_* ]] && break
        echo "Не похоже на токен ноды (должен начинаться с nmt_)."
    done

    cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
    sed -i "s|^SERVER_URL=.*|SERVER_URL=${server_url}|" "$ENV_FILE"
    sed -i "s|^NODE_TOKEN=.*|NODE_TOKEN=${node_token}|" "$ENV_FILE"
    sed -i "s|^NODE_NAME=.*|NODE_NAME=$(hostname)|" "$ENV_FILE"
    sed -i "s|^INTERFACE=.*|INTERFACE=any|" "$ENV_FILE"
    chmod 600 "$ENV_FILE"

    echo "Имя ноды: $(hostname), интерфейс: any (поменять можно потом в .env)"
    echo
    if ! (cd "$PROJECT_DIR" && compose_build_quiet); then
        echo
        echo "[ОШИБКА] Сборка/запуск не завершились - см. ошибку выше." >&2
        echo "         Исправь и повтори: cd $PROJECT_DIR && sudo bash install-agent.sh" >&2
        exit 1
    fi

    install_cli_wrapper
    echo
    echo "[OK] Agent установлен и запущен."
    echo "     Статус: domain-monitor-agent status"
    echo "     Логи:   domain-monitor-agent logs"
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
