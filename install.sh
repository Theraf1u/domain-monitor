#!/usr/bin/env bash
# Domain Monitor - unified installer.
#
#   curl -fsSL https://raw.githubusercontent.com/Theraf1u/domain-monitor/main/install.sh | sudo bash
#
# Lets you pick what to install on this machine: only the Agent (traffic
# sniffer, for a VPN node), only the Server (Telegram bot + API, the
# central control point), or both together on the same box. Delegates to
# server/install.sh and agent/install-agent.sh for the actual work - this
# script is just the menu plus the bridging logic for "both" (creating a
# node on the freshly-installed local server and wiring its token straight
# into the agent's config, so you're not copy-pasting a token from
# Telegram into your own terminal).
#
# Installs itself as `dm` on first run, so the menu is reachable from
# anywhere on the box afterward, not just from within the checkout.
set -uo pipefail

REPO_URL="https://github.com/Theraf1u/domain-monitor.git"
DEFAULT_INSTALL_DIR="/opt/domain-monitor"
DM_COMMAND="/usr/local/bin/dm"
PROJECT_DIR=""

# ------------------------------------------------------------------
# Colors / box drawing
# ------------------------------------------------------------------
C_RESET='\033[0m'
C_BOLD='\033[1m'
C_BORDER='\033[0;32m'
C_NICK='\033[1;35m'
C_SUB='\033[0;36m'
C_LABEL='\033[0;36m'
C_NUM='\033[1;33m'
C_OK='\033[0;32m'
C_OFF='\033[0;90m'
C_ERR='\033[0;31m'

BOX_WIDTH=62

hr() { printf '─%.0s' $(seq 1 "$BOX_WIDTH"); }
box_top()    { printf "${C_BORDER}┌%b┐${C_RESET}\n" "$(hr)"; }
box_bottom() { printf "${C_BORDER}└%b┘${C_RESET}\n" "$(hr)"; }
box_empty()  { printf "${C_BORDER}│${C_RESET}%${BOX_WIDTH}s${C_BORDER}│${C_RESET}\n" ""; }

