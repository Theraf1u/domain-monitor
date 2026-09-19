"""Docker Engine status for the "🖥 Сервер"/"🩺 Диагностика" Settings
screens (spec 2.0 Part 2, section 1.1). Talks to the Docker Engine API
directly over the Unix socket docker-compose.yml mounts in - no `docker`
CLI binary needed in the image.

SECURITY: this module only ever issues GET calls against read-only
endpoints (/version, /containers/json), but that is a self-imposed
discipline in THIS code, not something the socket mount enforces -
mounting docker.sock at all (even `:ro`) grants whatever reaches it the
same power as root on the host, full stop. See the long comment on the
volume mount in docker-compose.yml. This was an explicit, informed
risk-acceptance decision, not an oversight - if it's ever revisited, the
fix is deleting that volume line, not "hardening" this module further
(there's no meaningfully safer way to use a raw docker.sock mount).

Deliberately out of scope even with the socket mounted: Compose version
(Compose is a client-side CLI plugin, not part of the Docker Engine API -
there's nothing on the socket to ask) and UFW status (unrelated to
Docker entirely, needs host filesystem/netfilter access this container
still doesn't have). Both stay CLI-only via `doctor`.

An install that hasn't been recreated since this was added (the socket
mount is new in docker-compose.yml) simply won't have /var/run/docker.sock
inside the container - every function here degrades to returning None
rather than raising, so an un-updated container never breaks on this."""
from __future__ import annotations

import logging

import aiohttp

logger = logging.getLogger(__name__)

_SOCKET_PATH = "/var/run/docker.sock"
# Any fixed API version recent enough for /version and /containers/json;
# the daemon negotiates down to what it actually supports if this is
# newer than it, so this doesn't need to track the host's Docker version.
_BASE_URL = "http://docker/v1.41"


async def _get(path: str) -> dict | list | None:
    import os

    if not os.path.exists(_SOCKET_PATH):
        return None
    try:
        connector = aiohttp.UnixConnector(path=_SOCKET_PATH)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(f"{_BASE_URL}{path}", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
    except Exception:
        logger.debug("Docker socket query failed for %s", path, exc_info=True)
        return None


async def engine_version() -> str | None:
    data = await _get("/version")
    if not data:
        return None
    return data.get("Version")


async def container_summary() -> tuple[int, int] | None:
    """(running, total) across every container on the host, not just this
    project's own - an operator asking "is Docker healthy on this box"
    means the whole host, the same way `docker ps` would show it."""
    data = await _get("/containers/json?all=true")
    if data is None or not isinstance(data, list):
        return None
    running = sum(1 for c in data if c.get("State") == "running")
    return running, len(data)
