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
        echo "Этот установщик нужно запускать от root (используй sudo)." >&2
        exit 1
    fi
}

# The node's public IP, not its hostname - an admin managing several VPN
# nodes from the bot's node list recognizes "45.137.202.118" at a glance,
# while a generic cloud-provider hostname tells them nothing. Falls back
# to hostname if outbound access to ifconfig.me fails, so setup never
# hard-fails on it.
detect_node_ip() {
    local ip
    ip="$(curl -s -4 -m 3 ifconfig.me 2>/dev/null)"
    if [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "$ip"
    else
        hostname
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
    # Write to a temp file and rename into place rather than overwriting
    # $DM_COMMAND directly - this process may itself be executing from
    # that exact inode (re-running `dm` after a git pull), and an
    # in-place cp/write would truncate the file bash is still reading
    # from mid-script. A rename swaps the directory entry atomically and
    # leaves the old inode's contents intact for any process still using it.
    local tmp="${DM_COMMAND}.new.$$"
    cp "$PROJECT_DIR/install.sh" "$tmp"
    chmod +x "$tmp"
    mv -f "$tmp" "$DM_COMMAND"
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
    printf "${C_SUB}Domain Monitor${C_RESET}\n"
    echo
    printf "${C_LABEL}Запуск из любой точки сервера:${C_RESET} ${C_BOLD}%s${C_RESET}\n" "dm"
    printf "${C_LABEL}Server:${C_RESET} %b   ${C_LABEL}Agent:${C_RESET} %b\n" "$(component_status server)" "$(component_status agent)"
    echo
}

print_menu_box() {
    box_top
    box_line "1) Оба          - сервер (бот) и агент на этой машине" "${C_NUM}1)${C_RESET} Оба          - сервер (бот) и агент на этой машине"
    box_line "2) Agent        - только сниффер трафика" "${C_NUM}2)${C_RESET} Agent        - только сниффер трафика"
    box_line "3) Server       - сервер + Telegram-бот" "${C_NUM}3)${C_RESET} Server       - сервер + Telegram-бот"
    box_line "4) Статус       - что установлено и работает" "${C_NUM}4)${C_RESET} Статус       - что установлено и работает"
    box_line "5) Диагностика  - проверить установленные компоненты" "${C_NUM}5)${C_RESET} Диагностика  - проверить установленные компоненты"
    box_line "6) Управление скриптом  - переустановка, обновление" "${C_NUM}6)${C_RESET} Управление скриптом  - переустановка, обновление"
    box_empty
    box_line "0) Выход" "${C_NUM}0)${C_RESET} Выход"
    box_bottom
}

show_status() {
    echo
    printf "${C_LABEL}Server:${C_RESET} %b\n" "$(component_status server)"
    printf "${C_LABEL}Agent:${C_RESET}  %b\n" "$(component_status agent)"
    echo
    read -r -p "Enter - назад в меню" _ </dev/tty
}

run_doctor() {
    echo
    if [ -f "$PROJECT_DIR/server/.env" ]; then
        echo "== Server =="
        bash "$PROJECT_DIR/server/scripts/doctor.sh"
        echo
    fi
    if [ -f "$PROJECT_DIR/agent/.env" ]; then
        echo "== Agent =="
        bash "$PROJECT_DIR/agent/scripts/doctor.sh"
        echo
    fi
    if [ ! -f "$PROJECT_DIR/server/.env" ] && [ ! -f "$PROJECT_DIR/agent/.env" ]; then
        echo "Ничего не установлено - нечего проверять."
        echo
    fi
    read -r -p "Enter - назад в меню" _ </dev/tty
}

show_menu() {
    print_banner
    print_menu_box
    echo
    local choice
    read -r -p "$(printf "${C_LABEL}Выбери действие${C_RESET} ${C_OFF}[0-6]${C_RESET}: ")" choice </dev/tty
    case "$choice" in
        1) install_both ;;
        2) exec bash "$PROJECT_DIR/agent/install-agent.sh" ;;
        3) exec bash "$PROJECT_DIR/server/install.sh" ;;
        4) show_status; show_menu ;;
        5) run_doctor; show_menu ;;
        6) script_management_menu; show_menu ;;
        0) exit 0 ;;
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
    for _ in $(seq 1 60); do
        if curl -fsS -m 2 "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1; then
            ready=1
            break
        fi
        sleep 1
    done
    if [ "$ready" -ne 1 ]; then
        # HTTP checks from this shell can occasionally miss a server that's
        # actually fine (seen on a loaded host: curl times out a few times
        # right after container start even though the app itself came up
        # in under a second) - fall back to Docker's own healthcheck, which
        # polls from inside the same network namespace, before giving up.
        echo "[*] HTTP-проверка не прошла за 60 сек, смотрю статус контейнера напрямую ..."
        local health
        health="$(docker inspect --format '{{.State.Health.Status}}' domain-monitor-server 2>/dev/null || echo unknown)"
        if [ "$health" = "healthy" ] || [ "$health" = "starting" ]; then
            echo "[*] Контейнер сообщает статус '$health' - считаю сервер рабочим, продолжаю."
        else
            echo "[FAILED] Сервер не поднялся (статус контейнера: $health)." >&2
            echo "         Проверь: domain-monitor-server status / domain-monitor-server logs" >&2
            exit 1
        fi
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
    node_name="$(detect_node_ip)"
    interface="any"
    echo "Имя ноды: $node_name, интерфейс: $interface (поменять можно потом в agent/.env)"

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

    if ! (cd "$PROJECT_DIR/agent" && source scripts/lib.sh && compose_build_quiet); then
        echo "[ОШИБКА] Сборка/запуск агента не завершились - см. ошибку выше." >&2
        exit 1
    fi

    chmod +x "$PROJECT_DIR/agent/bin/domain-monitor-agent"
    ln -sf "$PROJECT_DIR/agent/bin/domain-monitor-agent" /usr/local/bin/domain-monitor-agent

    echo
    echo "[OK] Server + Agent установлены и запущены на этой машине."
    echo "     Открой бота в Telegram и нажми /start - нода '${node_name}' уже там."
    echo "     Статус: domain-monitor-server status / domain-monitor-agent status"
}

