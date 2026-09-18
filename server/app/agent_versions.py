"""Agent version comparison - a display concern only. Nothing here ever
executes remote code or triggers an update by itself (the 2.0 spec is
explicit: no remote shell, no arbitrary command execution via heartbeat).
When a new agent build ships, bump RECOMMENDED_AGENT_VERSION here to
match agent/app/uplink.py's AGENT_VERSION - there's no automated release
pipeline linking the two, so this is a manual sync point by design.
"""
from __future__ import annotations

RECOMMENDED_AGENT_VERSION = "1.0.0"


def parse_version(v: str) -> tuple[int, ...] | None:
    """"1.2.3" -> (1, 2, 3). None for anything that isn't dotted
    integers, so a malformed or custom version string is treated as
    "can't compare", never crashes the caller."""
    try:
        return tuple(int(p) for p in v.strip().split("."))
    except (ValueError, AttributeError):
        return None


def version_badge(current: str | None) -> str | None:
    """None when there's nothing useful to say (no version reported, or
    it doesn't parse as dotted integers) - never a fabricated status."""
    if not current:
        return None
    current_t = parse_version(current)
    recommended_t = parse_version(RECOMMENDED_AGENT_VERSION)
    if current_t is None or recommended_t is None:
        return None
    if current_t < recommended_t:
        return f"🟡 Agent v{current} → доступна v{RECOMMENDED_AGENT_VERSION}"
    if current_t > recommended_t:
        return f"🔵 Agent v{current} (новее рекомендованной v{RECOMMENDED_AGENT_VERSION})"
    return f"🟢 Agent v{current} (актуальная)"