# $1 = plain text used only to compute padding, $2 = the (possibly
# colored) text actually printed - kept separate because ANSI escape
# bytes would otherwise get counted as visible width.
box_line() {
    local plain="  $1" colored="  $2"
    local pad=$(( BOX_WIDTH - ${#plain} ))
    [ "$pad" -lt 0 ] && pad=0
    printf "${C_BORDER}│${C_RESET}%b%*s${C_BORDER}│${C_RESET}\n" "$colored" "$pad" ""
}

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "This installer must be run as root (use sudo)." >&2
        exit 1
    fi
}

# If this script is already running from inside a checkout (server/ and
# agent/ sitting right next to it), use that instead of cloning a second
# copy - lets `cd domain-monitor && sudo bash install.sh` work too, not
# just the curl-pipe-bash one-liner or the installed `dm` command.
resolve_project_dir() {
    local here
    here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd 2>/dev/null || echo "")"
    if [ -n "$here" ] && [ -d "$here/server" ] && [ -d "$here/agent" ]; then
        PROJECT_DIR="$here"
        return
    fi
    if [ -d "$DEFAULT_INSTALL_DIR/server" ] && [ -d "$DEFAULT_INSTALL_DIR/agent" ]; then
        PROJECT_DIR="$DEFAULT_INSTALL_DIR"
        return
    fi
    echo "[*] Cloning Domain Monitor into $DEFAULT_INSTALL_DIR ..."
    if ! command -v git >/dev/null 2>&1; then
        apt-get update -qq && apt-get install -y -qq git
    fi
    git clone --quiet "$REPO_URL" "$DEFAULT_INSTALL_DIR"
    PROJECT_DIR="$DEFAULT_INSTALL_DIR"
}

# Installs this script itself as `dm` so the menu is reachable from
# anywhere afterward. A plain copy (not a symlink) - keeps working even
# if invoked via curl|bash, where there is no source file to link to.
install_dm_command() {
    if [ -f "$DM_COMMAND" ] && cmp -s "$PROJECT_DIR/install.sh" "$DM_COMMAND" 2>/dev/null; then
        return
    fi
    cp "$PROJECT_DIR/install.sh" "$DM_COMMAND"
    chmod +x "$DM_COMMAND"
}

component_status() {
    # $1 = server|agent -> prints a short colored status string
    local component="$1" name="domain-monitor-${1}"
    if [ ! -f "$PROJECT_DIR/$component/.env" ]; then
        printf "${C_OFF}не установлен${C_RESET}"
        return
    fi
    if docker inspect "$name" >/dev/null 2>&1 && [ "$(docker inspect --format '{{.State.Running}}' "$name" 2>/dev/null)" = "true" ]; then
        printf "${C_OK}установлен, работает${C_RESET}"
    else
        printf "${C_ERR}установлен, не запущен${C_RESET}"
    fi
}

# "THERAF1U" rendered in the ANSI Shadow figlet font.
print_logo() {
    printf "${C_NICK}"
    cat <<'LOGO'
████████╗██╗  ██╗███████╗██████╗  █████╗ ███████╗ ██╗██╗   ██╗
╚══██╔══╝██║  ██║██╔════╝██╔══██╗██╔══██╗██╔════╝███║██║   ██║
   ██║   ███████║█████╗  ██████╔╝███████║█████╗  ╚██║██║   ██║
   ██║   ██╔══██║██╔══╝  ██╔══██╗██╔══██║██╔══╝   ██║██║   ██║
   ██║   ██║  ██║███████╗██║  ██║██║  ██║██║      ██║╚██████╔╝
   ╚═╝   ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝      ╚═╝ ╚═════╝
LOGO
    printf "${C_RESET}"
}

print_banner() {
    clear 2>/dev/null || true
    echo
    print_logo
    printf "                                     ${C_SUB}Domain Monitor${C_RESET}\n"
    echo
    printf "${C_LABEL}Запуск из любой точки сервера:${C_RESET} ${C_BOLD}%s${C_RESET}\n" "dm"
    printf "${C_LABEL}Server:${C_RESET} %b   ${C_LABEL}Agent:${C_RESET} %b\n" "$(component_status server)" "$(component_status agent)"
    echo
}

print_menu_box() {
    box_top
    box_line "1) Agent        - только сниффер трафика" "${C_NUM}1)${C_RESET} Agent        - только сниффер трафика"
    box_line "2) Server       - сервер + Telegram-бот" "${C_NUM}2)${C_RESET} Server       - сервер + Telegram-бот"
    box_line "3) Оба          - сервер и агент на этой машине" "${C_NUM}3)${C_RESET} Оба          - сервер и агент на этой машине"
    box_line "4) Удалить всё  - снести всё, что тут стоит" "${C_NUM}4)${C_RESET} Удалить всё  - снести всё, что тут стоит"
    box_line "5) Выход" "${C_NUM}5)${C_RESET} Выход"
    box_bottom
}

show_menu() {
    print_banner
    print_menu_box
    echo
    local choice
    read -r -p "$(printf "${C_LABEL}Выбери действие${C_RESET} ${C_OFF}[1-5]${C_RESET}: ")" choice </dev/tty
    case "$choice" in
        1) exec bash "$PROJECT_DIR/agent/install-agent.sh" ;;
        2) exec bash "$PROJECT_DIR/server/install.sh" ;;
        3) install_both ;;
        4) uninstall_all ;;
        5) exit 0 ;;
        *) echo "Неверный выбор."; sleep 1; show_menu ;;
    esac
}

