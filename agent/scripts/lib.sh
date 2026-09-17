#!/usr/bin/env bash
# Shared helpers, sourced by every other script in scripts/ and bin/.
# `compose()` hides the docker-compose-v1-vs-docker-compose-v2-plugin
# split: some hosts only have the standalone v1 binary.
set -uo pipefail

have_compose() {
    if docker compose version >/dev/null 2>&1; then
        echo "plugin"
    elif command -v docker-compose >/dev/null 2>&1; then
        echo "standalone"
    else
        echo "none"
    fi
}

compose() {
    case "$(have_compose)" in
        plugin) docker compose "$@" ;;
        standalone) docker-compose "$@" ;;
        *) echo "Docker Compose not found (neither 'docker compose' nor 'docker-compose')." >&2; exit 1 ;;
    esac
}
