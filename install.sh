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
set -uo pipefail

REPO_URL="https://github.com/Theraf1u/domain-monitor.git"
DEFAULT_INSTALL_DIR="/opt/domain-monitor"
PROJECT_DIR=""

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "This installer must be run as root (use sudo)." >&2
        exit 1
    fi
}

# If this script is already running from inside a checkout (server/ and
# agent/ sitting right next to it), use that instead of cloning a second
# copy - lets `cd domain-monitor && sudo bash install.sh` work too, not
# just the curl-pipe-bash one-liner.
resolve_project_dir() {
    local here
    here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
    if [ -d "$here/server" ] && [ -d "$here/agent" ]; then
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

show_menu() {
    echo
    echo "== Domain Monitor =="
    echo
    echo "1) Agent        - только сниффер трафика (ставится на каждую VPN-ноду)"
    echo "2) Server       - только сервер + Telegram-бот (центральная точка управления)"
    echo "3) Оба          - сервер и агент вместе, на этой же машине"
    echo "4) Удалить всё  - снести контейнеры/образы/данные/CLI/папку проекта"
    echo "5) Выход"
    echo
    local choice
    read -r -p "> " choice </dev/tty
    case "$choice" in
        1) exec bash "$PROJECT_DIR/agent/install-agent.sh" ;;
        2) exec bash "$PROJECT_DIR/server/install.sh" ;;
        3) install_both ;;
        4) uninstall_all ;;
        5) exit 0 ;;
        *) echo "Неверный выбор."; show_menu ;;
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
    echo "  - CLI-команды (/usr/local/bin/domain-monitor-server, domain-monitor-agent)"
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

    rm -f /usr/local/bin/domain-monitor-server /usr/local/bin/domain-monitor-agent

    echo "[*] Удаляю $PROJECT_DIR ..."
    cd /
    rm -rf "${PROJECT_DIR:?}"

    echo
    echo "[OK] Domain Monitor полностью удалён с этой машины."
}

main() {
    check_root
    resolve_project_dir
    show_menu
}

main "$@"
