#!/usr/bin/env bash
# Diagnostics for the agent. Exits 0 if everything checked out, 1 otherwise.
set -uo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

FAILED=0

ok()   { echo "✓ $1"; }
fail() { echo "✗ $1"; [ -n "${2:-}" ] && echo "  -> $2"; FAILED=1; }

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    ok "Docker is installed and running"
else
    fail "Docker is not available/running" "Install Docker or start the Docker daemon."
fi

if [ "$(have_compose)" != "none" ]; then
    ok "Docker Compose is available"
else
    fail "Docker Compose not found" "Install the 'docker compose' plugin or 'docker-compose'."
fi

if [ -f "$ENV_FILE" ]; then
    ok ".env exists"
    grep -q '^SERVER_URL=https\?://.\+' "$ENV_FILE" && ok "SERVER_URL is set" || fail "SERVER_URL missing/invalid in .env" "Run install-agent.sh again."
    grep -q '^NODE_TOKEN=nmt_.\+' "$ENV_FILE" && ok "NODE_TOKEN is set" || fail "NODE_TOKEN missing/invalid in .env" "Get a token from the Server (Add Node) and set it in .env."
else
    fail ".env not found at $ENV_FILE" "Run install-agent.sh to create it."
fi

if docker inspect domain-monitor-agent >/dev/null 2>&1; then
    running="$(docker inspect --format '{{.State.Running}}' domain-monitor-agent)"
    if [ "$running" = "true" ]; then
        ok "Container is running"
        health="$(docker inspect --format '{{.State.Health.Status}}' domain-monitor-agent 2>/dev/null || echo none)"
        [ "$health" = "healthy" ] || [ "$health" = "none" ] && ok "Container healthcheck: $health" || fail "Container healthcheck: $health" "Check logs: domain-monitor-agent logs"

        if docker exec domain-monitor-agent tshark -v >/dev/null 2>&1; then
            ok "tshark is present inside the container"
        else
            fail "tshark not found/working inside the container" "Image may be out of date; try: domain-monitor-agent update"
        fi

        caps="$(docker inspect --format '{{.HostConfig.CapAdd}}' domain-monitor-agent 2>/dev/null)"
        if echo "$caps" | grep -q NET_ADMIN && echo "$caps" | grep -q NET_RAW; then
            ok "Capture capabilities (NET_ADMIN, NET_RAW) are set"
        else
            fail "Missing NET_ADMIN/NET_RAW capabilities" "Recreate the container via docker-compose.yml (do not run with 'docker run' manually)."
        fi
    else
        fail "Container exists but is not running" "domain-monitor-agent start"
    fi
else
    fail "Container 'domain-monitor-agent' does not exist" "Run install-agent.sh."
fi

if [ -f "$ENV_FILE" ] && grep -q '^SERVER_URL=' "$ENV_FILE"; then
    SERVER_URL="$(grep -oP '^SERVER_URL=\K.*' "$ENV_FILE")"
    if curl -fsS -m 5 "${SERVER_URL}/healthz" >/dev/null 2>&1; then
        ok "Server reachable at $SERVER_URL"
    else
        fail "Server not reachable at $SERVER_URL" "Check network/firewall between this node and the server."
    fi
fi

DB_FILE="$PROJECT_DIR/data/agent_buffer.db"
if [ -f "$DB_FILE" ]; then
    if command -v sqlite3 >/dev/null 2>&1; then
        if sqlite3 "$DB_FILE" "PRAGMA integrity_check;" 2>/dev/null | grep -q "^ok$"; then
            ok "Local buffer database integrity check passed"
        else
            fail "Local buffer database integrity check failed" "Safe to delete ./data/agent_buffer.db - it's just a retry buffer, not the source of truth."
        fi
    else
        ok "Local buffer database file exists (sqlite3 CLI not installed on host, skipped integrity check)"
    fi
else
    ok "Local buffer database not created yet (agent may have just started)"
fi

AVAIL_KB="$(df -Pk "$PROJECT_DIR" | awk 'NR==2 {print $4}')"
AVAIL_MB=$((AVAIL_KB / 1024))
if [ "$AVAIL_MB" -ge 200 ]; then
    ok "Disk space: ${AVAIL_MB}MB free"
else
    fail "Low disk space: ${AVAIL_MB}MB free" "Free up space - a full disk can stall the local event buffer."
fi

echo
if [ "$FAILED" -eq 0 ]; then
    echo "All checks passed."
else
    echo "Some checks failed - see recommendations above."
fi
exit "$FAILED"
