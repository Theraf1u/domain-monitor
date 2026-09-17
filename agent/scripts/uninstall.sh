#!/usr/bin/env bash
# Fully removes the Domain Monitor Agent: container, image, CLI wrapper,
# and (on confirmation) local data and the project directory itself.
set -uo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI_TARGET="/usr/local/bin/domain-monitor-agent"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root (use sudo)." >&2
    exit 1
fi

echo "This will stop and remove the Domain Monitor Agent container and Docker image."
read -r -p "Continue? (yes/no): " confirm </dev/tty
if [[ ! "$confirm" =~ ^[Yy] ]]; then
    echo "Cancelled."
    exit 0
fi

(cd "$PROJECT_DIR" && compose down --rmi local 2>/dev/null) || true

if [ -f "$CLI_TARGET" ]; then
    rm -f "$CLI_TARGET"
    echo "Removed $CLI_TARGET"
fi

read -r -p "Also delete the local event buffer in ./data? (yes/no): " wipe_data </dev/tty
if [[ "$wipe_data" =~ ^[Yy] ]]; then
    rm -rf "${PROJECT_DIR:?}/data"
    echo "Data removed."
fi

if [ -d "$PROJECT_DIR/backups" ]; then
    read -r -p "Also delete backups in ./backups? (yes/no): " wipe_backups </dev/tty
    if [[ "$wipe_backups" =~ ^[Yy] ]]; then
        rm -rf "${PROJECT_DIR:?}/backups"
        echo "Backups removed."
    fi
fi

read -r -p "Also delete the whole project directory ($PROJECT_DIR)? (yes/no): " wipe_all </dev/tty
if [[ "$wipe_all" =~ ^[Yy] ]]; then
    cd /
    rm -rf "${PROJECT_DIR:?}"
    echo "Project directory removed. Uninstall complete."
else
    echo "Uninstall complete. Project files remain at $PROJECT_DIR (delete manually if no longer needed)."
fi
