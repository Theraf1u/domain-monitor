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
    echo "== Установка Server =="
    echo "Telegram - единственный способ управления, веб-интерфейса нет."
    echo

    local admin_key public_url port bot_token admin_id telegram_proxy

    admin_key="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))' 2>/dev/null || head -c32 /dev/urandom | base64 | tr -d '/+=' | head -c43)"
    public_url="http://$(curl -s -4 -m 3 ifconfig.me 2>/dev/null || hostname)"
    port="$(find_free_port 8000)"

    echo "Адрес: $public_url   Порт: $port   (поменять можно потом в .env)"
    echo

    while true; do
        prompt bot_token "Telegram BOT_TOKEN (от @BotFather)" ""
        [[ "$bot_token" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]] && break
        echo "Не похоже на токен бота (формат: <цифры>:<30+ символов>)."
    done
    while true; do
        prompt admin_id "Твой Telegram ADMIN_ID (цифры, узнать у @userinfobot)" ""
        [[ "$admin_id" =~ ^-?[0-9]+$ ]] && break
        echo "Должно быть числом."
    done
    prompt telegram_proxy "Прокси для Telegram, если этот сервер без него не достучится (Enter - не нужен)" ""

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
        if curl -fsS -m 8 "https://api.telegram.org/bot${bot_token}/getMe" | grep -q '"ok":true'; then
            echo "[*] Telegram доступен напрямую с этого сервера - хорошо."
        else
            echo "[!] Не удалось достучаться до Telegram напрямую."
            echo "    Если он заблокирован на этой сети, пропиши TELEGRAM_PROXY в .env и перезапусти."
        fi
    fi

    echo
    if ! (cd "$PROJECT_DIR" && compose_build_quiet); then
        echo
        echo "[ОШИБКА] Сборка/запуск не завершились - см. ошибку выше." >&2
        echo "         Исправь и повтори: cd $PROJECT_DIR && sudo bash install.sh" >&2
        exit 1
    fi

    install_cli_wrapper
    echo
    echo "[OK] Server установлен и запущен."
    echo "     Открой бота в Telegram и отправь /start."
    echo "     Статус: domain-monitor-server status"
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