install_both() {
    local server_env="$PROJECT_DIR/server/.env"
    local agent_env="$PROJECT_DIR/agent/.env"

    if [ -f "$server_env" ]; then
        echo "[*] Server уже настроен (найден server/.env) - пропускаю установку сервера."
    else
        echo
        echo "[*] Устанавливаю Server ..."
        if ! bash "$PROJECT_DIR/server/install.sh"; then
            echo "[FAILED] Установка сервера не завершилась. Смотри ошибку выше." >&2
            exit 1
        fi
    fi

    local port admin_key
    port="$(grep -oP '^PORT=\K.*' "$server_env")"
    admin_key="$(grep -oP '^ADMIN_API_KEY=\K.*' "$server_env")"

    echo
    echo "[*] Жду готовности сервера ..."
    local ready=0
    for _ in $(seq 1 30); do
        if curl -fsS -m 2 "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1; then
            ready=1
            break
        fi
        sleep 1
    done
    if [ "$ready" -ne 1 ]; then
        echo "[FAILED] Сервер не ответил на /healthz за 30 секунд." >&2
        echo "         Проверь: domain-monitor-server status / domain-monitor-server logs" >&2
        exit 1
    fi

    if [ -f "$agent_env" ]; then
        echo "[*] Agent уже настроен (найден agent/.env) - пропускаю установку агента."
        echo
        echo "[OK] Готово. Server и Agent уже установлены на этой машине."
        return
    fi

    echo
    echo "[*] Настраиваю Agent на этой же машине (SERVER_URL=http://127.0.0.1:${port})"
    local node_name interface
    read -r -p "Имя для этой ноды [$(hostname)]: " node_name </dev/tty
    node_name="${node_name:-$(hostname)}"
    read -r -p "Сетевой интерфейс для захвата трафика [any]: " interface </dev/tty
    interface="${interface:-any}"

    echo "[*] Создаю ноду на сервере ..."
    local resp token
    resp="$(curl -fsS -X POST "http://127.0.0.1:${port}/api/v1/nodes" \
        -H "X-Admin-Key: ${admin_key}" -H "Content-Type: application/json" \
        -d "{\"name\":\"${node_name}\"}")"
    token="$(echo "$resp" | grep -oP '"token"\s*:\s*"\K[^"]+')"
    if [ -z "$token" ]; then
        echo "[FAILED] Не удалось создать ноду через API: $resp" >&2
        echo "         Можно добавить её вручную через бота и запустить агент через agent/install-agent.sh" >&2
        exit 1
    fi

    cp "$PROJECT_DIR/agent/.env.example" "$agent_env"
    sed -i "s|^SERVER_URL=.*|SERVER_URL=http://127.0.0.1:${port}|" "$agent_env"
    sed -i "s|^NODE_TOKEN=.*|NODE_TOKEN=${token}|" "$agent_env"
    sed -i "s|^NODE_NAME=.*|NODE_NAME=${node_name}|" "$agent_env"
    sed -i "s|^INTERFACE=.*|INTERFACE=${interface}|" "$agent_env"
    chmod 600 "$agent_env"

    echo "[*] Собираю и запускаю Agent ..."
    if ! (cd "$PROJECT_DIR/agent" && source scripts/lib.sh && compose up -d --build); then
        echo "[FAILED] Сборка/запуск агента не завершились - см. ошибку выше." >&2
        exit 1
    fi

    chmod +x "$PROJECT_DIR/agent/bin/domain-monitor-agent"
    ln -sf "$PROJECT_DIR/agent/bin/domain-monitor-agent" /usr/local/bin/domain-monitor-agent

    echo
    echo "[OK] Server + Agent установлены и запущены на этой машине."
    echo "     Открой бота в Telegram и нажми /start - нода '${node_name}' уже там."
    echo "     Статус: domain-monitor-server status / domain-monitor-agent status"
}

uninstall_all() {
    echo
    echo "Это удалит с этой машины ВСЁ, что относится к Domain Monitor:"
    echo "  - контейнеры и образы domain-monitor-server / domain-monitor-agent"
    echo "  - их данные (база нод/доменов на сервере, локальный буфер агента) и .env"
    echo "  - CLI-команды (domain-monitor-server, domain-monitor-agent, dm)"
    echo "  - всю папку проекта: $PROJECT_DIR"
    echo
    local confirm
    read -r -p "Продолжить? (yes/no): " confirm </dev/tty
    if [[ ! "$confirm" =~ ^[Yy] ]]; then
        echo "Отменено."
        return
    fi

    for component in server agent; do
        if [ -f "$PROJECT_DIR/$component/docker-compose.yml" ]; then
            echo "[*] Останавливаю $component ..."
            (
                cd "$PROJECT_DIR/$component" || exit 0
                # shellcheck source=./server/scripts/lib.sh
                source "scripts/lib.sh" 2>/dev/null
                compose down --rmi local 2>/dev/null
            ) || true
        fi
    done

    # Belt-and-suspenders: in case compose's own project bookkeeping was
    # stale (e.g. .env got edited/moved since the container was created)
    # and `compose down` above didn't find it.
    docker rm -f domain-monitor-server domain-monitor-agent >/dev/null 2>&1 || true
    docker rmi domain-monitor-server:latest domain-monitor-agent:latest >/dev/null 2>&1 || true

    rm -f /usr/local/bin/domain-monitor-server /usr/local/bin/domain-monitor-agent "$DM_COMMAND"

    echo "[*] Удаляю $PROJECT_DIR ..."
    cd /
    rm -rf "${PROJECT_DIR:?}"

    echo
    echo "[OK] Domain Monitor полностью удалён с этой машины."
}

main() {
    check_root
    resolve_project_dir
    install_dm_command
    show_menu
}

main "$@"