# Actual teardown work, no prompts - shared by uninstall_all() and
# reinstall_all() so there's exactly one place that knows how to fully
# remove the thing, instead of two copies that can drift apart.
_do_uninstall() {
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
    disable_auto_update_quiet

    echo "[*] Удаляю $PROJECT_DIR ..."
    cd /
    rm -rf "${PROJECT_DIR:?}"
}

uninstall_all() {
    echo
    echo "Это удалит с этой машины ВСЁ, что относится к Domain Monitor:"
    echo "  - контейнеры и образы domain-monitor-server / domain-monitor-agent"
    echo "  - их данные (база нод/доменов на сервере, локальный буфер агента) и .env"
    echo "  - CLI-команды (domain-monitor-server, domain-monitor-agent, dm)"
    echo "  - автообновление по расписанию, если было включено"
    echo "  - всю папку проекта: $PROJECT_DIR"
    echo
    local confirm
    read -r -p "Продолжить? (y/n): " confirm </dev/tty
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo "Отменено."
        return
    fi

    _do_uninstall

    echo
    echo "[OK] Domain Monitor полностью удалён с этой машины."
}

reinstall_all() {
    echo
    echo "Это полностью снесёт текущую установку (контейнеры, образы, данные,"
    echo ".env, автообновление) и сразу откроет мастер установки заново, с нуля."
    echo
    local confirm
    read -r -p "Продолжить? (y/n): " confirm </dev/tty
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo "Отменено."
        return
    fi

    _do_uninstall
    echo
    echo "[*] Ставлю заново ..."
    resolve_project_dir
    install_dm_command
    show_menu
}

# Pulls the latest code and rebuilds/restarts whichever components are
# actually installed - skips a component entirely if it was never set up
# (no .env), same "only touch what's there" rule as the rest of the menu.
update_all() {
    echo
    echo "[*] Обновляю Domain Monitor ..."
    if [ ! -d "$PROJECT_DIR/.git" ]; then
        echo "[!] $PROJECT_DIR - это не git-checkout, обновление кода невозможно." >&2
        echo "    Переустанови через пункт «Управление скриптом -> Переустановить»." >&2
        return 1
    fi
    if ! (cd "$PROJECT_DIR" && git fetch --quiet origin && git reset --quiet --hard origin/HEAD); then
        echo "[ОШИБКА] Не удалось забрать обновления с git." >&2
        return 1
    fi
    install_dm_command

    local touched=0
    if [ -f "$PROJECT_DIR/server/.env" ]; then
        echo "[*] Пересобираю Server ..."
        (cd "$PROJECT_DIR/server" && source scripts/lib.sh && compose_build_quiet) && touched=1
    fi
    if [ -f "$PROJECT_DIR/agent/.env" ]; then
        echo "[*] Пересобираю Agent ..."
        (cd "$PROJECT_DIR/agent" && source scripts/lib.sh && compose_build_quiet) && touched=1
    fi
    if [ "$touched" -eq 0 ]; then
        echo "[*] Ничего не установлено - код обновлён, пересобирать нечего."
    fi
    echo
    echo "[OK] Обновление завершено."
}

