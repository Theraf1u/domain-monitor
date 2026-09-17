#!/usr/bin/env bash
set -uo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

while true; do
    echo
    echo "== Domain Monitor Server =="
    echo "1) Status"
    echo "2) Logs"
    echo "3) Restart"
    echo "4) Update (pull + rebuild)"
    echo "5) Doctor (diagnostics)"
    echo "6) Backup"
    echo "7) Restore"
    echo "8) Uninstall"
    echo "9) Exit"
    read -r -p "> " choice </dev/tty
    case "$choice" in
        1) bash "$PROJECT_DIR/scripts/healthcheck.sh" ;;
        2) (cd "$PROJECT_DIR" && compose logs --tail 100 -f) ;;
        3) (cd "$PROJECT_DIR" && compose restart) ;;
        4) bash "$PROJECT_DIR/scripts/update.sh" ;;
        5) bash "$PROJECT_DIR/scripts/doctor.sh" ;;
        6) bash "$PROJECT_DIR/scripts/backup.sh" ;;
        7) read -r -p "Backup file name (in ./backups): " f </dev/tty; bash "$PROJECT_DIR/scripts/restore.sh" "$f" ;;
        8) bash "$PROJECT_DIR/scripts/uninstall.sh"; exit 0 ;;
        9) exit 0 ;;
        *) echo "Unknown option" ;;
    esac
done
