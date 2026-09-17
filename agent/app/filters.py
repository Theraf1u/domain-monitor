"""Domain normalization. Identical rules to the server's copy
(app/filters.py in domain-monitor-server) - a domain the agent buffers as
valid is never rejected by the server's own validation."""
from __future__ import annotations

import re

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)"
    r"(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.[A-Za-z]{2,63}$"
)


def normalize_domain(raw: str) -> str | None:
    if not raw:
        return None
    value = raw.strip().strip(".").lower()
    if not value:
        return None
    if "," in value or " " in value:
        return None
    if not _HOSTNAME_RE.match(value):
        return None
    return value
