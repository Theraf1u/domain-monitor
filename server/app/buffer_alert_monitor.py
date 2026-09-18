"""Background task that watches each node's buffer fill level and fires
exactly one notification per threshold crossing (spec section 12) -
structurally the same pattern as NodeHealthMonitor: a periodic tick,
compare against the last recorded level, notify only on change.
"""
from __future__ import annotations

import asyncio
import logging

from app import runtime_settings
from app.database import Database
from app.notifier import Notifier

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 60


def _level_for(buffer_bytes: int | None, buffer_limit_bytes: int | None, warning_pct: int, critical_pct: int) -> str | None:
    """None when the fill level can't be computed at all (agent hasn't
    reported buffer_limit_bytes yet - an old agent, or one that hasn't
    sent its first heartbeat) - never guess a percentage from a missing
    limit (spec 12: "если buffer_limit неизвестен - проценты не
    показывать"), and never treat "unknown" as "critical"."""
    if buffer_bytes is None or not buffer_limit_bytes:
        return None
    pct = 100 * buffer_bytes / buffer_limit_bytes
    if pct >= critical_pct:
        return "critical"
    if pct >= warning_pct:
        return "warning"
    return "ok"


class BufferAlertMonitor:
    def __init__(self, db: Database, notifier: Notifier) -> None:
        self.db = db
        self.notifier = notifier
        self._stopped = asyncio.Event()

    async def run(self) -> None:
        while not self._stopped.is_set():
            try:
                await self._check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Buffer alert check failed")
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=_CHECK_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _check_once(self) -> None:
        warning_pct = await asyncio.to_thread(runtime_settings.get_buffer_warning_pct, self.db)
        critical_pct = await asyncio.to_thread(runtime_settings.get_buffer_critical_pct, self.db)
        nodes = await asyncio.to_thread(self.db.list_nodes)
        for node in nodes:
            if node.status != "active":
                continue
            level = _level_for(node.buffer_bytes, node.buffer_limit_bytes, warning_pct, critical_pct)
            if level is None:
                continue  # unknown limit - nothing to alert on for this node yet
            previous = node.buffer_alert_level
            if level == "ok":
                level = None  # stored as NULL, not the string "ok"
            if level == previous:
                continue
            await asyncio.to_thread(self.db.set_node_buffer_alert_level, node.id, level)
            pct = round(100 * node.buffer_bytes / node.buffer_limit_bytes)
            if level == "critical":
                text = f"📦 Буфер ноды <b>{node.name}</b> критически заполнен: {pct}%"
                if self.notifier.should_deliver("agent_buffer_critical"):
                    await self.notifier.deliver_for_node(node, text)
            elif level == "warning":
                text = f"📦 Буфер ноды <b>{node.name}</b> заполняется: {pct}%"
                if self.notifier.should_deliver("agent_buffer_warning"):
                    await self.notifier.deliver_for_node(node, text)
            elif previous is not None:
                # Dropped back below the warning threshold - recovery, on
                # whichever type it was actually alerting under.
                text = f"📦 Буфер ноды <b>{node.name}</b> снова в норме ({pct}%)"
                event_type = "agent_buffer_critical" if previous == "critical" else "agent_buffer_warning"
                if self.notifier.should_deliver(event_type):
                    await self.notifier.deliver_for_node(node, text)

    def stop(self) -> None:
        self._stopped.set()
