"""Persistent runtime override for the agent's server URL (spec 2.0 Part
2, section 3.4): migration must never edit the host's .env from inside
the container (the container doesn't even have it mounted - see
docker-compose.yml), so a successful migration switchover is recorded
here instead, in the one directory the agent already persists across
restarts (/data, the same volume as its buffer DB).

    effective_server_url = runtime override OR SERVER_URL from env

An accepted automated migration switchover (app/uplink.py) writes this
file via set_override(). The manual CLI (`domain-monitor-agent
set-server <url>`, agent/scripts/set_server.sh) runs on the host, so it
edits .env directly and just deletes this file outright to make sure a
manual override always wins over a stale automated one.
"""
from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

_FILENAME = "runtime.json"


def _path(data_dir: str) -> str:
    return os.path.join(data_dir, _FILENAME)


def load_override(data_dir: str) -> str | None:
    """None means "no override" - the honest default, not a fabricated
    URL. A corrupt/unreadable file is treated the same way (logged, not
    raised) so a damaged runtime.json can never crash the agent or silently
    strand it on a URL nobody chose."""
    try:
        with open(_path(data_dir), encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("runtime.json exists but couldn't be read - ignoring override", exc_info=True)
        return None
    value = data.get("server_url_override")
    return value if isinstance(value, str) and value else None


def set_override(data_dir: str, server_url: str | None) -> None:
    """server_url=None clears the override (falls back to SERVER_URL from
    env again) - used by `set-server --clear` and by a failed migration
    switchover that never persisted one in the first place."""
    path = _path(data_dir)
    if server_url is None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"server_url_override": server_url}, fh)
    os.chmod(tmp, 0o600)  # not a secret, but spec 9 lists runtime config among files to lock down
    os.replace(tmp, path)  # atomic - a reader never sees a half-written file


def effective_server_url(configured_url: str, data_dir: str) -> str:
    override = load_override(data_dir)
    return override.rstrip("/") if override else configured_url