# ------------------------------------------------------------------
# Auto-update: a root crontab entry that calls this same script with
# --auto-update, which just runs update_all() non-interactively and
# exits - no menu, no prompts, safe for cron. Entries are tagged with
# CRON_MARKER so enabling/disabling never disturbs any of the admin's
# own unrelated crontab lines.
# ------------------------------------------------------------------
CRON_MARKER="# domain-monitor-auto-update"

is_auto_update_enabled() {
    crontab -l 2>/dev/null | grep -qF "$CRON_MARKER"
}

disable_auto_update_quiet() {
    crontab -l 2>/dev/null | grep -vF "$CRON_MARKER" | crontab - 2>/dev/null || true
}

enable_auto_update() {
    if ! command -v crontab >/dev/null 2>&1; then
        echo "[!] На этой машине нет cron/crontab - автообновление недоступно." >&2
        return 1
    fi
    echo
    echo "Как часто автоматически обновлять и пересобирать (git pull + rebuild)?"
    echo "1) Ежедневно, в 03:00"
    echo "2) Раз в неделю, воскресенье в 03:00"
    echo "3) Свой график (в формате cron)"
    echo "0) Отмена"
    local choice schedule
    read -r -p "Выбор [0-3]: " choice </dev/tty
    case "$choice" in
        1) schedule="0 3 * * *" ;;
        2) schedule="0 3 * * 0" ;;
        3)
            read -r -p "Cron-выражение (например: 0 4 * * *), пусто - отмена: " schedule </dev/tty
            if [ -z "$schedule" ]; then
                echo "Отменено."
                return 0
            fi
            ;;
        0|"") echo "Отменено."; return 0 ;;
        *) echo "Неверный выбор."; return 1 ;;
    esac

    disable_auto_update_quiet
    (
        crontab -l 2>/dev/null
        echo "$schedule $DM_COMMAND --auto-update >> $PROJECT_DIR/auto-update.log 2>&1 $CRON_MARKER"
    ) | crontab -
    echo
    echo "[OK] Автообновление включено: $schedule"
    echo "     Лог: $PROJECT_DIR/auto-update.log"
}

disable_auto_update() {
    disable_auto_update_quiet
    echo
    echo "[OK] Автообновление выключено."
}

toggle_auto_update() {
    if is_auto_update_enabled; then
        disable_auto_update
    else
        enable_auto_update
    fi
}

print_script_menu_box() {
    local au_status="$1" au_label
    if [ "$au_status" = "включено" ]; then
        au_label="${C_OK}включено${C_RESET}"
    else
        au_label="${C_OFF}выключено${C_RESET}"
    fi
    box_top
    box_line "1) Переустановить    - полный снос и установка заново" "${C_NUM}1)${C_RESET} Переустановить    - полный снос и установка заново"
    box_line "2) Удалить           - снести всё, что тут стоит" "${C_NUM}2)${C_RESET} Удалить           - снести всё, что тут стоит"
    box_line "3) Обновить          - git pull + пересборка компонентов" "${C_NUM}3)${C_RESET} Обновить          - git pull + пересборка компонентов"
    box_line "4) Автообновление ($au_status)" "${C_NUM}4)${C_RESET} Автообновление (${au_label})"
    box_empty
    box_line "0) Назад" "${C_NUM}0)${C_RESET} Назад"
    box_bottom
}

script_management_menu() {
    while true; do
        local au_status
        if is_auto_update_enabled; then
            au_status="включено"
        else
            au_status="выключено"
        fi
        print_banner
        printf "${C_LABEL}Управление скриптом${C_RESET}\n\n"
        print_script_menu_box "$au_status"
        echo
        local choice
        read -r -p "$(printf "${C_LABEL}Выбор${C_RESET} ${C_OFF}[0-4]${C_RESET}: ")" choice </dev/tty
        case "$choice" in
            1) reinstall_all; return ;;
            2) uninstall_all; exit 0 ;;
            3) update_all; read -r -p "Enter - назад в меню" _ </dev/tty ;;
            4) toggle_auto_update; read -r -p "Enter - назад в меню" _ </dev/tty ;;
            0) return ;;
            *) echo "Неверный выбор."; sleep 1 ;;
        esac
    done
}

main() {
    check_root
    if [ "${1:-}" = "--auto-update" ]; then
        resolve_project_dir
        update_all
        exit 0
    fi
    resolve_project_dir
    install_dm_command
    show_menu
}

main "$@"
