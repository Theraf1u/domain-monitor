#!/usr/bin/env bash
# Diagnostics: checks the pieces the server actually depends on and prints
# clear pass/fail lines plus a short recommendation on failure. Exits 0 if
# everything checked out, 1 if anything failed.
set -uo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

FAILED=0

ok()   { echo "✓ $1"; }
fail() { echo "✗ $1"; [ -n "${2:-}" ] && echo "  -> $2"; FAILED=1; }

# Docker
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ok "Docker is installed and running"
else
    fail "Docker is not available/running" "Install Docker or start the Docker daemon."
fi

# Compose
if [ "$(have_compose)" != "none" ]; then
    ok "Docker Compose is available"
else
    fail "Docker Compose not found" "Install the 'docker compose' plugin or 'docker-compose'."
fi

# .env
if [ -f "$ENV_FILE" ]; then
    ok ".env exists"
    if grep -q '^ADMIN_API_KEY=.\+' "$ENV_FILE"; then
        ok "ADMIN_API_KEY is set"
    else
        fail "ADMIN_API_KEY is missing/empty in .env" "Run the installer again or set it manually."
    fi
else
    fail ".env not found at $ENV_FILE" "Run install.sh to create it."
fi

# Container
if docker inspect domain-monitor-server >/dev/null 2>&1; then
    running="$(docker inspect --format '{{.State.Running}}' domain-monitor-server)"
    if [ "$running" = "true" ]; then
        ok "Container is running"
        health="$(docker inspect --format '{{.State.Health.Status}}' domain-monitor-server 2>/dev/null || echo none)"
        if [ "$health" = "healthy" ] || [ "$health" = "none" ]; then
            ok "Container healthcheck: $health"
        else
            fail "Container healthcheck: $health" "Check logs: domain-monitor-server logs"
        fi
    else
        fail "Container exists but is not running" "domain-monitor-server start"
    fi
else
    fail "Container 'domain-monitor-server' does not exist" "Run install.sh."
fi

# HTTP reachability
PORT="$(grep -oP '^PORT=\K.*' "$ENV_FILE" 2>/dev/null || echo 8000)"
if curl -fsS -m 5 "http://localhost:${PORT}/healthz" >/dev/null 2>&1; then
    ok "HTTP /healthz responds on port $PORT"
else
    fail "HTTP /healthz did not respond on port $PORT" "Container may still be starting, or the port is blocked."
fi

# Database
DB_FILE="$PROJECT_DIR/data/domain_monitor.db"
if [ -f "$DB_FILE" ]; then
    if command -v sqlite3 >/dev/null 2>&1; then
        if sqlite3 "$DB_FILE" "PRAGMA integrity_check;" 2>/dev/null | grep -q "^ok$"; then
            ok "Database integrity check passed"
        else
            fail "Database integrity check failed" "Consider restoring from a backup: domain-monitor-server restore"
        fi
        applied="$(sqlite3 "$DB_FILE" "SELECT COUNT(*) FROM schema_migrations;" 2>/dev/null || echo 0)"
        ok "Migrations applied: $applied"
    else
        ok "Database file exists (sqlite3 CLI not installed on host, skipped integrity check)"
    fi
else
    fail "Database file not found at $DB_FILE" "It's created on first start; check container logs if it's missing after a while."
fi

# Telegram - the only management interface, so this is a hard requirement.
if grep -q '^BOT_TOKEN=.\+' "$ENV_FILE" 2>/dev/null; then
    BOT_TOKEN="$(grep -oP '^BOT_TOKEN=\K.*' "$ENV_FILE")"
    PROXY="$(grep -oP '^TELEGRAM_PROXY=\K.*' "$ENV_FILE" 2>/dev/null || true)"
    CURL_PROXY_ARG=()
    [ -n "$PROXY" ] && CURL_PROXY_ARG=(-x "$PROXY")
    if curl -fsS -m 8 "${CURL_PROXY_ARG[@]}" "https://api.telegram.org/bot${BOT_TOKEN}/getMe" | grep -q '"ok":true'; then
        ok "Telegram API reachable, bot token valid"
    else
        fail "Telegram API not reachable or token invalid" \
            "Check BOT_TOKEN, and TELEGRAM_PROXY if Telegram is blocked on this network. Also verify the container itself can reach it: docker logs domain-monitor-server"
    fi
else
    fail "BOT_TOKEN not set in .env" "Telegram is the only management interface - run install.sh again or set BOT_TOKEN/ADMIN_ID by hand."
fi

# Disk space
AVAIL_KB="$(df -Pk "$PROJECT_DIR" | awk 'NR==2 {print $4}')"
AVAIL_MB=$((AVAIL_KB / 1024))
if [ "$AVAIL_MB" -ge 500 ]; then
    ok "Disk space: ${AVAIL_MB}MB free"
else
    fail "Low disk space: ${AVAIL_MB}MB free" "Free up space or move ./data to a larger volume."
fi

echo
if [ "$FAILED" -eq 0 ]; then
    echo "All checks passed."
else
    echo "Some checks failed - see recommendations above."
fi
exit "$FAILED"
