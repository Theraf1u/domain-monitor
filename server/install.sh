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
    port="$(find_free_port 8280)"
    public_url="http://$(curl -s -4 -m 3 ifconfig.me 2>/dev/null || hostname)"
    # PUBLIC_URL is what every agent's SERVER_URL gets set to - without
    # the port baked in, agents would connect to the address's default
    # port (80) instead of wherever uvicorn actually listens, and every
    # heartbeat/event would silently go nowhere.
    [ "$port" != "80" ] && public_url="${public_url}:${port}"

    echo "Адрес: $public_url   (поменять можно потом в .env)"
    # spec 2.0 Part 2, section 3.7 "stable hostname": PUBLIC_URL is baked
    # into every agent's SERVER_URL - a raw IP means a future server
    # move (Migration 2.0's migrate-to, or just switching hosting
    # providers) requires every agent to actually pick up the new
    # address (which Migration 2.0 does automatically, but a DNS name
    # would let it happen without touching a single agent - see
    # `migrate-to --target-public-url`).
    if [[ "$public_url" =~ ^https?://[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+(:[0-9]+)?$ ]]; then
        echo "💡 Это голый IP. Если заведёшь DNS A-запись на него (например monitor.example.com)"
        echo "   и укажешь PUBLIC_URL=http://monitor.example.com:$port в .env - при будущем переносе"
        echo "   сервера (domain-monitor-server migrate-to) можно будет просто поменять DNS,"
        echo "   не трогая агентов вообще. Необязательно, можно сделать и позже."
    fi
    open_firewall_port "$port"
    echo

    while true; do
        prompt bot_token "Telegram BOT_TOKEN (от @BotFather)" ""
        [[ "$bot_token" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]] && break
        echo "Не похоже на токен бота (формат: <цифры>:<30+ символов>)."
    done
    while true; do
        prompt admin_id "Telegram ADMIN_ID (цифры, узнать у @userinfobot; можно несколько через запятую)" ""
        [[ "$admin_id" =~ ^-?[0-9]+(,-?[0-9]+)*$ ]] && break
        echo "Должно быть числом (или числами через запятую)."
    done
    while true; do
        prompt telegram_proxy "Прокси для Telegram, если этот сервер без него не достучится (Enter - не нужен)" ""
        [ -z "$telegram_proxy" ] && break
        [[ "$telegram_proxy" =~ ^(socks5|http)://[^[:space:]]+:[0-9]+$ ]] && break
        echo "Не похоже на прокси (формат: socks5://host:port или http://host:port, без пробелов; socks5h не поддерживается)."
    done

    cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
    # Locked down BEFORE any secret is written into it, not after - a
    # multi-user box could otherwise catch BOT_TOKEN/ADMIN_API_KEY sitting
    # world-readable for the brief window while the sed calls below fill
    # them in (spec 2.0 Part 2, section 9).
    chmod 600 "$ENV_FILE"
    sed -i "s|^ADMIN_API_KEY=.*|ADMIN_API_KEY=${admin_key}|" "$ENV_FILE"
    sed -i "s|^PUBLIC_URL=.*|PUBLIC_URL=${public_url}|" "$ENV_FILE"
    sed -i "s|^PORT=.*|PORT=${port}|" "$ENV_FILE"
    sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=${bot_token}|" "$ENV_FILE"
    sed -i "s|^ADMIN_ID=.*|ADMIN_ID=${admin_id}|" "$ENV_FILE"
    if [ -n "${telegram_proxy:-}" ]; then
        echo "TELEGRAM_PROXY=${telegram_proxy}" >> "$ENV_FILE"
    fi

    if [ -z "${telegram_proxy:-}" ]; then
        if curl -fsS -m 8 "https://api.telegram.org/bot${bot_token}/getMe" | grep -q '"ok":true'; then
            echo "[*] Telegram доступен напрямую с этого сервера - хорошо."
        else
            echo "[!] Не удалось достучаться до Telegram напрямую."
            echo "    Если он заблокирован на этой сети, пропиши TELEGRAM_PROXY в .env и перезапусти."
        fi
    fi

    echo
    export GIT_REV="$(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
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
