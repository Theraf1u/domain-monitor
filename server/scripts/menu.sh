#!/usr/bin/env bash
set -uo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

while true; do
    echo
    echo "== Domain Monitor Server =="
    echo "1) Статус"
    echo "2) Логи"
    echo "3) Перезапуск"
    echo "4) Обновить (pull + пересборка)"
    echo "5) Диагностика"
    echo "6) Бэкап"
    echo "7) Восстановить"
    echo "8) Добавить ноду"
    echo "9) Удалить"
    echo "10) Выход"
    read -r -p "> " choice </dev/tty
    case "$choice" in
        1) bash "$PROJECT_DIR/scripts/healthcheck.sh" ;;
        2) (cd "$PROJECT_DIR" && compose logs --tail 100 -f) ;;
        3) (cd "$PROJECT_DIR" && compose restart) ;;
        4) bash "$PROJECT_DIR/scripts/update.sh" ;;
        5) bash "$PROJECT_DIR/scripts/doctor.sh" ;;
        6) bash "$PROJECT_DIR/scripts/backup.sh" ;;
        7) read -r -p "Имя файла бэкапа (в ./backups): " f </dev/tty; bash "$PROJECT_DIR/scripts/restore.sh" "$f" ;;
        8) bash "$PROJECT_DIR/scripts/add_node.sh" ;;
        9) bash "$PROJECT_DIR/scripts/uninstall.sh"; exit 0 ;;
        10) exit 0 ;;
        *) echo "Неверный выбор" ;;
    esac
done
