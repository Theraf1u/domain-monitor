#!/usr/bin/env bash
# Pulls the latest code and rebuilds/restarts the container. Never touches
# .env or ./data, so config and the local event buffer survive an update.
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root (use sudo)." >&2
    exit 1
fi

cd "$PROJECT_DIR"

if [ -d .git ]; then
    echo "[*] Pulling latest changes..."
    git fetch --quiet origin
    git reset --quiet --hard origin/HEAD
else
    echo "[!] Not a git checkout - rebuilding local files as-is."
fi

echo "[*] Rebuilding image..."
compose build

echo "[*] Restarting..."
compose up -d

echo "[OK] Update complete."
